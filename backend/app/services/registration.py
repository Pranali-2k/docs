"""Patient registration: demographics (with encrypted identifiers), insurance policies,
guarantor, release of PHI and document/image storage. Every change is audited field by field."""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Guarantor, InsurancePolicy, Patient, PatientDocument, Payer, PhiRelease
from app.services import audit, crypto
from app.services.normalize import normalize_email, normalize_member_id, normalize_phone, parse_date

DOC_KINDS = {"photo_id", "insurance_card_front", "insurance_card_back", "referral", "other"}
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "application/pdf"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class RegistrationError(ValueError):
    pass


def next_mrn(db: Session) -> str:
    while True:
        mrn = f"DH{secrets.randbelow(10**6):06d}"
        if not db.scalar(select(Patient.id).where(Patient.mrn == mrn)):
            return mrn


def find_duplicates(db: Session, first: str, last: str, dob: date | None) -> list[Patient]:
    if not dob:
        return []
    return list(db.scalars(select(Patient).where(
        func.lower(Patient.first_name) == first.lower(),
        func.lower(Patient.last_name) == last.lower(),
        Patient.date_of_birth == dob,
    )))


DEMOGRAPHIC_FIELDS = {
    "prefix", "first_name", "middle_name", "last_name", "suffix", "preferred_name", "previous_name",
    "is_test_patient", "marital_status", "date_of_birth", "sex", "phone", "home_phone", "work_phone",
    "preferred_contact_method", "email", "address_line1", "address_line2", "city", "state", "postal_code",
    "preferred_provider_id", "notes", "drivers_license_state", "pcp_name", "pms_id", "portal_status",
}
PORTAL_STATUSES = {"inactive", "invited", "active"}
SENSITIVE_FIELDS = {"ssn", "drivers_license_number"}
MARITAL_STATUSES = {"single", "married", "divorced", "widowed", "separated", "domestic_partner", "unspecified"}
POLICY_TYPES = {"PPO", "HMO", "EPO", "POS", "HDHP", "Medicare", "Medicaid", "Other"}
COVERAGE_ORDERS = ("primary", "secondary", "tertiary")
POLICY_FIELDS = {
    "plan_name", "policy_type", "group_number", "claims_address", "notes", "requires_referral",
    "requires_precert_inpatient", "requires_preauth_outpatient", "relationship_to_subscriber", "subscriber_name",
    "holder_first_name", "holder_last_name", "holder_date_of_birth", "eligibility_status", "copay_amount",
    "coinsurance_pct", "deductible", "remaining_deductible", "out_of_pocket", "remaining_out_of_pocket",
    "effective_date", "end_date",
}


def _clean(field: str, value):
    """Normalize one demographic value; raises RegistrationError on invalid input."""
    if isinstance(value, str):
        value = value.strip() or None
    if value is None:
        return None
    if field in {"phone", "home_phone", "work_phone"}:
        norm = normalize_phone(value)
        if norm is None:
            raise RegistrationError(f"{field.replace('_', ' ').capitalize()} must be a 10-digit US number")
        return norm
    if field == "email":
        norm = normalize_email(value)
        if norm is None:
            raise RegistrationError("Email address is not valid")
        return norm
    if field in {"first_name", "last_name", "middle_name"}:
        return value.title() if value.islower() or value.isupper() else value
    if field == "portal_status":
        v = value.lower()
        if v not in PORTAL_STATUSES:
            raise RegistrationError(f"Portal status must be one of {sorted(PORTAL_STATUSES)}")
        return v
    if field == "marital_status":
        v = value.lower().replace(" ", "_")
        if v not in MARITAL_STATUSES:
            raise RegistrationError(f"Marital status must be one of {sorted(MARITAL_STATUSES)}")
        return v
    if field in {"state", "drivers_license_state"} and isinstance(value, str):
        return value.upper()[:2] if field == "drivers_license_state" else value
    if field == "date_of_birth":
        if isinstance(value, str):
            value = parse_date(value)
        if value and value > date.today():
            raise RegistrationError("Date of birth can't be in the future")
    return value


def _set_ssn(p: Patient, ssn: str | None) -> None:
    if not ssn:
        p.ssn_encrypted, p.ssn_last4 = None, None
        return
    digits = re.sub(r"\D", "", ssn)
    if len(digits) != 9 or digits.startswith(("000", "666", "9")) or digits[3:5] == "00" or digits[5:] == "0000":
        raise RegistrationError("Social Security Number must be a valid 9-digit SSN")
    try:
        p.ssn_encrypted = crypto.encrypt(digits)
    except crypto.CryptoError as exc:
        raise RegistrationError(f"Could not secure the SSN: {exc}") from exc
    p.ssn_last4 = digits[-4:]


