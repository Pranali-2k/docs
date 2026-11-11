"""Portal workflow orchestrator.

    Email -> Request Intake -> Patient Match -> AI Analysis -> Auto Response OR Manual Review -> Resolution

Mirrors the PRD's scheduling workflow: identify the patient, confirm the insurance
on the chart, run eligibility, confirm network participation, schedule, then
update the chart. Every step is audited. Any manual-review trigger stops the
automation before it touches the schedule or emails the patient.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.providers import LLMProvider, get_llm
from app.models import Appointment, Patient, Payer, PortalRequest, Provider, ProviderNetwork, RequestStatus
from app.services import audit, draft_chart, eligibility, email_gateway, responder, scheduling
from app.services.extraction import ExtractedRequest, ExtractionContext, extract_request
from app.services.matching import MatchResult, match_patient
from app.services.normalize import name_similarity, normalize_member_id, parse_date

REVIEW_REASONS = {
    "CLINICAL_RED_FLAG": "Possible urgent clinical symptoms. Contact the patient and triage now.",
    "AI_ENGINE_ERROR": "The AI model failed, so rule-based extraction was used. Verify all fields.",
    "LOW_CONFIDENCE": "AI confidence is below the automation threshold.",
    "UNSUPPORTED_INTENT": "The request is not a scheduling action the AI can complete.",
    "INCOMPLETE_DATA": "Required information is missing.",
    "UNMATCHED_PATIENT": "No existing patient record matches. Register the patient or link the right chart.",
    "AMBIGUOUS_MATCH": "Several patient records could match.",
    "CONFLICTING_INFO": "Information in the email conflicts with the patient chart.",
    "INSURANCE_CHANGE": "The patient reports new or changed insurance. Verify the card and update the chart.",
    "NO_INSURANCE_ON_FILE": "No active insurance on the chart.",
    "ELIGIBILITY_FAILED": "Insurance eligibility verification did not return active coverage.",
    "OUT_OF_NETWORK": "The requested provider is not in network with the patient's payer.",
    "PROVIDER_NOT_FOUND": "The requested provider is not on staff.",
    "APPOINTMENT_NOT_FOUND": "Could not identify exactly one upcoming appointment to change.",
    "NO_AVAILABILITY": "No open slots found in the search window.",
    "SCHEDULING_ERROR": "The booking could not be completed.",
    "REQUESTED_TIME_UNAVAILABLE": "The exact date/time asked for is not open; alternatives are offered instead.",
}


def missing_details(x: ExtractedRequest) -> list[str]:
    """What a new patient still has to tell us, as plain lines for the reply (only what the email lacks)."""
    p, ins = x.patient, x.insurance
    items = []
    if not (p.first_name and p.last_name):
        items.append("Your full name")
    if not p.date_of_birth:
        items.append("Your date of birth")
    if not p.sex:
        items.append("Your sex (as it appears on your insurance card)")
    if not (p.phone or p.contact_phone):
        items.append("A phone number where we can reach you")
    if not p.email:
        items.append("Your email address")
    if not p.address_line1:
        items.append("Your home address (street, city, state and ZIP code)")
    if not (ins.payer_name and ins.member_id):
        items.append("Your insurance company, member ID and group number (or tell us you are paying yourself)")
    if not x.appointment.provider_name:
        items.append("Which doctor you would like to see, and the days and times that suit you")
    return items


def details_request_facts(db: Session, req: PortalRequest, x: ExtractedRequest) -> dict | None:
    """Facts for the "please send us these details" reply, or None when nothing is missing."""
    items = missing_details(x)
    if not items:
        return None
    proposed = (req.draft_chart or {}).get("proposed")
    slot_line = ""
    if proposed:
        slot_line = (f"We have a tentative opening on {proposed['label']} with {proposed['provider_name']}, "
                     "and will keep it for you while we set up your chart.\n\n")
    return {"first_name": x.patient.first_name or "there",
            "details_list": "\n".join(f"  - {i}" for i in items), "slot_line": slot_line}


def _reason(code: str, detail: str | None = None) -> dict:
    return {"code": code, "message": REVIEW_REASONS[code] + (f" {detail}" if detail else "")}


class Outcome:
    def __init__(self) -> None:
        self.reasons: list[dict] = []
        self.provider: Provider | None = None
        self.in_network_ids: list[int] = []
        self.target_appt: Appointment | None = None
        self.slots: list[tuple[int, datetime]] = []
        self.slots_match_preference = False
        self.eligibility = None
        self.notes: list[str] = []  # things staff should follow up, that do not stop the booking

    def add(self, code: str, detail: str | None = None) -> None:
        if not any(r["code"] == code and r["message"].endswith(detail or "") for r in self.reasons):
            self.reasons.append(_reason(code, detail))


# --------------------------------------------------------------------------- helpers

def _context(db: Session, req: PortalRequest) -> ExtractionContext:
    email = req.source_email
    received = email.received_at if email else datetime.now(timezone.utc)
    payers = [(p.name, list(p.aliases or [])) for p in db.scalars(select(Payer))]
    providers = [p.full_name for p in db.scalars(select(Provider).where(Provider.is_active))]
    ref = max(received.date() if received else date.today(), scheduling.clinic_now().date())
    return ExtractionContext(
        reference_date=ref,
        sender_email=email.from_address if email else None,
        sender_name=email.from_name if email else None,
        payers=payers,
        providers=providers,
    )


def _resolve_provider(db: Session, name: str | None) -> Provider | None:
    if not name:
        return None
    best, best_score = None, 0.0
    tokens = [t for t in name.replace(".", " ").split() if t.lower() not in {"dr", "doctor"}]
    for p in db.scalars(select(Provider).where(Provider.is_active)):
        parts = p.full_name.replace(".", " ").split()
        score = max((name_similarity(t, part) for t in tokens for part in parts[-2:]), default=0)
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 0.85 else None


def _resolve_payer(db: Session, name: str | None) -> Payer | None:
    if not name:
        return None
    for p in db.scalars(select(Payer)):
        if name.lower() in {p.name.lower(), *(a.lower() for a in p.aliases or [])}:
            return p
    return None


def _required_fields(x: ExtractedRequest) -> list[str]:
    missing = []
    if not (x.patient.first_name and x.patient.last_name):
        missing.append("patient full name")
    if not (x.patient.date_of_birth or x.patient.phone or x.patient.email or x.insurance.member_id):
        missing.append("a second identifier (DOB, phone, email or member ID)")
    return missing


# --------------------------------------------------------------------------- main entry

def process_request(
    db: Session, req: PortalRequest, *, actor: str = "system", llm: LLMProvider | None = None, keep_patient: bool = False
) -> PortalRequest:
    """Run (or re-run) the full workflow for a request. Commits are left to the caller."""
    settings = get_settings()
    llm = llm or get_llm()
    if req.status in {RequestStatus.RESOLVED.value, RequestStatus.CLOSED.value}:
        raise ValueError(f"Request {req.reference} is {req.status}; reopen it before reprocessing")
    if req.appointment and req.appointment.status == "booked" and req.appointment.source_request_id == req.id:
        raise ValueError("An appointment was already booked for this request. Resolve it or cancel that booking first")
    req.status = RequestStatus.PROCESSING.value
    audit.record(db, "request", req.id, "processing_started", actor, llm=llm.name)

    # ---- 1. AI analysis: extract structured data from the email ------------------------
    email = req.source_email
    x = extract_request(llm, email.subject if email else "", email.body_text if email else "", _context(db, req))
    req.extracted = x.model_dump()
    req.intent = x.intent
    req.ai_confidence = x.confidence
    req.ai_summary = x.summary
    req.llm_provider = x.engine
    req.priority = "high" if (x.clinical_red_flag or x.appointment.is_urgent) else "normal"
    audit.record(db, "request", req.id, "ai_extracted", actor, engine=x.engine, intent=x.intent,
                 confidence=x.confidence, error=x.engine_error)

    out = Outcome()
    if x.clinical_red_flag:
        out.add("CLINICAL_RED_FLAG")
    if x.engine_error:
        out.add("AI_ENGINE_ERROR", f"({x.engine_error[:120]})")
    if x.confidence < settings.ai_confidence_threshold:
        out.add("LOW_CONFIDENCE", f"({x.confidence:.2f} < {settings.ai_confidence_threshold:.2f})")
    if x.intent in {"general_question", "other", "billing_records"}:
        out.add("UNSUPPORTED_INTENT", f"(intent: {x.intent.replace('_', ' ')})")
    if x.intent == "insurance_update":
        out.add("INSURANCE_CHANGE")
    missing = _required_fields(x)
    if missing:
        out.add("INCOMPLETE_DATA", "Missing: " + ", ".join(missing) + ".")

    # ---- 2. Patient match ---------------------------------------------------------------
    patient: Patient | None = None
    if keep_patient and req.patient_id:
        patient = db.get(Patient, req.patient_id)
        req.match_details = {**(req.match_details or {}), "status": "linked_by_staff"}
        audit.record(db, "request", req.id, "patient_match_kept", actor, patient_id=req.patient_id)
    else:
        m = match_patient(db, x, settings.match_confidence_threshold)
        linked_by_name = False
        if m.status != "matched" and x.intent == "new_appointment" and m.candidates:
            # Booking (not editing a chart): one chart that fits the name exactly and clashes with nothing is enough.
            top = m.candidates[0]
            clear = len(m.candidates) == 1 or top.score - m.candidates[1].score >= 0.2
            if (top.score >= settings.schedule_match_threshold and {"last name", "first name"} <= set(top.evidence)
                    and not top.conflicts and clear):
                m = MatchResult("matched", top.patient_id, top.score, m.candidates, [])
                linked_by_name = True
        req.match_confidence = m.confidence
        req.match_details = {**m.as_dict(), **({"linked_by": "name"} if linked_by_name else {})}
        req.patient_id = m.patient_id
        patient = db.get(Patient, m.patient_id) if m.patient_id else None
        audit.record(db, "request", req.id, "patient_matched" if patient else "patient_match_failed", actor,
                     status=m.status, confidence=m.confidence, patient_id=m.patient_id)
        requested = _resolve_provider(db, x.appointment.provider_name) if x.appointment.provider_name else None
        draft_chart.refresh(db, req, x, matched=m.status != "unmatched", provider=requested, actor=actor)
        if m.status == "unmatched" and x.intent not in {"new_appointment", "reschedule", "cancel", "insurance_update"}:
            pass  # a bill or a question from someone with no chart: not a registration problem
        elif m.status == "unmatched":
            drafted = (req.draft_chart or {}).get("status") == "pending"
            out.add("UNMATCHED_PATIENT", "A draft chart and proposed slot are ready for staff approval." if drafted
                    else "New patient registration may be needed." if x.is_new_patient else None)
        elif m.status == "ambiguous":
            out.add("AMBIGUOUS_MATCH", f"Top score {m.confidence:.2f}.")
        for c in m.conflicts:
            out.add("CONFLICTING_INFO", c + ".")
        if linked_by_name and patient is not None:
            out.notes.append("LINKED_BY_NAME")

    # ---- 3. Chart checks, eligibility, network, scheduling ---------------------------
    if patient is not None:
        _check_chart(db, req, x, patient, out, actor)

    # ---- 4. Decision --------------------------------------------------------------------
    req.review_reasons = out.reasons
    req.provider_id = out.provider.id if out.provider else req.provider_id
    # The asked-for time is taken but the doctor has other openings: answer now with the nearest ones.
    only_taken = (
        settings.offer_slots_when_unavailable and patient is not None and out.slots and x.intent == "new_appointment"
        and out.reasons and all(r["code"] == "REQUESTED_TIME_UNAVAILABLE" for r in out.reasons)
    )
    if only_taken:
        req.review_reasons = []
        return _auto_complete(db, req, x, patient, out, llm, actor, offer_only=True)
    if out.reasons:
        return _route_to_review(db, req, x, patient, out, llm, actor)
    return _auto_complete(db, req, x, patient, out, llm, actor)


def _check_chart(db: Session, req: PortalRequest, x: ExtractedRequest, patient: Patient, out: Outcome, actor: str):
    policy = patient.primary_policy
    book_anyway = get_settings().book_without_insurance
    if x.intent in {"new_appointment", "reschedule"}:
        if policy is None:
            if book_anyway:
                out.notes.append("NO_INSURANCE_ON_FILE")  # booked anyway; the insurance is added before the visit
            else:
                out.add("NO_INSURANCE_ON_FILE")
        if x.insurance.member_id and policy and normalize_member_id(policy.member_id) != x.insurance.member_id:
            out.add("CONFLICTING_INFO", f"Member ID in email ({x.insurance.member_id}) differs from chart.")
        email_payer = _resolve_payer(db, x.insurance.payer_name)
        if x.insurance.payer_name and policy and (email_payer is None or email_payer.id != policy.payer_id):
            out.add("CONFLICTING_INFO", f"Insurance in email ({x.insurance.payer_name}) differs from chart "
                                        f"({policy.payer.name}).")

    # Provider: explicit request > patient's preferred > anyone in network with the payer.
    if x.appointment.provider_name:
        out.provider = _resolve_provider(db, x.appointment.provider_name)
        if out.provider is None:
            out.add("PROVIDER_NOT_FOUND", f"Requested: {x.appointment.provider_name}.")
    if out.provider is None and patient.preferred_provider_id and not x.appointment.provider_name:
        out.provider = db.get(Provider, patient.preferred_provider_id)

    # Existing appointment for reschedule / cancel.
    if x.intent in {"reschedule", "cancel"}:
        upcoming = scheduling.upcoming_for_patient(db, patient.id)
        cur = parse_date(x.appointment.current_appointment_date)
        if cur:
            upcoming = [a for a in upcoming if a.start_at.date() == cur]
        if len(upcoming) == 1:
            out.target_appt = upcoming[0]
            if out.provider is None:
                out.provider = out.target_appt.provider
        else:
            out.add("APPOINTMENT_NOT_FOUND", f"Found {len(upcoming)} matching upcoming appointments.")

    if x.intent not in {"new_appointment", "reschedule"}:
        return
    if policy is None:
        if book_anyway:
            _find_slots_without_payer(db, x, out)
        return

    # Eligibility + network. Checks cover the provider we would book with.
    if out.provider is None:
        out.in_network_ids = list(db.scalars(
            select(ProviderNetwork.provider_id).join(Provider)
            .where(ProviderNetwork.payer_id == policy.payer_id, ProviderNetwork.in_network, Provider.is_active)
        ))
    elif out.provider:
        out.in_network_ids = [out.provider.id]
    preferred = [d for d in (parse_date(s) for s in x.appointment.preferred_dates) if d]
    service_date = preferred[0] if preferred else scheduling.clinic_now().date()
    check_provider = out.provider or (db.get(Provider, out.in_network_ids[0]) if out.in_network_ids else None)
    out.eligibility = eligibility.run_eligibility(
        db, patient, policy, check_provider, request_id=req.id, actor=actor, service_date=service_date
    )
    if out.eligibility.coverage_status != "active":
        out.add("ELIGIBILITY_FAILED", f"Status: {out.eligibility.coverage_status}. {out.eligibility.message or ''}".strip())
    if out.provider and out.eligibility.in_network is False:
        out.add("OUT_OF_NETWORK", f"{out.provider.full_name} / {policy.payer.name}.")
    if not out.in_network_ids and out.provider is None:
        out.add("OUT_OF_NETWORK", f"No active provider is in network with {policy.payer.name}.")

    if out.in_network_ids:
        out.slots, out.slots_match_preference = scheduling.find_slots(
            db, out.in_network_ids, preferred_dates=preferred, time_of_day=x.appointment.preferred_time_of_day,
            exact_time=draft_chart._exact_time(x.appointment.preferred_time),
        )
        if not out.slots:
            out.add("NO_AVAILABILITY")
        elif x.appointment.preferred_time and preferred:
            # asked for an exact time: never silently book a different one
            want = draft_chart._exact_time(x.appointment.preferred_time)
            first = out.slots[0][1]
            if not (first.date() in preferred and first.time() == want):
                out.add("REQUESTED_TIME_UNAVAILABLE", f"Asked for {x.appointment.preferred_time} on "
                                                      f"{', '.join(d.isoformat() for d in preferred[:2])}.")
        elif preferred and not out.slots_match_preference:
            # The days asked for have no opening for this doctor: offer the nearest, do not book one by itself.
            out.add("REQUESTED_TIME_UNAVAILABLE", "No opening on the day(s) asked for "
                                                  f"({', '.join(d.isoformat() for d in preferred[:2])}); "
                                                  "nearest openings are offered.")


def _find_slots_without_payer(db: Session, x: ExtractedRequest, out: Outcome) -> None:
    """Open slots for a patient with no insurance on file: the requested doctor, or any active one."""
    ids = [out.provider.id] if out.provider else list(db.scalars(select(Provider.id).where(Provider.is_active)))
    out.in_network_ids = ids
    preferred = [d for d in (parse_date(s) for s in x.appointment.preferred_dates) if d]
    out.slots, out.slots_match_preference = scheduling.find_slots(
        db, ids, preferred_dates=preferred, time_of_day=x.appointment.preferred_time_of_day,
        exact_time=draft_chart._exact_time(x.appointment.preferred_time),
    )
    if not out.slots:
        out.add("NO_AVAILABILITY")
    elif x.appointment.preferred_time and preferred:
        want = draft_chart._exact_time(x.appointment.preferred_time)
        first = out.slots[0][1]
        if not (first.date() in preferred and first.time() == want):
            out.add("REQUESTED_TIME_UNAVAILABLE", f"Asked for {x.appointment.preferred_time} on "
                                                  f"{', '.join(d.isoformat() for d in preferred[:2])}.")
    elif preferred and not out.slots_match_preference:
        # The days asked for have no opening for this doctor: offer the nearest, do not book one by itself.
        out.add("REQUESTED_TIME_UNAVAILABLE", "No opening on the day(s) asked for "
                                              f"({', '.join(d.isoformat() for d in preferred[:2])}); nearest openings are offered.")


# --------------------------------------------------------------------------- outcomes

def _slot_facts(db: Session, slots) -> str:
    lines = []
    for i, (pid, start) in enumerate(slots, 1):
        lines.append(f"  {i}. {scheduling.fmt_slot(start)} with {db.get(Provider, pid).full_name}")
    return "\n".join(lines)


def _asked_when(x: ExtractedRequest) -> str:
    """"on Monday, October 19 at 9:15 AM" for the day and time the patient asked for (whatever was said)."""
    day = ""
    try:
        d = datetime.fromisoformat(x.appointment.preferred_dates[0]).date()
        day = f"on {d:%A, %B} {d.day}"
    except (IndexError, ValueError, TypeError):
        pass
    at = ""
    try:
        t = datetime.strptime(x.appointment.preferred_time or "", "%H:%M")
        at = f"at {t:%I:%M %p}".replace(" 0", " ")
    except ValueError:
        pass
    return " ".join(part for part in (day, at) if part) or "at that time"


def _auto_complete(db, req, x: ExtractedRequest, patient: Patient, out: Outcome, llm, actor, *, offer_only: bool = False) -> PortalRequest:
    settings = get_settings()
    facts = {"first_name": patient.first_name}
    kind = None
    try:
        if x.intent == "cancel":
            appt = out.target_appt
            scheduling.cancel(db, appt, actor=actor, request_id=req.id)
            facts.update(old_when=scheduling.fmt_slot(appt.start_at), provider=appt.provider.full_name)
            req.appointment_id, kind = appt.id, "cancelled"
        elif x.intent in {"new_appointment", "reschedule"}:
            if settings.auto_book_appointments and not offer_only:
                pid, start = out.slots[0]
                provider = db.get(Provider, pid)
                appt = scheduling.book(db, patient, provider, start, reason=x.appointment.visit_reason,
                                       request_id=req.id, actor=actor)
                facts.update(when=scheduling.fmt_slot(start), provider=provider.full_name, location=provider.location)
                if x.intent == "reschedule":
                    old = out.target_appt
                    scheduling.cancel(db, old, actor=actor, request_id=req.id)
                    facts["old_when"] = scheduling.fmt_slot(old.start_at)
                    kind = "rescheduled"
                else:
                    kind = "booked"
                    if out.eligibility and out.eligibility.copay:
                        facts["coverage_line"] = f"  Insurance: verified ({out.eligibility.plan_name}), " \
                                                 f"estimated copay {out.eligibility.copay}\n"
                    if "LINKED_BY_NAME" in out.notes:
                        facts["coverage_line"] = facts.get("coverage_line", "") + (
                            f"  Booked under the chart we have for {patient.first_name} {patient.last_name}. If that is not "
                            "you, or any of your details have changed, please reply and let us know.\n")
                    if "NO_INSURANCE_ON_FILE" in out.notes and not (out.eligibility and out.eligibility.copay):
                        facts["coverage_line"] = ("  Insurance: none on file yet. Please bring your insurance card "
                                                  "or reply with your member ID so we can add it before your visit.\n")
                req.appointment_id, req.provider_id = appt.id, provider.id
            else:
                facts.update(slot_list=_slot_facts(db, out.slots))
                named = {pid for pid, _ in out.slots}
                if len(named) == 1:  # one doctor's openings: say whose
                    facts["with_provider"] = f" with {db.get(Provider, next(iter(named))).full_name}"
                kind = "offer_slots"
                if offer_only:
                    # The doctor is not free at the time asked for (booked or kept free): say so plainly and ask to reschedule.
                    doctor = out.provider or db.get(Provider, next(iter(named)))
                    facts.update(provider_name=doctor.full_name, with_provider="", asked_when=_asked_when(x))
                    kind = "unavailable"
    except scheduling.SchedulingError as exc:
        out.add("SCHEDULING_ERROR", str(exc))
        req.review_reasons = out.reasons
        return _route_to_review(db, req, x, patient, out, llm, actor)

    subject, body = responder.compose(kind, facts, llm, reference=req.reference)
    req.draft_response = body
    req.decision = "auto_respond"
    to = req.source_email.from_address if req.source_email else patient.email
    if settings.auto_send_responses and to:
        email_gateway.send_reply(db, req, to_address=to, subject=subject, body=body, actor="ai-assistant")
    done = kind in {"booked", "rescheduled", "cancelled"}
    req.status = (RequestStatus.RESOLVED if done else RequestStatus.AUTO_RESPONDED).value
    if done:
        req.resolved_at = datetime.now(timezone.utc)
        req.resolved_by = "ai-assistant"
        req.resolution_note = f"Auto-resolved: appointment {kind}." + (
            " No insurance is on file: add it before the visit." if "NO_INSURANCE_ON_FILE" in out.notes else "")
    audit.record(db, "request", req.id, "auto_responded", actor, outcome=kind, appointment_id=req.appointment_id,
                 status=req.status, notes=out.notes or None)
    return req


def _route_to_review(db, req, x: ExtractedRequest, patient: Patient | None, out: Outcome, llm, actor) -> PortalRequest:
    req.decision = "manual_review"
    req.status = RequestStatus.NEEDS_REVIEW.value
    req.resolved_at = None
    first = patient.first_name if patient else (x.patient.first_name or "there")
    kind = "red_flag" if any(r["code"] == "CLINICAL_RED_FLAG" for r in out.reasons) else "review_hold"
    facts: dict = {"first_name": first}
    if x.intent in {"billing_records", "general_question", "other"} and kind == "review_hold":
        kind = "not_scheduling"
        sender = (req.source_email.from_name if req.source_email and req.source_email.from_name else "").split()
        facts = {"first_name": sender[0] if sender else "there",
                 "topic_line": {"billing_records": "a bill or records document ",
                                "general_question": "a question ", "other": "a message "}.get(x.intent, "")}
    # A new patient with no chart: ask for what the email did not give, instead of a plain "we are reviewing".
    if kind == "review_hold" and patient is None and x.intent == "new_appointment" and any(
        r["code"] == "UNMATCHED_PATIENT" for r in out.reasons
    ):
        needed = details_request_facts(db, req, x)
        if needed:
            kind, facts = "request_details", needed
    # When slots were found, give staff a ready-to-send offer instead of a holding reply.
    if out.slots and kind == "review_hold" and all(
        r["code"] in {"LOW_CONFIDENCE", "CONFLICTING_INFO", "INSURANCE_CHANGE", "REQUESTED_TIME_UNAVAILABLE"}
        for r in out.reasons
    ):
        kind = "offer_slots"
        facts["slot_list"] = _slot_facts(db, out.slots)
    _, body = responder.compose(kind, facts, None, reference=req.reference)
    req.draft_response = body
    audit.record(db, "request", req.id, "routed_to_review", actor, reasons=[r["code"] for r in out.reasons],
                 suggested_slots=[(pid, s.isoformat()) for pid, s in out.slots])
    return req


def reopen(db: Session, req: PortalRequest, actor: str) -> None:
    req.status = RequestStatus.NEEDS_REVIEW.value
    req.resolved_at = None
    audit.record(db, "request", req.id, "request_reopened", actor)


def dashboard_counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(PortalRequest.status, func.count()).group_by(PortalRequest.status)).all()
    return {status: count for status, count in rows}
