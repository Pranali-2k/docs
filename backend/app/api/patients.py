"""Patient chart API: patient data (with protected identifiers), contact info, guarantor,
release of PHI, insurance policies, eligibility (270/271) and documents.

Domain errors (RegistrationError, EligibilityError) become 4xx responses here; anything
unexpected is caught by the global handlers in app.main (logged, 500 with a reference).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.db import get_db
from app.models import (
    Appointment,
    AuditLog,
    EligibilityBenefit,
    EligibilityCheck,
    InsurancePolicy,
    Patient,
    PatientDocument,
    PhiRelease,
    PortalRequest,
    Provider,
    User,
)
from app.security import current_user
from app.services import audit_text, eligibility, registration
from app.services.normalize import digits

router = APIRouter(prefix="/api/patients", tags=["patients"])
log = logging.getLogger("docshealth.api.patients")


# --------------------------------------------------------------------------- helpers

def _get(db: Session, patient_id: int) -> Patient:
    p = db.get(Patient, patient_id)
    if p is None:
        raise HTTPException(404, "Patient not found")
    return p


def _get_policy(db: Session, p: Patient, policy_id: int) -> InsurancePolicy:
    pol = db.get(InsurancePolicy, policy_id)
    if pol is None or pol.patient_id != p.id:
        raise HTTPException(404, "Policy not found for this patient")
    return pol


def _fail(db: Session, exc: Exception, status: int = 422):
    db.rollback()
    raise HTTPException(status, str(exc)) from None


def chart(db: Session, p: Patient) -> dict:
    db.refresh(p)
    appts = db.scalars(select(Appointment).where(Appointment.patient_id == p.id).order_by(Appointment.start_at.desc()))
    checks = db.scalars(select(EligibilityCheck).where(EligibilityCheck.patient_id == p.id)
                        .order_by(EligibilityCheck.checked_at.desc()).limit(20))
    reqs = db.scalars(select(PortalRequest).where(PortalRequest.patient_id == p.id)
                      .order_by(PortalRequest.created_at.desc()).limit(20))
    events = db.scalars(select(AuditLog).where(AuditLog.entity_type == "patient", AuditLog.entity_id == p.id)
                        .order_by(AuditLog.id.desc()).limit(100))
    order = {"primary": 0, "secondary": 1, "tertiary": 2}
    return {
        **S.patient_summary(p),
        **S.demographics(p),
        "preferred_provider_id": p.preferred_provider_id,
        "notes": p.notes,
        "created_at": S.iso(p.created_at),
        "policies": [S.policy(x) for x in sorted(
            p.policies, key=lambda x: (not x.is_active, order.get(x.coverage_order or "primary", 3), -x.id))],
        "guarantor": S.guarantor(p.guarantor),
        "phi_releases": [S.phi_release(r) for r in sorted(p.phi_releases, key=lambda r: -r.id)],
        "documents": [S.document(d) for d in p.documents],
        "appointments": [S.appointment(a) for a in appts],
        "eligibility_checks": [S.eligibility(c) for c in checks],
        "requests": [S.request_row(r) for r in reqs],
        "history": audit_text.annotate(db, list(events)),
    }


# --------------------------------------------------------------------------- search & registration

@router.get("")
def search(q: str | None = None, limit: int = 50, db: Session = Depends(get_db), _: User = Depends(current_user)):
    stmt = select(Patient)
    if q:
        like = f"%{q.strip().lower()}%"
        conds = [Patient.first_name.ilike(like), Patient.last_name.ilike(like), Patient.mrn.ilike(like),
                 Patient.email.ilike(like), Patient.preferred_name.ilike(like)]
        if len(digits(q)) >= 4:
            conds.append(Patient.phone.like(f"%{digits(q)}%"))
            conds.append(Patient.id.in_(select(InsurancePolicy.patient_id)
                                        .where(InsurancePolicy.member_id.ilike(like))))
        parts = q.split()
        if len(parts) == 2:
            conds.append((Patient.first_name.ilike(f"{parts[0]}%")) & (Patient.last_name.ilike(f"{parts[1]}%")))
        stmt = stmt.where(or_(*conds))
    rows = db.scalars(stmt.order_by(Patient.last_name, Patient.first_name).limit(min(limit, 200)))
    return [S.patient_summary(p) for p in rows]


class PolicyIn(BaseModel):
    payer_id: int
    member_id: str = Field(min_length=1, max_length=60)
    coverage_order: str | None = None
    group_number: str | None = None
    plan_name: str | None = None
    policy_type: str | None = None
    claims_address: str | None = None
    notes: str | None = Field(default=None, max_length=500)
    subscriber_name: str | None = None
    relationship_to_subscriber: str = "self"
    holder_first_name: str | None = None
    holder_last_name: str | None = None
    holder_date_of_birth: date | None = None
    requires_referral: bool | None = None
    requires_precert_inpatient: bool | None = None
    requires_preauth_outpatient: bool | None = None
    effective_date: date | None = None
    end_date: date | None = None
    is_primary: bool = True


class DemographicsIn(BaseModel):
    prefix: str | None = Field(default=None, max_length=20)
    first_name: str | None = Field(default=None, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    suffix: str | None = Field(default=None, max_length=20)
    preferred_name: str | None = Field(default=None, max_length=100)
    previous_name: str | None = Field(default=None, max_length=200)
    is_test_patient: bool | None = None
    marital_status: str | None = None
    date_of_birth: date | None = None
    sex: str | None = None
    ssn: str | None = Field(default=None, max_length=11)
    drivers_license_number: str | None = Field(default=None, max_length=25)
    drivers_license_state: str | None = Field(default=None, max_length=2)
    phone: str | None = None
    home_phone: str | None = None
    work_phone: str | None = None
    preferred_contact_method: str | None = None
    email: EmailStr | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    preferred_provider_id: int | None = None
    notes: str | None = None


class PatientIn(DemographicsIn):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    date_of_birth: date
    insurance: PolicyIn | None = None
    allow_duplicate: bool = False


@router.post("", status_code=201)
def register(body: PatientIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    data = body.model_dump(exclude={"insurance", "allow_duplicate"})
    try:
        p = registration.create_patient(db, data, actor=user.email, allow_duplicate=body.allow_duplicate)
        if body.insurance:
            registration.add_policy(db, p, body.insurance.model_dump(exclude_none=True), actor=user.email)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc, 409 if "duplicate" in str(exc).lower() else 422)
    return chart(db, p)


@router.get("/{patient_id}")
def get_chart(patient_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return chart(db, _get(db, patient_id))


@router.patch("/{patient_id}")
def update(patient_id: int, body: DemographicsIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = _get(db, patient_id)
    try:
        registration.update_patient(db, p, body.model_dump(exclude_unset=True), actor=user.email)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc)
    return chart(db, p)


class RevealIn(BaseModel):
    field: str
    reason: str | None = Field(default=None, max_length=200)


@router.post("/{patient_id}/reveal")
def reveal(patient_id: int, body: RevealIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Show a protected identifier (eye icon). Every reveal is written to the audit trail."""
    p = _get(db, patient_id)
    try:
        value = registration.reveal_identifier(db, p, body.field, actor=user.email, reason=body.reason)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc, 400)
    return {"field": body.field, "value": value}