def _set_license(p: Patient, number: str | None) -> None:
    if not number:
        p.drivers_license_encrypted, p.drivers_license_last4 = None, None
        return
    cleaned = re.sub(r"[^A-Za-z0-9]", "", number).upper()
    if not 4 <= len(cleaned) <= 20:
        raise RegistrationError("Driver's license number must be 4-20 letters or digits")
    try:
        p.drivers_license_encrypted = crypto.encrypt(cleaned)
    except crypto.CryptoError as exc:
        raise RegistrationError(f"Could not secure the license number: {exc}") from exc
    p.drivers_license_last4 = cleaned[-4:]


def create_patient(db: Session, data: dict, *, actor: str, allow_duplicate: bool = False) -> Patient:
    first, last = (data.get("first_name") or "").strip(), (data.get("last_name") or "").strip()
    if not first or not last:
        raise RegistrationError("First and last name are required")
    first, last = _clean("first_name", first), _clean("last_name", last)
    dob = _clean("date_of_birth", data.get("date_of_birth"))
    dupes = find_duplicates(db, first, last, dob)
    if dupes and not allow_duplicate:
        raise RegistrationError(f"Possible duplicate of MRN {dupes[0].mrn} (same name and DOB)")
    p = Patient(mrn=next_mrn(db), first_name=first, last_name=last, date_of_birth=dob, is_test_patient=False)
    for field in DEMOGRAPHIC_FIELDS - {"first_name", "last_name", "date_of_birth"}:
        if field in data and data[field] is not None:
            setattr(p, field, _clean(field, data[field]))
    _set_ssn(p, data.get("ssn"))
    _set_license(p, data.get("drivers_license_number"))
    db.add(p)
    db.flush()
    audit.record(db, "patient", p.id, "patient_registered", actor, mrn=p.mrn)
    return p


def update_patient(db: Session, p: Patient, changes: dict, *, actor: str) -> Patient:
    """Apply demographic changes and record a field-level amendment history entry."""
    changed: dict = {}
    for field, value in changes.items():
        if field in SENSITIVE_FIELDS:
            continue
        if field not in DEMOGRAPHIC_FIELDS:
            raise RegistrationError(f"Unknown field: {field}")
        value = _clean(field, value)
        if field in {"first_name", "last_name"} and not value:
            raise RegistrationError("First and last name are required")
        current = getattr(p, field)
        if current != value:
            changed[field] = {"from": None if current is None else str(current),
                              "to": None if value is None else str(value)}
            setattr(p, field, value)
    if "ssn" in changes:
        old = p.ssn_last4
        _set_ssn(p, changes["ssn"])
        if old != p.ssn_last4:
            changed["ssn"] = {"from": f"***-**-{old}" if old else None,
                              "to": f"***-**-{p.ssn_last4}" if p.ssn_last4 else None}
    if "drivers_license_number" in changes:
        old = p.drivers_license_last4
        _set_license(p, changes["drivers_license_number"])
        if old != p.drivers_license_last4:
            changed["drivers_license_number"] = {"from": f"****{old}" if old else None,
                                                 "to": f"****{p.drivers_license_last4}" if p.drivers_license_last4 else None}
    if changed:
        audit.record(db, "patient", p.id, "demographics_updated", actor, changes=changed)
    return p


def reveal_identifier(db: Session, p: Patient, which: str, *, actor: str, reason: str | None) -> str | None:
    """Return a decrypted SSN / license number. Every reveal is audited."""
    token = {"ssn": p.ssn_encrypted, "drivers_license_number": p.drivers_license_encrypted}.get(which)
    if which not in SENSITIVE_FIELDS:
        raise RegistrationError("Unknown identifier")
    try:
        value = crypto.decrypt(token)
    except crypto.CryptoError as exc:
        raise RegistrationError(str(exc)) from exc
    audit.record(db, "patient", p.id, "sensitive_identifier_viewed", actor, field=which, reason=reason)
    if which == "ssn" and value:
        return f"{value[:3]}-{value[3:5]}-{value[5:]}"
    return value


def _active_policy(p: Patient, order: str) -> InsurancePolicy | None:
    for pol in p.policies:
        if pol.is_active and (pol.coverage_order or ("primary" if pol.is_primary else "secondary")) == order:
            return pol
    return None


