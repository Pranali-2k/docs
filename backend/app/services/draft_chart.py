"""Draft patient charts for unmatched new-patient requests (staff approval required).

When an email asks for an appointment for someone who has no chart, the AI prepares:
  * the chart fields it could read from the email (name, DOB, sex, phone, email, address, SSN last 4),
  * possible duplicates already on file, and
  * a proposed slot (the exact requested date/time when free, otherwise the nearest alternatives).
Nothing is created or booked here. `approve()` creates the chart, links it and books the chosen slot in ONE
transaction; `discard()` drops the proposal. Both are audited.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Patient, PortalRequest, Provider
from app.services import audit, registration, scheduling
from app.services.normalize import parse_date

log = logging.getLogger("docshealth.draft_chart")

CHART_FIELDS = ("first_name", "last_name", "date_of_birth", "sex", "phone", "email",
                "address_line1", "city", "state", "postal_code", "ssn_last4")
REQUIRED = ("first_name", "last_name", "date_of_birth")
RECOMMENDED = ("sex", "address_line1")


class DraftError(Exception):
    pass


def eligible(x) -> bool:
    """Only for requests to book someone new, with at least a full name to start a chart from."""
    return bool(x.patient.first_name and x.patient.last_name) and (
        x.intent == "new_appointment" or x.is_new_patient is True)


def _exact_time(value: str | None) -> time | None:
    try:
        return time.fromisoformat(value) if value else None
    except ValueError:
        return None


def propose_slots(db: Session, x, provider: Provider | None = None, limit: int = 5) -> dict:
    """Proposed slot + alternatives. `matches_request` is True only when the exact requested date (and time,
    when one was asked for) is free."""
    ids = [provider.id] if provider else list(db.scalars(select(Provider.id).where(Provider.is_active)))
    preferred = [d for d in (parse_date(s) for s in x.appointment.preferred_dates) if d]
    exact = _exact_time(x.appointment.preferred_time)
    slots, matched = scheduling.find_slots(db, ids, preferred_dates=preferred, exact_time=exact,
                                           time_of_day=x.appointment.preferred_time_of_day, limit=limit)
    rows = [{"provider_id": pid, "provider_name": db.get(Provider, pid).full_name, "start_at": s.isoformat(),
             "label": scheduling.fmt_slot(s)} for pid, s in slots]
    first = slots[0][1] if slots else None
    exact_hit = bool(first and preferred and first.date() in preferred and (exact is None or first.time() == exact))
    return {"proposed": rows[0] if rows else None, "alternatives": rows[1:], "matches_request": exact_hit and matched,
            "requested": {"dates": [d.isoformat() for d in preferred], "time": x.appointment.preferred_time,
                          "time_of_day": x.appointment.preferred_time_of_day}}


def build(db: Session, req: PortalRequest, x, provider: Provider | None = None) -> dict:
    p = x.patient
    fields = {k: getattr(p, k, None) for k in CHART_FIELDS}
    fields["phone"] = fields["phone"] or p.contact_phone
    fields["email"] = fields["email"] or p.contact_email
    dob = parse_date(fields["date_of_birth"])
    dupes = registration.find_duplicates(db, fields["first_name"], fields["last_name"], dob) if dob else []
    insurance = x.insurance.model_dump() if (x.insurance.payer_name or x.insurance.member_id) else None
    slots = propose_slots(db, x, provider)
    notes = []
    if not p.requester_is_patient:
        notes.append("Sent on the patient's behalf (e.g. attorney or case manager): confirm details before approving.")
    if not insurance:
        notes.append("No insurance in the email: eligibility can't be checked until a policy is added.")
    if slots["proposed"] and not slots["matches_request"] and (slots["requested"]["dates"] or slots["requested"]["time"]):
        notes.append("The exact requested date/time is not available; the nearest open slots are proposed.")
    return {
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "patient": fields,
        "missing_required": [k for k in REQUIRED if not fields.get(k)],
        "missing_recommended": [k for k in RECOMMENDED if not fields.get(k)] + ([] if fields["phone"] or fields["email"]
                                                                               else ["phone_or_email"]),
        "duplicates": [{"id": d.id, "mrn": d.mrn, "name": f"{d.first_name} {d.last_name}",
                        "date_of_birth": d.date_of_birth.isoformat() if d.date_of_birth else None} for d in dupes],
        "insurance": insurance,
        "visit_reason": x.appointment.visit_reason,
        "requester": {"is_patient": p.requester_is_patient, "relationship": p.requester_relationship,
                      "from_address": req.source_email.from_address if req.source_email else None},
        **slots,
        "notes": notes,
    }


def refresh(db: Session, req: PortalRequest, x, *, matched: bool, provider: Provider | None, actor: str) -> None:
    """Called by the pipeline after patient matching. Keeps decided (approved/discarded) drafts untouched."""
    current = req.draft_chart or {}
    if current.get("status") in {"approved", "discarded"}:
        return
    if matched or not eligible(x):
        if current:
            req.draft_chart = None
        return
    try:
        req.draft_chart = build(db, req, x, provider)
        audit.record(db, "request", req.id, "draft_chart_prepared", actor,
                     missing=req.draft_chart["missing_required"], duplicates=len(req.draft_chart["duplicates"]),
                     proposed=(req.draft_chart["proposed"] or {}).get("start_at"))
    except Exception:  # a draft is a convenience: never let it break request processing
        log.exception("could not prepare a draft chart for request %s", req.id)
        req.draft_chart = None


def approve(db: Session, req: PortalRequest, *, fields: dict, actor: str, allow_duplicate: bool = False,
            book: dict | None = None, payer_id: int | None = None, member_id: str | None = None,
            group_number: str | None = None) -> tuple[Patient, object | None]:
    """Create the chart (and policy), link it to the request and optionally book. The caller commits;
    any error leaves nothing behind once the caller rolls back."""
    draft = req.draft_chart or {}
    if draft.get("status") != "pending":
        raise DraftError("There is no pending draft chart on this request")
    if req.patient_id:
        raise DraftError("This request is already linked to a patient")
    data = {**(draft.get("patient") or {}), **{k: v for k, v in fields.items() if k in CHART_FIELDS}}
    last4 = (data.pop("ssn_last4", None) or "").strip()
    data = {k: (v.strip() if isinstance(v, str) else v) or None for k, v in data.items()}
    missing = [k for k in REQUIRED if not data.get(k)]
    if missing:
        raise DraftError("Required before creating the chart: " + ", ".join(m.replace("_", " ") for m in missing))
    try:
        patient = registration.create_patient(db, data, actor=actor, allow_duplicate=allow_duplicate)
        if last4:
            if not (len(last4) == 4 and last4.isdigit()):
                raise DraftError("SSN last 4 must be 4 digits")
            patient.ssn_last4 = last4
        if payer_id and member_id:
            registration.add_policy(db, patient, {"payer_id": payer_id, "member_id": member_id,
                                                  "group_number": group_number}, actor=actor)
    except registration.RegistrationError as exc:
        raise DraftError(str(exc)) from exc
    req.patient_id = patient.id
    req.match_details = {**(req.match_details or {}), "status": "linked_by_staff", "linked_by": actor,
                         "via": "draft_chart"}
    appt = None
    if book:
        provider = db.get(Provider, book["provider_id"])
        if provider is None:
            raise DraftError("Provider not found")
        start = book["start_at"]
        start = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
        try:
            appt = scheduling.book(db, patient, provider, start.replace(tzinfo=None), request_id=req.id, actor=actor,
                                   reason=draft.get("visit_reason"))
        except scheduling.SchedulingError as exc:
            raise DraftError(f"Could not book that slot: {exc}") from exc
        req.appointment_id, req.provider_id = appt.id, provider.id
    req.draft_chart = {**draft, "status": "approved", "approved_by": actor,
                       "approved_at": datetime.now(timezone.utc).isoformat(), "patient_id": patient.id,
                       "mrn": patient.mrn, "appointment_id": appt.id if appt else None}
    audit.record(db, "request", req.id, "draft_chart_approved", actor, patient_id=patient.id, mrn=patient.mrn,
                 appointment_id=appt.id if appt else None)
    return patient, appt


def discard(db: Session, req: PortalRequest, *, actor: str, reason: str | None = None) -> None:
    draft = req.draft_chart or {}
    if draft.get("status") != "pending":
        raise DraftError("There is no pending draft chart on this request")
    req.draft_chart = {**draft, "status": "discarded", "discarded_by": actor, "discard_reason": reason,
                       "discarded_at": datetime.now(timezone.utc).isoformat()}
    audit.record(db, "request", req.id, "draft_chart_discarded", actor, reason=reason)