@router.get("/{patient_id}/history")
def amendments(patient_id: int, entity: str = "patient", db: Session = Depends(get_db),
               _: User = Depends(current_user)):
    """'Show Amendments History' / 'View History': field-level changes to this chart."""
    p = _get(db, patient_id)
    policy_ids = [x.id for x in p.policies]
    stmt = select(AuditLog)
    if entity == "policy":
        stmt = stmt.where(AuditLog.entity_type == "policy", AuditLog.entity_id.in_(policy_ids or [-1]))
    else:
        stmt = stmt.where(AuditLog.entity_type == "patient", AuditLog.entity_id == p.id)
    return audit_text.annotate(db, list(db.scalars(stmt.order_by(AuditLog.id.desc()).limit(300))))


# --------------------------------------------------------------------------- guarantor & PHI release

class GuarantorIn(BaseModel):
    same_as_patient: bool = True
    relationship_to_patient: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    phone: str | None = None
    email: str | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


@router.put("/{patient_id}/guarantor")
def put_guarantor(patient_id: int, body: GuarantorIn, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    p = _get(db, patient_id)
    try:
        registration.upsert_guarantor(db, p, body.model_dump(), actor=user.email)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc)
    return chart(db, p)


class PhiReleaseIn(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=200)
    relationship_to_patient: str | None = None
    phone: str | None = None
    may_discuss_appointments: bool = True
    may_discuss_billing: bool = False
    may_discuss_clinical: bool = False
    signed_on: date | None = None
    expires_on: date | None = None


