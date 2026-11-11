"""Portal request management: inbox, detail, staff review actions, resolution."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.config import get_settings
from app.db import get_db
from app.models import Appointment, AuditLog, EligibilityCheck, EmailMessage, Patient, PortalRequest, Provider, RequestStatus, User
from app.security import current_user
from app.services import audit, audit_text, db_procs, draft_chart, email_gateway, pipeline, registration, responder, scheduling
from app.services.extraction import ExtractedRequest
from app.services.normalize import parse_date

router = APIRouter(prefix="/api/requests", tags=["requests"])
log = logging.getLogger("docshealth.requests")


def _get(db: Session, request_id: int) -> PortalRequest:
    req = db.get(PortalRequest, request_id)
    if req is None:
        raise HTTPException(404, "Request not found")
    return req


def detail(db: Session, req: PortalRequest) -> dict:
    db.refresh(req)  # relationships may be stale after id assignments
    emails = db.scalars(select(EmailMessage).where(EmailMessage.request_id == req.id).order_by(EmailMessage.received_at))
    checks = db.scalars(
        select(EligibilityCheck).where(EligibilityCheck.request_id == req.id).order_by(EligibilityCheck.checked_at.desc())
    )
    events = db.scalars(
        select(AuditLog).where(AuditLog.entity_type == "request", AuditLog.entity_id == req.id).order_by(AuditLog.id)
    )
    return {
        **S.request_row(req),
        "extracted": req.extracted,
        "match_details": req.match_details,
        "provider": S.provider(req.provider) if req.provider else None,
        "appointment": S.appointment(req.appointment) if req.appointment else None,
        "draft_response": req.draft_response,
        "draft_chart": req.draft_chart,
        "llm_provider": req.llm_provider,
        "resolution_note": req.resolution_note,
        "resolved_by": req.resolved_by,
        "first_response_at": S.iso(req.first_response_at),
        "emails": [S.email(m) for m in emails],
        "eligibility_checks": [S.eligibility(c) for c in checks],
        "timeline": audit_text.annotate(db, list(events)),
    }


def _clinic_zone() -> ZoneInfo:
    return ZoneInfo(get_settings().clinic_timezone)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of a clinic-local calendar day, as UTC instants (naive on SQLite, which stores no zone)."""
    zone = _clinic_zone()
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    return start, end