def add_policy(db: Session, p: Patient, data: dict, *, actor: str) -> InsurancePolicy:
    payer = db.get(Payer, data.get("payer_id"))
    if payer is None:
        raise RegistrationError("Unknown payer")
    member = normalize_member_id(data.get("member_id"))
    if not member:
        raise RegistrationError("Policy number (member ID) is required")
    order = data.get("coverage_order") or ("primary" if data.get("is_primary", True) else "secondary")
    if order not in COVERAGE_ORDERS:
        raise RegistrationError(f"coverage_order must be one of {COVERAGE_ORDERS}")
    existing = _active_policy(p, order)
    if existing is not None:
        archive_policy(db, existing, actor=actor, reason=f"replaced by new {order} policy")
    pol = InsurancePolicy(
        patient_id=p.id, payer_id=payer.id, member_id=member, coverage_order=order, is_primary=order == "primary",
        is_active=True, subscriber_name=data.get("subscriber_name") or p.full_name,
        relationship_to_subscriber=data.get("relationship_to_subscriber") or "self",
        requires_referral=False, requires_precert_inpatient=False, requires_preauth_outpatient=False,
    )
    _apply_policy_fields(pol, {k: v for k, v in data.items() if k in POLICY_FIELDS})
    db.add(pol)
    db.flush()
    db.refresh(p)
    audit.record(db, "patient", p.id, "insurance_added", actor, payer=payer.name, member_id=member, policy_id=pol.id,
                 coverage_order=order)
    audit.record(db, "policy", pol.id, "policy_created", actor, payer=payer.name, member_id=member)
    return pol


def _apply_policy_fields(pol: InsurancePolicy, data: dict) -> dict:
    changed: dict = {}
    for field, value in data.items():
        if field not in POLICY_FIELDS:
            raise RegistrationError(f"Unknown policy field: {field}")
        if isinstance(value, str):
            value = value.strip() or None
        if field == "policy_type" and value and value not in POLICY_TYPES:
            raise RegistrationError(f"Policy type must be one of {sorted(POLICY_TYPES)}")
        if field == "notes" and value and len(value) > 500:
            raise RegistrationError("Notes are limited to 500 characters")
        if field == "coinsurance_pct" and value is not None and not 0 <= float(value) <= 100:
            raise RegistrationError("Co-insurance must be between 0 and 100%")
        if field in {"copay_amount", "deductible", "remaining_deductible", "out_of_pocket",
                     "remaining_out_of_pocket"} and value is not None and float(value) < 0:
            raise RegistrationError(f"{field.replace('_', ' ').capitalize()} can't be negative")
        if field in {"effective_date", "end_date", "holder_date_of_birth"} and isinstance(value, str):
            value = parse_date(value)
        if field == "eligibility_status" and value and value not in {"active", "inactive", "unknown"}:
            raise RegistrationError("Eligibility status must be active, inactive or unknown")
        current = getattr(pol, field)
        if (None if current is None else str(current)) != (None if value is None else str(value)):
            changed[field] = {"from": None if current is None else str(current),
                              "to": None if value is None else str(value)}
            setattr(pol, field, value)
    if pol.effective_date and pol.end_date and pol.end_date < pol.effective_date:
        raise RegistrationError("Policy end date is before the effective date")
    return changed


def update_policy(db: Session, pol: InsurancePolicy, data: dict, *, actor: str) -> InsurancePolicy:
    if not pol.is_active:
        raise RegistrationError("This policy is archived; create a new policy instead")
    changes = dict(data)
    if "payer_id" in changes:
        payer = db.get(Payer, changes.pop("payer_id"))
        if payer is None:
            raise RegistrationError("Unknown payer")
        if payer.id != pol.payer_id:
            changes_payer = {"payer": {"from": pol.payer.name, "to": payer.name}}
            pol.payer_id, pol.payer = payer.id, payer
        else:
            changes_payer = {}
    else:
        changes_payer = {}
    if "member_id" in changes:
        member = normalize_member_id(changes.pop("member_id"))
        if not member:
            raise RegistrationError("Policy number is required")
        if member != pol.member_id:
            changes_payer["member_id"] = {"from": pol.member_id, "to": member}
            pol.member_id = member
    changed = {**changes_payer, **_apply_policy_fields(pol, changes)}
    if changed:
        audit.record(db, "policy", pol.id, "policy_updated", actor, changes=changed)
        audit.record(db, "patient", pol.patient_id, "insurance_updated", actor, policy_id=pol.id, changes=changed)
    return pol


def archive_policy(db: Session, pol: InsurancePolicy, *, actor: str, reason: str | None = None) -> InsurancePolicy:
    if not pol.is_active:
        raise RegistrationError("Policy is already archived")
    pol.is_active = False
    pol.is_primary = False
    pol.archived_at = datetime.now(timezone.utc)
    pol.archived_by = actor
    audit.record(db, "policy", pol.id, "policy_archived", actor, reason=reason)
    audit.record(db, "patient", pol.patient_id, "insurance_archived", actor, policy_id=pol.id, reason=reason)
    return pol