@router.post("/{patient_id}/phi-releases", status_code=201)
def add_release(patient_id: int, body: PhiReleaseIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    p = _get(db, patient_id)
    try:
        registration.add_phi_release(db, p, body.model_dump(), actor=user.email)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc)
    return chart(db, p)


@router.post("/{patient_id}/phi-releases/{release_id}/revoke")
def revoke_release(patient_id: int, release_id: int, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    p = _get(db, patient_id)
    rel = db.get(PhiRelease, release_id)
    if rel is None or rel.patient_id != p.id:
        raise HTTPException(404, "Release not found")
    try:
        registration.revoke_phi_release(db, rel, actor=user.email)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc, 409)
    return chart(db, p)


# --------------------------------------------------------------------------- insurance

@router.post("/{patient_id}/insurance", status_code=201)
def add_insurance(patient_id: int, body: PolicyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = _get(db, patient_id)
    try:
        registration.add_policy(db, p, body.model_dump(exclude_none=True), actor=user.email)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc)
    return chart(db, p)


def _policy_detail(db: Session, p: Patient, pol: InsurancePolicy) -> dict:
    checks = db.scalars(select(EligibilityCheck).where(EligibilityCheck.policy_id == pol.id)
                        .order_by(EligibilityCheck.checked_at.desc()).limit(25)).all()
    return {**S.policy(pol), "patient": S.patient_summary(p),
            "eligibility_checks": [S.eligibility(c) for c in checks]}


@router.get("/{patient_id}/policies/{policy_id}")
def get_policy(patient_id: int, policy_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    p = _get(db, patient_id)
    return _policy_detail(db, p, _get_policy(db, p, policy_id))


class PolicyPatch(BaseModel):
    payer_id: int | None = None
    member_id: str | None = None
    group_number: str | None = None
    plan_name: str | None = None
    policy_type: str | None = None
    claims_address: str | None = None
    notes: str | None = Field(default=None, max_length=500)
    subscriber_name: str | None = None
    relationship_to_subscriber: str | None = None
    holder_first_name: str | None = None
    holder_last_name: str | None = None
    holder_date_of_birth: date | None = None
    requires_referral: bool | None = None
    requires_precert_inpatient: bool | None = None
    requires_preauth_outpatient: bool | None = None
    eligibility_status: str | None = None
    copay_amount: float | None = None
    coinsurance_pct: float | None = None
    deductible: float | None = None
    remaining_deductible: float | None = None
    out_of_pocket: float | None = None
    remaining_out_of_pocket: float | None = None
    effective_date: date | None = None
    end_date: date | None = None


@router.patch("/{patient_id}/policies/{policy_id}")
def patch_policy(patient_id: int, policy_id: int, body: PolicyPatch, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    p = _get(db, patient_id)
    pol = _get_policy(db, p, policy_id)
    try:
        registration.update_policy(db, pol, body.model_dump(exclude_unset=True), actor=user.email)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc)
    return _policy_detail(db, p, pol)


class ArchiveIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


@router.post("/{patient_id}/policies/{policy_id}/archive")
def archive(patient_id: int, policy_id: int, body: ArchiveIn, db: Session = Depends(get_db),
            user: User = Depends(current_user)):
    p = _get(db, patient_id)
    pol = _get_policy(db, p, policy_id)
    try:
        registration.archive_policy(db, pol, actor=user.email, reason=body.reason)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc, 409)
    return chart(db, p)


@router.post("/{patient_id}/policies/{policy_id}/replace", status_code=201)
def archive_and_create(patient_id: int, policy_id: int, body: PolicyIn, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    """'Archive & Create New Primary': the old policy is kept (archived) for history."""
    p = _get(db, patient_id)
    old = _get_policy(db, p, policy_id)
    if not old.is_active:
        raise HTTPException(409, "Policy is already archived")
    data = body.model_dump(exclude_none=True)
    data["coverage_order"] = old.coverage_order or "primary"
    try:
        registration.archive_policy(db, old, actor=user.email, reason="archived and replaced")
        new = registration.add_policy(db, p, data, actor=user.email)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc)
    return _policy_detail(db, p, new)


# --------------------------------------------------------------------------- eligibility

class EligibilityIn(BaseModel):
    provider_id: int | None = None
    policy_id: int | None = None
    service_date: date | None = None
    npi_type: Literal["individual", "group"] = "individual"