@router.get("/days")
def request_days(
    days: int = 60,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    """The clinic-local days that have email requests, newest first, with how many: for the day picker."""
    zone = _clinic_zone()
    since = datetime.now(timezone.utc) - timedelta(days=min(max(days, 1), 366))
    received = func.coalesce(EmailMessage.received_at, PortalRequest.created_at)
    rows = db.execute(
        select(received, PortalRequest.status)
        .join(EmailMessage, PortalRequest.source_email_id == EmailMessage.id, isouter=True)
        .where(received >= since)
    ).all()
    counts: dict[str, dict] = {}
    for moment, status in rows:
        moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        key = moment.astimezone(zone).date().isoformat()
        entry = counts.setdefault(key, {"day": key, "count": 0, "open": 0})
        entry["count"] += 1
        entry["open"] += status not in {RequestStatus.RESOLVED.value, RequestStatus.CLOSED.value}
    return sorted(counts.values(), key=lambda e: e["day"], reverse=True)


@router.get("")
def list_requests(
    status: str | None = None,
    intent: str | None = None,
    q: str | None = None,
    reason: str | None = None,
    day: date | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    stmt = select(PortalRequest).join(EmailMessage, PortalRequest.source_email_id == EmailMessage.id, isouter=True)
    if status:
        stmt = stmt.where(PortalRequest.status.in_(status.split(",")))
    if intent:
        stmt = stmt.where(PortalRequest.intent == intent)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.outerjoin(Patient, PortalRequest.patient_id == Patient.id).where(or_(
            PortalRequest.reference.ilike(like), EmailMessage.subject.ilike(like), EmailMessage.from_address.ilike(like),
            Patient.last_name.ilike(like), Patient.first_name.ilike(like), Patient.mrn.ilike(like),
        ))
    if day:
        # The day the mail arrived, on the clinic's calendar (Los Angeles), not the server's or the browser's.
        start, end = _day_bounds(day)
        received = func.coalesce(EmailMessage.received_at, PortalRequest.created_at)
        stmt = stmt.where(received >= start, received < end)
    rows = db.scalars(stmt.order_by(PortalRequest.created_at.desc()).limit(min(limit, 500))).all()
    out = [S.request_row(r) for r in rows]
    if reason:
        out = [r for r in out if any(x["code"] == reason for x in r["review_reasons"])]
    return out


@router.get("/{request_id}")
def get_request(request_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return detail(db, _get(db, request_id))


@router.post("/{request_id}/reprocess")
def reprocess(request_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    req = _get(db, request_id)
    try:
        pipeline.process_request(db, req, actor=user.email, keep_patient=bool(req.patient_id and
                                 (req.match_details or {}).get("status") == "linked_by_staff"))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return detail(db, req)


class AssignIn(BaseModel):
    assignee: str | None = None


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _chart_fields(p: Patient) -> dict:
    """The demographics a draft chart form is filled from."""
    return {
        "id": p.id, "mrn": p.mrn, "first_name": p.first_name, "last_name": p.last_name,
        "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else None, "sex": p.sex,
        "phone": p.phone, "email": p.email, "address_line1": p.address_line1, "city": p.city, "state": p.state,
        "postal_code": p.postal_code,
    }


@router.get("/{request_id}/patient-suggestions")
def patient_suggestions(request_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Charts this request might be about, with their demographics, so a form can be filled from one.

    Looks the person up the way staff would: by name (and date of birth when the email gave one), by the
    phone number or by the email address. Nothing is linked; staff choose.
    """
    req = _get(db, request_id)
    ext = (req.extracted or {}).get("patient") or {}
    first, last = (ext.get("first_name") or "").strip(), (ext.get("last_name") or "").strip()
    dob = parse_date(ext.get("date_of_birth")) if ext.get("date_of_birth") else None
    phone = _digits(ext.get("phone"))[-10:]
    emails = {e.lower() for e in (ext.get("email"), ext.get("contact_email")) if e}
    found: dict[int, tuple[Patient, list[str]]] = {}

    def note(p: Patient, why: str) -> None:
        entry = found.setdefault(p.id, (p, []))
        if why not in entry[1]:
            entry[1].append(why)

    if last:
        stmt = select(Patient).where(Patient.last_name.ilike(last))
        if first:
            stmt = stmt.where(Patient.first_name.ilike(f"{first[:3]}%"))
        for p in db.scalars(stmt.limit(8)):
            note(p, "name")
            if dob and p.date_of_birth == dob:
                note(p, "date of birth")
    if dob and last:
        for p in db.scalars(select(Patient).where(Patient.date_of_birth == dob, Patient.last_name.ilike(f"{last[:4]}%")).limit(5)):
            note(p, "date of birth")
    if len(phone) >= 7:
        for p in db.scalars(select(Patient).where(Patient.phone.like(f"%{phone}")).limit(5)):
            note(p, "phone")
    for address in emails:
        for p in db.scalars(select(Patient).where(Patient.email == address).limit(5)):
            note(p, "email")
    ranked = sorted(found.values(), key=lambda item: -len(item[1]))[:5]
    return [{**_chart_fields(p), "evidence": why, "primary_payer": S.patient_summary(p).get("primary_payer"),
             "member_id": S.patient_summary(p).get("member_id")} for p, why in ranked]


class DraftReplyIn(BaseModel):
    instructions: str | None = Field(default=None, max_length=400)


@router.post("/{request_id}/draft-reply")
def draft_reply(request_id: int, body: DraftReplyIn, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """A reply to the email that was received.

    The facts a patient is told (provider, date and time, the options, the reference) always come from
    the database through the vetted templates. When a language model is available it writes the reply to what
    the person actually asked, and the result is accepted only if every fact survives verbatim; otherwise the
    template is returned. `engine` says which one wrote it.
    """
    from app.llm.providers import LLMError, get_llm

    req = _get(db, request_id)
    email = req.source_email
    ext = req.extracted or {}
    first = ((req.patient.first_name if req.patient else None) or (ext.get("patient") or {}).get("first_name")
             or "there")
    facts: dict = {"first_name": first}
    chart = req.draft_chart or {}
    if req.appointment is not None:
        provider = req.appointment.provider or db.get(Provider, req.appointment.provider_id)
        kind = {"reschedule": "rescheduled", "cancel": "cancelled"}.get(req.intent, "booked")
        if req.appointment.status == "cancelled" and req.intent != "cancel":
            kind = "review_hold"
        facts.update({"when": scheduling.fmt_slot(req.appointment.start_at), "provider": provider.full_name,
                      "location": provider.location})
    elif ext.get("clinical_red_flag"):
        kind = "red_flag"
    elif req.patient is None and req.intent == "new_appointment" and pipeline.details_request_facts(
        db, req, ExtractedRequest.model_validate(ext)
    ):
        kind = "request_details"
        facts.update(pipeline.details_request_facts(db, req, ExtractedRequest.model_validate(ext)))
    elif chart.get("proposed"):
        options = [chart["proposed"], *chart.get("alternatives", [])][:3]
        kind = "offer_slots"
        facts["slot_list"] = "\n".join(f"  - {o['label']} with {o['provider_name']}" for o in options)
        if chart["proposed"].get("provider_name"):
            facts["with_provider"] = f" with {chart['proposed']['provider_name']}"
    else:
        kind = "review_hold"
    try:
        subject, text = responder.compose(kind, facts, None, reference=req.reference)
    except KeyError as exc:  # a template fact that is not known for this request
        raise HTTPException(409, f"Cannot draft a reply yet: missing {exc}") from None
    result = {"subject": subject, "body": text, "engine": "template", "kind": kind}

    try:
        llm = get_llm()
    except LLMError:
        llm = None
    if llm is not None and not llm.is_offline and email is not None:
        must_keep = [str(facts[k]) for k in ("when", "provider") if facts.get(k)]
        must_keep += [line.strip() for line in str(facts.get("slot_list", "")).splitlines() if line.strip()]
        system = (
            "You write short, warm email replies for a medical scheduling office. Reply to what the person actually "
            "asked, in the same language. Use ONLY the facts supplied; never invent dates, times, names, prices or "
            "instructions. Do not give medical advice. Return only the email body: no subject, no signature."
        )
        prompt = (f"Email received:\n{(email.body_text or '')[:1800]}\n\nFacts you must keep exactly:\n"
                  f"{text}\n" + (f"\nStaff instruction: {body.instructions}\n" if body.instructions else ""))
        try:
            written = llm.complete(system, prompt).strip()
        except LLMError as exc:
            result["note"] = f"The language model was unavailable ({str(exc)[:80]}); this is the standard reply."
        else:
            if written and all(f in written for f in must_keep) and len(written) < 4000:
                result.update({"body": written + responder.SIGNOFF.format(clinic=get_settings().clinic_name,
                                                                         reference=req.reference),
                               "engine": f"llm:{llm.name}"})
            else:
                result["note"] = "The model's reply changed a fact, so the standard reply is shown."
    elif llm is None or llm.is_offline:
        result["note"] = "No language model is configured, so this is the standard reply for this situation."
    return result


@router.post("/{request_id}/assign")
def assign(request_id: int, body: AssignIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    req = _get(db, request_id)
    db_procs.scalar(db, "SELECT assign_request(:req, :assignee, :actor)",
                    {"req": req.id, "assignee": body.assignee, "actor": user.email})
    db.commit()
    db.refresh(req)
    return detail(db, req)


class LinkIn(BaseModel):
    patient_id: int
    reprocess: bool = True


@router.post("/{request_id}/link-patient")
def link_patient(request_id: int, body: LinkIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    req = _get(db, request_id)
    patient = db.get(Patient, body.patient_id)
    if patient is None:
        raise HTTPException(404, "Patient not found")
    req.patient_id = patient.id
    req.match_details = {**(req.match_details or {}), "status": "linked_by_staff", "linked_by": user.email}
    audit.record(db, "request", req.id, "patient_linked", user.email, patient_id=patient.id, mrn=patient.mrn)
    if body.reprocess and req.status not in {RequestStatus.RESOLVED.value, RequestStatus.CLOSED.value}:
        try:
            pipeline.process_request(db, req, actor=user.email, keep_patient=True)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
    # Staff confirmed who this is: the match is as certain as it gets, whatever the automatic score was.
    req.match_confidence = 1.0
    db.commit()
    return detail(db, req)


class CreatePatientIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: str | None = None
    phone: str | None = None
    email: str | None = None
    payer_id: int | None = None
    member_id: str | None = None
    group_number: str | None = None
    allow_duplicate: bool = False


@router.post("/{request_id}/create-patient")
def create_patient_from_request(
    request_id: int, body: CreatePatientIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    """Register a new patient using the AI-extracted fields (staff may override any of them)."""
    req = _get(db, request_id)
    ext = req.extracted or {}
    pat, ins = ext.get("patient") or {}, ext.get("insurance") or {}
    first = body.first_name or pat.get("first_name")
    last = body.last_name or pat.get("last_name")
    if not first or not last:
        raise HTTPException(422, "First and last name are required")
    try:
        patient = registration.create_patient(db, {
            "first_name": first, "last_name": last,
            "date_of_birth": parse_date(body.date_of_birth or pat.get("date_of_birth")),
            "phone": body.phone or pat.get("phone"), "email": body.email or pat.get("email"),
        }, actor=user.email, allow_duplicate=body.allow_duplicate)
        member = body.member_id or ins.get("member_id")
        if body.payer_id and member:
            registration.add_policy(db, patient, {"payer_id": body.payer_id, "member_id": member,
                                                  "group_number": body.group_number or ins.get("group_number")},
                                    actor=user.email)
    except registration.RegistrationError as exc:
        raise HTTPException(409, str(exc)) from None
    return link_patient(request_id, LinkIn(patient_id=patient.id), db, user)


class DraftBookIn(BaseModel):
    provider_id: int
    start_at: datetime


class DraftApproveIn(BaseModel):
    patient: dict = Field(default_factory=dict)  # staff edits to the AI-drafted fields
    allow_duplicate: bool = False
    book: DraftBookIn | None = None
    send_confirmation: bool = True
    payer_id: int | None = None
    member_id: str | None = Field(default=None, max_length=40)
    group_number: str | None = Field(default=None, max_length=40)


@router.post("/{request_id}/draft-chart/approve")
def approve_draft_chart(request_id: int, body: DraftApproveIn, db: Session = Depends(get_db),
                        user: User = Depends(current_user)):
    """Staff approval: create the drafted chart, link it, and (optionally) book the chosen slot, all or nothing."""
    req = _get(db, request_id)
    try:
        patient, appt = draft_chart.approve(
            db, req, fields=body.patient, actor=user.email, allow_duplicate=body.allow_duplicate,
            book=body.book.model_dump() if body.book else None, payer_id=body.payer_id,
            member_id=body.member_id, group_number=body.group_number)
    except draft_chart.DraftError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except Exception:
        db.rollback()
        log.exception("draft chart approval failed for request %s", request_id)
        raise HTTPException(500, "The chart could not be created; nothing was saved. Try again.") from None
    if appt is not None:
        if body.send_confirmation and req.source_email:
            provider = appt.provider or db.get(Provider, appt.provider_id)
            subject, text = responder.compose("booked", {"first_name": patient.first_name,
                                                         "when": scheduling.fmt_slot(appt.start_at),
                                                         "provider": provider.full_name,
                                                         "location": provider.location}, None, reference=req.reference)
            # replies go to whoever wrote in (the patient, or their attorney/case manager), held while outbound is off
            email_gateway.send_reply(db, req, to_address=req.source_email.from_address, subject=subject, body=text,
                                     actor=user.email)
        _resolve(db, req, user, f"Chart {patient.mrn} created from the AI draft and appointment booked for "
                                f"{scheduling.fmt_slot(appt.start_at)}.")
    else:
        try:  # chart only: re-run the checks (insurance, eligibility, slots) against the new chart
            pipeline.process_request(db, req, actor=user.email, keep_patient=True)
        except ValueError as exc:
            log.info("not reprocessed after draft approval: %s", exc)
    req.match_confidence = 1.0  # the chart was confirmed and created by staff
    db.commit()
    return detail(db, req)


class DraftDiscardIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post("/{request_id}/draft-chart/discard")
def discard_draft_chart(request_id: int, body: DraftDiscardIn, db: Session = Depends(get_db),
                        user: User = Depends(current_user)):
    req = _get(db, request_id)
    try:
        draft_chart.discard(db, req, actor=user.email, reason=body.reason)
    except draft_chart.DraftError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return detail(db, req)


@router.get("/{request_id}/draft-chart/slots")
def draft_chart_slots(request_id: int, provider_id: int | None = None, db: Session = Depends(get_db),
                      _: User = Depends(current_user)):
    """More slot options for the draft (optionally for one provider), honouring the requested date/time."""
    from app.services.extraction import ExtractedRequest

    req = _get(db, request_id)
    x = ExtractedRequest.model_validate(req.extracted or {})
    provider = db.get(Provider, provider_id) if provider_id else None
    return draft_chart.propose_slots(db, x, provider, limit=8)


class BookIn(BaseModel):
    provider_id: int
    start_at: datetime
    visit_reason: str | None = None
    send_confirmation: bool = True
    cancel_appointment_id: int | None = None  # for reschedules


@router.post("/{request_id}/book")
def book_for_request(request_id: int, body: BookIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    req = _get(db, request_id)
    if req.patient is None:
        raise HTTPException(409, "Link or register the patient first")
    provider = db.get(Provider, body.provider_id)
    if provider is None:
        raise HTTPException(404, "Provider not found")
    start = body.start_at.replace(tzinfo=None)
    try:
        reason = body.visit_reason or (req.extracted or {}).get("appointment", {}).get("visit_reason")
        old = None
        if body.cancel_appointment_id:
            old = db.get(Appointment, body.cancel_appointment_id)
            if old is None or old.patient_id != req.patient_id:
                raise HTTPException(404, "Appointment to cancel not found for this patient")
            appt = scheduling.reschedule(db, old, provider, start, request_id=req.id, actor=user.email, reason=reason)
        else:
            appt = scheduling.book(db, req.patient, provider, start, request_id=req.id, actor=user.email, reason=reason)
    except scheduling.SchedulingError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    req.appointment_id, req.provider_id = appt.id, provider.id
    if body.send_confirmation:
        facts = {"first_name": req.patient.first_name, "when": scheduling.fmt_slot(start),
                 "provider": provider.full_name, "location": provider.location}
        kind = "booked"
        if old is not None:
            kind, facts["old_when"] = "rescheduled", scheduling.fmt_slot(old.start_at)
        subject, text = responder.compose(kind, facts, None, reference=req.reference)
        to = req.source_email.from_address if req.source_email else req.patient.email
        if to:
            email_gateway.send_reply(db, req, to_address=to, subject=subject, body=text, actor=user.email)
    _resolve(db, req, user, f"Appointment booked by staff for {scheduling.fmt_slot(start)}.")
    db.commit()
    return detail(db, req)


class SendIn(BaseModel):
    subject: str | None = None
    body: str = Field(min_length=1)
    resolve: bool = False
    to_address: str | None = None


@router.post("/{request_id}/send-response")
def send_response(request_id: int, body: SendIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    req = _get(db, request_id)
    to = body.to_address or (req.source_email.from_address if req.source_email else None) or \
        (req.patient.email if req.patient else None)
    if not to:
        raise HTTPException(422, "No recipient address")
    subject = body.subject or f"Re: {req.source_email.subject if req.source_email else 'Your request'} [{req.reference}]"
    msg = email_gateway.send_reply(db, req, to_address=to, subject=subject, body=body.body, actor=user.email)
    req.draft_response = body.body
    if body.resolve:
        _resolve(db, req, user, "Staff replied and resolved.")
    elif req.status == RequestStatus.NEEDS_REVIEW.value:
        req.status = RequestStatus.IN_REVIEW.value
    db.commit()
    return {"email": S.email(msg), "request": detail(db, req)}


class ResolveIn(BaseModel):
    note: str = Field(min_length=1)
    close: bool = False  # closed = no action needed (spam, duplicate, ...)


def _resolve(db: Session, req: PortalRequest, user: User, note: str, close: bool = False) -> None:
    try:
        db_procs.scalar(db, "SELECT resolve_request(:req, :note, :close, :actor)",
                        {"req": req.id, "note": note, "close": close, "actor": user.email})
    except db_procs.ProcedureError as exc:
        raise HTTPException(409, str(exc)) from None
    db.refresh(req)


@router.post("/{request_id}/resolve")
def resolve(request_id: int, body: ResolveIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    req = _get(db, request_id)
    _resolve(db, req, user, body.note, body.close)
    db.commit()
    return detail(db, req)


@router.post("/{request_id}/reopen")
def reopen(request_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    req = _get(db, request_id)
    pipeline.reopen(db, req, user.email)
    db.commit()
    return detail(db, req)


@router.get("/{request_id}/slots")
def suggest_slots(request_id: int, provider_id: int | None = None, db: Session = Depends(get_db),
                  _: User = Depends(current_user)):
    req = _get(db, request_id)
    appt = (req.extracted or {}).get("appointment") or {}
    ids = [provider_id] if provider_id else [p.id for p in db.scalars(select(Provider).where(Provider.is_active))]
    dates = [d for d in (parse_date(s) for s in appt.get("preferred_dates", [])) if d]
    slots, matched = scheduling.find_slots(db, ids, preferred_dates=dates,
                                           time_of_day=appt.get("preferred_time_of_day", "any"), limit=8)
    return {
        "matches_preference": matched,
        "slots": [{"provider_id": pid, "provider_name": db.get(Provider, pid).full_name, "start_at": s.isoformat(),
                   "label": scheduling.fmt_slot(s)} for pid, s in slots],
    }


# --------------------------------------------------------------------------- bulk actions

class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["close", "resolve", "reprocess"]
    note: str | None = Field(default=None, max_length=1000)


@router.post("/bulk")
def bulk(body: BulkIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Close / resolve / re-run the AI on many requests. Each request is committed on its own, so one
    problem never undoes the rest; the result lists what was done, skipped (and why) and failed."""
    note = (body.note or "").strip()
    if body.action in {"close", "resolve"} and not note:
        raise HTTPException(422, "A note is required to close or resolve requests")
    done, skipped, failed = [], [], []
    for rid in dict.fromkeys(body.ids):  # de-duplicate, keep order
        req = db.get(PortalRequest, rid)
        if req is None:
            skipped.append({"id": rid, "reason": "not found"})
            continue
        ref = req.reference
        try:
            if body.action == "reprocess":
                pipeline.process_request(db, req, actor=user.email, keep_patient=bool(
                    req.patient_id and (req.match_details or {}).get("status") == "linked_by_staff"))
            elif req.status in {RequestStatus.RESOLVED.value, RequestStatus.CLOSED.value}:
                skipped.append({"id": rid, "reference": ref, "reason": f"already {req.status}"})
                continue
            else:
                _resolve(db, req, user, note, close=body.action == "close")
            db.commit()
            done.append({"id": rid, "reference": ref, "status": req.status})
        except ValueError as exc:  # e.g. resolved/booked requests can't be re-run
            db.rollback()
            skipped.append({"id": rid, "reference": ref, "reason": str(exc)})
        except Exception as exc:
            db.rollback()
            log.exception("bulk %s failed for request %s", body.action, rid)
            failed.append({"id": rid, "reference": ref, "error": type(exc).__name__})
    audit.record(db, "request", None, f"bulk_{body.action}", user.email, requested=len(body.ids),
                 done=len(done), skipped=len(skipped), failed=len(failed), note=note or None)
    db.commit()
    return {"action": body.action, "done": done, "skipped": skipped, "failed": failed}