def upsert_guarantor(db: Session, p: Patient, data: dict, *, actor: str) -> Guarantor:
    g = p.guarantor or Guarantor(patient_id=p.id)
    same = bool(data.get("same_as_patient", True))
    fields = ["relationship_to_patient", "first_name", "last_name", "date_of_birth", "phone", "email",
              "address_line1", "city", "state", "postal_code"]
    before = {f: getattr(g, f) for f in fields + ["same_as_patient"]}
    g.same_as_patient = same
    if same:
        g.relationship_to_patient = "self"
        for f in fields[1:]:
            setattr(g, f, None)
    else:
        if not (data.get("first_name") and data.get("last_name")):
            raise RegistrationError("Guarantor first and last name are required")
        for f in fields:
            v = data.get(f)
            if f == "phone" and v:
                v = normalize_phone(v)
                if v is None:
                    raise RegistrationError("Guarantor phone must be a 10-digit US number")
            if f == "email" and v:
                v = normalize_email(v)
                if v is None:
                    raise RegistrationError("Guarantor email is not valid")
            if f == "date_of_birth" and isinstance(v, str):
                v = parse_date(v)
            setattr(g, f, v or None)
    if g.id is None:
        db.add(g)
    db.flush()
    after = {f: getattr(g, f) for f in fields + ["same_as_patient"]}
    diff = {k: {"from": str(before[k]), "to": str(after[k])} for k in after if before[k] != after[k]}
    if diff:
        audit.record(db, "patient", p.id, "guarantor_updated", actor, changes=diff)
    return g


def add_phi_release(db: Session, p: Patient, data: dict, *, actor: str) -> PhiRelease:
    name = (data.get("recipient_name") or "").strip()
    if not name:
        raise RegistrationError("Recipient name is required")
    phone = data.get("phone")
    if phone:
        phone = normalize_phone(phone)
        if phone is None:
            raise RegistrationError("Phone must be a 10-digit US number")
    signed, expires = data.get("signed_on"), data.get("expires_on")
    signed = parse_date(signed) if isinstance(signed, str) else signed
    expires = parse_date(expires) if isinstance(expires, str) else expires
    if signed and expires and expires < signed:
        raise RegistrationError("Expiry date is before the signed date")
    rel = PhiRelease(
        patient_id=p.id, recipient_name=name, relationship_to_patient=data.get("relationship_to_patient"), phone=phone,
        may_discuss_appointments=bool(data.get("may_discuss_appointments", True)),
        may_discuss_billing=bool(data.get("may_discuss_billing", False)),
        may_discuss_clinical=bool(data.get("may_discuss_clinical", False)),
        signed_on=signed, expires_on=expires, created_by=actor,
    )
    db.add(rel)
    db.flush()
    audit.record(db, "patient", p.id, "phi_release_added", actor, release_id=rel.id, recipient=name)
    return rel


def revoke_phi_release(db: Session, rel: PhiRelease, *, actor: str) -> PhiRelease:
    if rel.revoked_at:
        raise RegistrationError("Release is already revoked")
    rel.revoked_at = datetime.now(timezone.utc)
    audit.record(db, "patient", rel.patient_id, "phi_release_revoked", actor, release_id=rel.id,
                 recipient=rel.recipient_name)
    return rel


def _matches_type(content: bytes, content_type: str) -> bool:
    """Magic-byte check so a renamed or corrupt file can't be stored as an image/PDF."""
    head = content[:16]
    if content_type == "application/pdf":
        return head.startswith(b"%PDF-")
    if content_type == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if content_type == "image/heic":
        return head[4:8] == b"ftyp"
    return False


def store_document(
    db: Session, p: Patient, *, kind: str, filename: str, content_type: str, content: bytes, actor: str
) -> PatientDocument:
    if kind not in DOC_KINDS:
        raise RegistrationError(f"kind must be one of {sorted(DOC_KINDS)}")
    if content_type not in ALLOWED_TYPES:
        raise RegistrationError("Only JPEG, PNG, WEBP, HEIC or PDF files are accepted")
    if len(content) > MAX_UPLOAD_BYTES:
        raise RegistrationError("File exceeds 10 MB")
    if not content:
        raise RegistrationError("The file is empty")
    if not _matches_type(content, content_type):
        raise RegistrationError("The file's contents don't match its type; it may be damaged or renamed")
    suffix = Path(filename).suffix.lower()[:8]
    try:
        folder = Path(get_settings().upload_dir) / str(p.id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{kind}-{uuid.uuid4().hex}{suffix}"
        path.write_bytes(content)
    except OSError as exc:
        raise RegistrationError(f"Could not store the file: {exc.strerror or exc}") from exc
    doc = PatientDocument(
        patient_id=p.id, kind=kind, filename=Path(filename).name[:255], content_type=content_type,
        storage_path=str(path), size_bytes=len(content), uploaded_by=actor,
    )
    db.add(doc)
    db.flush()
    audit.record(db, "patient", p.id, "document_uploaded", actor, kind=kind, document_id=doc.id)
    return doc