def _run_check(db: Session, p: Patient, pol: InsurancePolicy | None, body: EligibilityIn, user: User) -> EligibilityCheck:
    provider = db.get(Provider, body.provider_id) if body.provider_id else None
    if body.provider_id and provider is None:
        raise HTTPException(404, "Provider not found")
    npi_type = body.npi_type if (provider or body.npi_type == "group") else "group"
    try:
        check = eligibility.run_eligibility(db, p, pol, provider, actor=user.email, service_date=body.service_date,
                                            npi_type=npi_type)
        db.commit()
    except eligibility.EligibilityError as exc:
        _fail(db, exc)
    return check


@router.post("/{patient_id}/eligibility")
def check_eligibility(patient_id: int, body: EligibilityIn, db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
    p = _get(db, patient_id)
    pol = _get_policy(db, p, body.policy_id) if body.policy_id else p.primary_policy
    check = _run_check(db, p, pol, body, user)
    return S.eligibility(check)


@router.post("/{patient_id}/policies/{policy_id}/eligibility")
def check_policy_eligibility(patient_id: int, policy_id: int, body: EligibilityIn, db: Session = Depends(get_db),
                             user: User = Depends(current_user)):
    p = _get(db, patient_id)
    pol = _get_policy(db, p, policy_id)
    if not pol.is_active:
        raise HTTPException(409, "Eligibility can't be checked on an archived policy")
    check = _run_check(db, p, pol, body, user)
    return _eligibility_detail(db, check)


def _eligibility_detail(db: Session, check: EligibilityCheck) -> dict:
    db.refresh(check)
    patient = db.get(Patient, check.patient_id)
    provider = db.get(Provider, check.provider_id) if check.provider_id else None
    pol = db.get(InsurancePolicy, check.policy_id) if check.policy_id else None
    return S.eligibility_detail(check, patient, provider, pol)


@router.get("/{patient_id}/eligibility/{check_id}")
def eligibility_response(patient_id: int, check_id: int, db: Session = Depends(get_db),
                         _: User = Depends(current_user)):
    """'View Eligibility Response': detailed benefits, original 271 and discrepancies."""
    p = _get(db, patient_id)
    check = db.get(EligibilityCheck, check_id)
    if check is None or check.patient_id != p.id:
        raise HTTPException(404, "Eligibility response not found")
    return _eligibility_detail(db, check)


class SelectBenefitIn(BaseModel):
    benefit_id: int


@router.post("/{patient_id}/policies/{policy_id}/select-benefit")
def select_benefit(patient_id: int, policy_id: int, body: SelectBenefitIn, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    """'Select' next to Co-Pay / Deductible / ...: copy a 271 value onto the policy."""
    p = _get(db, patient_id)
    pol = _get_policy(db, p, policy_id)
    b = db.get(EligibilityBenefit, body.benefit_id)
    if b is None:
        raise HTTPException(404, "Benefit not found")
    try:
        eligibility.apply_benefit_to_policy(db, pol, b, actor=user.email)
        db.commit()
    except eligibility.EligibilityError as exc:
        _fail(db, exc)
    return _policy_detail(db, p, pol)


# --------------------------------------------------------------------------- documents

@router.post("/{patient_id}/documents", status_code=201)
async def upload_document(
    patient_id: int,
    kind: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    p = _get(db, patient_id)
    try:
        content = await file.read()
    except Exception as exc:  # client disconnects / malformed multipart
        log.warning("upload read failed: %s", exc)
        raise HTTPException(400, "The file could not be read. Try uploading it again.") from None
    try:
        doc = registration.store_document(db, p, kind=kind, filename=file.filename or "upload",
                                          content_type=file.content_type or "", content=content, actor=user.email)
        db.commit()
    except registration.RegistrationError as exc:
        _fail(db, exc)
    return S.document(doc)


@router.get("/{patient_id}/documents/{doc_id}")
def download_document(patient_id: int, doc_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    doc = db.get(PatientDocument, doc_id)
    if doc is None or doc.patient_id != patient_id:
        raise HTTPException(404, "Document not found")
    from pathlib import Path

    if not Path(doc.storage_path).is_file():
        raise HTTPException(410, "The stored file is missing. Upload it again.")
    return FileResponse(doc.storage_path, media_type=doc.content_type, filename=doc.filename)
