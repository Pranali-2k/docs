"""Plain-language descriptions for audit-trail entries.

    "patient #26 · chart updated · change=appointment_cancelled appointment_id=26"
becomes
    "Cancelled the appointment on Monday, October 12, 2026 at 1:30 PM with Dr. Anita Rao."

`annotate(db, entries)` serializes entries with `description` and `record_label` (e.g. "Avery Brooks · MRN DH629913",
"DH-261008-4AB1"). Names, dates and references are looked up in one batch per table, not per row. Descriptions
never raise: an unknown action or unexpected details falls back to readable text built from the action name.
The raw details stay available on each entry (`details`) for the technical view.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appointment, AuditLog, Patient, PortalRequest, Provider

log = logging.getLogger("docshealth.audit_text")

FIELD_LABELS = {
    "first_name": "first name", "last_name": "last name", "middle_name": "middle name", "preferred_name": "preferred name",
    "date_of_birth": "date of birth", "marital_status": "marital status", "sex": "sex", "phone": "phone",
    "home_phone": "home phone", "work_phone": "work phone", "email": "email", "address_line1": "street address",
    "address_line2": "address line 2", "city": "city", "state": "state", "postal_code": "ZIP code",
    "relationship_to_patient": "relationship to patient", "copay_amount": "copay", "deductible_amount": "deductible",
    "coinsurance_pct": "coinsurance", "group_number": "group number", "plan_name": "plan name",
    "skip_sender_domains": "skipped senders", "process_inbound": "process incoming mail with AI",
    "reply_from_same_mailbox": "reply from the same mailbox", "mark_as_read": "mark processed mail as read",
    "is_default_sender": "default sender", "enabled": "enabled", "folder": "folder", "display_name": "display name",
    "notes": "notes", "preferred_contact_method": "preferred contact method", "is_test_patient": "test patient",
}
DOC_KINDS = {"photo_id": "photo ID", "insurance_card_front": "insurance card (front)",
             "insurance_card_back": "insurance card (back)", "referral": "referral", "other": "document"}
SEND_STATUS = {
    "sent": "emailed", "held": "held, not emailed (outbound email is off)", "stored": "saved, not emailed (no email delivery set up)",
    "failed": "could not be delivered", "queued": "queued", "awaiting_approval": "waiting for approval (test mode)",
    "test_sent": "sent to the tester only (test mode)",
}
SOURCES = {"graph": "Microsoft 365 mailbox", "manual": "manual intake", "imap": "IMAP mailbox", "portal": "the portal",
           "demo": "demo lab"}
COVERAGE = {"active": "active", "inactive": "inactive", "unknown": "could not be confirmed", "error": "check failed"}


def _words(code: str) -> str:
    return (code or "").replace("_", " ").strip()


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else text + "."


def _val(v) -> str:
    if v is None or v == "" or v == "None":
        return "(empty)"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v) or "(empty)"
    return str(v)


def _last4(value) -> str:
    v = str(value or "")
    return f"ending {v[-4:]}" if len(v) >= 4 else "on file"


def _slot(start) -> str:
    """'2026-10-12T13:30:00' -> 'Monday, October 12, 2026 at 1:30 PM'."""
    try:
        dt = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
        return f"{dt:%A, %B} {dt.day}, {dt.year} at {dt.hour % 12 or 12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"
    except (TypeError, ValueError):
        return str(start)


def _changes(changes, label_map=FIELD_LABELS, limit: int = 4) -> str:
    """{'phone': {'from': None, 'to': '555'}} -> 'phone: (empty) → 555; email: …'"""
    if not isinstance(changes, dict) or not changes:
        return ""
    parts = []
    for field, ch in list(changes.items())[:limit]:
        label = label_map.get(field, _words(field))
        if isinstance(ch, dict) and ("from" in ch or "to" in ch):
            parts.append(f"{label}: {_val(ch.get('from'))} → {_val(ch.get('to'))}")
        else:
            parts.append(f"{label} → {_val(ch)}")
    more = len(changes) - limit
    return "; ".join(parts) + (f"; and {more} more" if more > 0 else "")


class Lookup:
    """Names/dates for the ids mentioned by a batch of audit entries (one query per table)."""

    def __init__(self, db: Session, entries: list[AuditLog]):
        pat, appt, prov, req = set(), set(), set(), set()
        for a in entries:
            d = a.details if isinstance(a.details, dict) else {}
            if a.entity_type == "patient" and a.entity_id:
                pat.add(a.entity_id)
            if a.entity_type == "request" and a.entity_id:
                req.add(a.entity_id)
            if a.entity_type == "appointment" and a.entity_id:
                appt.add(a.entity_id)
            for key, bucket in (("patient_id", pat), ("appointment_id", appt), ("provider_id", prov), ("request_id", req)):
                v = d.get(key)
                if isinstance(v, int):
                    bucket.add(v)
        self.patients = {p.id: p for p in db.scalars(select(Patient).where(Patient.id.in_(pat)))} if pat else {}
        self.appointments = ({a.id: a for a in db.scalars(select(Appointment).where(Appointment.id.in_(appt)))}
                             if appt else {})
        prov |= {a.provider_id for a in self.appointments.values()}
        self.providers = {p.id: p for p in db.scalars(select(Provider).where(Provider.id.in_(prov)))} if prov else {}
        self.requests = ({r.id: r.reference for r in db.scalars(select(PortalRequest).where(PortalRequest.id.in_(req)))}
                         if req else {})

    def patient(self, pid) -> str:
        p = self.patients.get(pid)
        return f"{p.first_name} {p.last_name} (MRN {p.mrn})" if p else (f"patient #{pid}" if pid else "the patient")

    def provider(self, pid) -> str:
        p = self.providers.get(pid)
        return p.full_name if p else ""

    def appointment(self, aid, fallback_start=None) -> str:
        """'Monday, October 12, 2026 at 1:30 PM with Dr. Anita Rao'"""
        a = self.appointments.get(aid)
        start = a.start_at if a else fallback_start
        who = self.provider(a.provider_id) if a else ""
        slot = _slot(start) if start else "an appointment"
        return f"{slot} with {who}" if who else slot

    def request(self, rid) -> str:
        return self.requests.get(rid) or (f"request #{rid}" if rid else "")


# --------------------------------------------------------------------------- describers (action -> sentence)

def _d_chart_updated(a, d, lk):
    change, appt = d.get("change"), lk.appointment(d.get("appointment_id"))
    if change == "appointment_booked":
        return f"Appointment booked and added to the chart: {appt}"
    if change == "appointment_cancelled":
        return f"Appointment cancelled on the chart: {appt}"
    return f"Chart updated ({_words(str(change))})" if change else "Chart updated"


def _d_routed(a, d, lk):
    reasons = [_words(r).capitalize() for r in d.get("reasons") or []]
    slots = len(d.get("suggested_slots") or [])
    tail = f" {slots} open slot(s) were suggested." if slots else ""
    return f"Sent to manual review: {', '.join(reasons) or 'needs a staff decision'}.{tail}"


def _d_auto(a, d, lk):
    what = {"booked": "booked the appointment", "rescheduled": "rescheduled the appointment",
            "cancelled": "cancelled the appointment", "offer_slots": "offered the patient open times"}
    appt = f" ({lk.appointment(d.get('appointment_id'))})" if d.get("appointment_id") else ""
    return f"The AI handled this automatically and {what.get(d.get('outcome'), _words(str(d.get('outcome'))))}{appt}"


def _d_response(a, d, lk):
    status = SEND_STATUS.get(d.get("send_status"), _words(str(d.get("send_status"))))
    method = f" via {d['method']}" if d.get("method") and d.get("send_status") == "sent" else ""
    return f"Reply to {d.get('to') or 'the sender'}: {status}{method}"


def _d_bulk(verb):
    def f(a, d, lk):
        parts = [f"{d.get('done', 0)} done"]
        if d.get("skipped"):
            parts.append(f"{d['skipped']} skipped")
        if d.get("failed"):
            parts.append(f"{d['failed']} failed")
        note = f' Note: "{d["note"]}"' if d.get("note") else ""
        return f"Bulk action, {verb} {d.get('requested', '')} request(s): {', '.join(parts)}.{note}"
    return f


def _d_draft_prepared(a, d, lk):
    bits = []
    if d.get("missing"):
        bits.append("missing " + ", ".join(_words(m) for m in d["missing"]))
    if d.get("duplicates"):
        bits.append(f"{d['duplicates']} possible duplicate chart(s)")
    if d.get("proposed"):
        bits.append(f"proposed slot {_slot(d['proposed'])}")
    return "The AI prepared a draft chart for staff approval" + (f" ({'; '.join(bits)})" if bits else "")


def _d_eligibility(a, d, lk):
    net = {True: "in network", False: "out of network"}.get(d.get("in_network"))
    disc = d.get("discrepancies")
    n = len(disc) if isinstance(disc, (list, tuple)) else (disc if isinstance(disc, int) else 0)
    prov = lk.provider(d.get("provider_id"))
    return ("Checked insurance eligibility" + (f" with {prov}" if prov else "") +
            f": coverage {COVERAGE.get(d.get('coverage_status'), _words(str(d.get('coverage_status'))))}"
            + (f", {net}" if net else "") + (f", {n} discrepancy(ies) found" if n else ""))


def _d_polled(a, d, lk):
    bits = [f"{len(d['created']) if isinstance(d.get('created'), list) else d.get('created', 0)} new request(s)"]
    if d.get("skipped"):
        extra = f" ({d['skipped_internal']} internal)" if d.get("skipped_internal") else ""
        bits.append(f"{d['skipped']} skipped{extra}")
    if d.get("failed"):
        bits.append(f"{d['failed']} failed")
    return f"Checked mailbox {d.get('address', '')}: {', '.join(bits)}"


DESCRIBE = {
    # appointments / charts
    "appointment_booked": lambda a, d, lk: (f"Booked {lk.appointment(a.entity_id, d.get('start'))} for "
                                            f"{lk.patient(d.get('patient_id'))}"
                                            + (f" from {lk.request(d['request_id'])}" if d.get("request_id") else "")),
    "appointment_cancelled": lambda a, d, lk: (f"Cancelled the {_slot(d.get('start'))} appointment for "
                                               f"{lk.patient(d.get('patient_id'))}"
                                               + (f" ({lk.request(d['request_id'])})" if d.get("request_id") else "")),
    "time_blocked": lambda a, d, lk: (f"Blocked provider time from {_slot(d.get('start'))} to {_slot(d.get('end'))}"
                                      + (f" ({d['reason']})" if d.get("reason") else "")),
    "time_unblocked": lambda a, d, lk: f"Freed provider time from {_slot(d.get('start'))} to {_slot(d.get('end'))}",
    "chart_updated": _d_chart_updated,
    "patient_registered": lambda a, d, lk: f"Registered a new patient chart (MRN {d.get('mrn', '')})",
    "demographics_updated": lambda a, d, lk: "Updated patient details: " + (_changes(d.get("changes")) or "no visible change"),
    "guarantor_updated": lambda a, d, lk: "Updated the guarantor: " + (_changes(d.get("changes")) or "no visible change"),
    "insurance_added": lambda a, d, lk: (f"Added {d.get('coverage_order') or ''} insurance: {d.get('payer', 'payer')}, "
                                         f"member ID {_last4(d.get('member_id'))}").replace("  ", " "),
    "insurance_updated": lambda a, d, lk: "Updated insurance: " + (_changes(d.get("changes")) or "no visible change"),
    "insurance_archived": lambda a, d, lk: "Archived an insurance policy (kept in history)",
    "policy_created": lambda a, d, lk: f"Created a {d.get('payer', '')} policy, member ID {_last4(d.get('member_id'))}",
    "policy_updated": lambda a, d, lk: "Updated an insurance policy: " + (_changes(d.get("changes")) or "no visible change"),
    "policy_archived": lambda a, d, lk: "Archived an insurance policy (kept in history)",
    "eligibility_checked": _d_eligibility,
    "eligibility_value_selected": lambda a, d, lk: (
        f"Copied {FIELD_LABELS.get(d.get('field'), _words(str(d.get('field'))))} {_val(d.get('new'))} from the insurance "
        f"response onto the policy (was {_val(d.get('old'))})"),
    "phi_release_added": lambda a, d, lk: f"Added a release of information for {d.get('recipient', 'a recipient')}",
    "phi_release_revoked": lambda a, d, lk: f"Revoked the release of information for {d.get('recipient', 'a recipient')}",
    "document_uploaded": lambda a, d, lk: f"Uploaded a {DOC_KINDS.get(d.get('kind'), 'document')} to the chart",
    "sensitive_identifier_viewed": lambda a, d, lk: (
        f"Viewed the patient's {'Social Security number' if d.get('field') == 'ssn' else _words(str(d.get('field')))}"
        + (f" (reason: {d['reason']})" if d.get("reason") else "")),
    # requests
    "request_created": lambda a, d, lk: (f"Received an email from {d.get('from_address', 'unknown')} via "
                                         f"{SOURCES.get(d.get('source'), d.get('source') or 'email')}: "
                                         f"“{(d.get('subject') or '(no subject)').strip()[:90]}”"),
    "processing_started": lambda a, d, lk: f"Started AI processing ({d.get('llm') or 'rules engine'})",
    "ai_extracted": lambda a, d, lk: (
        f"The AI read the email ({d.get('engine') or 'rules'}): {_words(str(d.get('intent')))}, "
        f"{round(float(d.get('confidence') or 0) * 100)}% confident"
        + (f". AI error: {str(d['error'])[:100]}" if d.get("error") else "")),
    "patient_matched": lambda a, d, lk: (f"Matched the sender to {lk.patient(d.get('patient_id'))} "
                                         f"({round(float(d.get('confidence') or 0) * 100)}% confidence)"),
    "patient_match_failed": lambda a, d, lk: (
        "Several charts could match; staff must choose one" if d.get("status") == "ambiguous"
        else "No existing patient chart matches the details in the email"),
    "patient_match_kept": lambda a, d, lk: f"Kept the chart staff chose: {lk.patient(d.get('patient_id'))}",
    "patient_linked": lambda a, d, lk: f"Staff linked the request to chart {d.get('mrn') or lk.patient(d.get('patient_id'))}",
    "draft_chart_prepared": _d_draft_prepared,
    "draft_chart_approved": lambda a, d, lk: (
        f"Staff approved the draft chart: created chart {d.get('mrn', '')}"
        + (f" and booked {lk.appointment(d['appointment_id'])}" if d.get("appointment_id") else " (no appointment booked)")),
    "draft_chart_discarded": lambda a, d, lk: "Staff discarded the draft chart" + (f" (reason: {d['reason']})" if d.get("reason") else ""),
    "routed_to_review": _d_routed,
    "auto_responded": _d_auto,
    "response_sent": _d_response,
    "request_resolved": lambda a, d, lk: "Marked as resolved" + (f": {d['note']}" if d.get("note") else ""),
    "request_closed": lambda a, d, lk: "Closed, no action needed" + (f": {d['note']}" if d.get("note") else ""),
    "request_reopened": lambda a, d, lk: "Reopened for review",
    "assigned": lambda a, d, lk: f"Assigned to {d['assignee']}" if d.get("assignee") else "Unassigned",
    "bulk_close": _d_bulk("closing"), "bulk_resolve": _d_bulk("resolving"), "bulk_reprocess": _d_bulk("re-running the AI on"),
    # email
    "email_resent": lambda a, d, lk: f"Retried sending an email: {SEND_STATUS.get(d.get('send_status'), _words(str(d.get('send_status'))))}",
    "test_send_approved": lambda a, d, lk: (
        f"Approved a test send: delivered to {d.get('delivered_to')} only; {d.get('real_recipient')} was not emailed "
        f"({SEND_STATUS.get(d.get('send_status'), _words(str(d.get('send_status'))))})"),
    "email_body_cleaned": lambda a, d, lk: (f"Converted the email from HTML to plain text "
                                            f"({d.get('chars_before')} → {d.get('chars_after')} characters)"),
    # integrations / system
    "microsoft_configured": lambda a, d, lk: ("Saved the Microsoft 365 app details" + (" (new secret)" if d.get("secret_changed") else "")
                                              + f", {d.get('auth_mode', '')} mode").rstrip(", "),
    "microsoft_connected": lambda a, d, lk: f"Connected Microsoft 365 ({d.get('auth_mode', '')} mode)",
    "microsoft_disconnected": lambda a, d, lk: "Disconnected Microsoft 365",
    "microsoft_tested": lambda a, d, lk: "Tested the Microsoft 365 connection" + (" (ok)" if d.get("ok") else ""),
    "mailbox_added": lambda a, d, lk: f"Added mailbox {d.get('address', '')} ({d.get('folder', 'Inbox')})",
    "mailbox_removed": lambda a, d, lk: f"Removed mailbox {d.get('address', '')}",
    "mailbox_updated": lambda a, d, lk: "Changed mailbox settings: " + (_changes(d.get("changes")) or "no visible change"),
    "mailbox_polled": _d_polled,
    "outbound_email_enabled": lambda a, d, lk: "Turned outbound email ON" + (f" ({d['reason']})" if d.get("reason") else ""),
    "outbound_email_disabled": lambda a, d, lk: "Turned outbound email OFF" + (f" ({d['reason']})" if d.get("reason") else ""),
    "outbound_test_mode_on": lambda a, d, lk: f"Turned test mode ON: replies wait for approval and go only to {d.get('recipient')}",
    "outbound_test_mode_off": lambda a, d, lk: "Turned test mode OFF",
    "email_settings_saved": lambda a, d, lk: f"Saved email delivery settings ({d.get('method', '')})",
    "email_test_sent": lambda a, d, lk: f"Sent a test email to {d.get('to')}: " + ("delivered" if d.get("ok") else "failed"),
    "smtp_test_email": lambda a, d, lk: (f"Sent a test email to {d.get('to')} via {d.get('host')}: "
                                         + ("delivered" if d.get("ok") else f"failed ({_words(str(d.get('error_kind')))})")),
    "inbound_mail_test": lambda a, d, lk: f"Tested reading mail ({d.get('method', '')}): " + ("ok" if d.get("ok") else "failed"),
    # users
    "login_succeeded": lambda a, d, lk: f"Signed in{' from ' + d['ip'] if d.get('ip') else ''}",
    "login_failed": lambda a, d, lk: f"Failed sign-in attempt{' from ' + d['ip'] if d.get('ip') else ''}",
    "login_locked": lambda a, d, lk: "Account locked after repeated failed sign-ins" + (f" (from {d['ip']})" if d.get("ip") else ""),
    "password_reset_requested": lambda a, d, lk: (f"Requested a password reset; the email was "
                                                  f"{SEND_STATUS.get(d.get('send_status'), _words(str(d.get('send_status'))))}"),
    "password_reset_requested_unknown": lambda a, d, lk: "Password reset requested for an unknown address (no email sent)",
    "password_reset_throttled": lambda a, d, lk: "Password reset requests blocked: too many attempts",
    "password_reset_completed": lambda a, d, lk: "Password changed using a reset link",
    "user_renamed": lambda a, d, lk: f"Display name changed from “{d.get('from')}” to “{d.get('to')}”",
}


def _fallback(a: AuditLog, d: dict) -> str:
    shown = [f"{_words(k)}: {_val(v)}" for k, v in d.items() if v not in (None, "", [], {})]
    return _words(a.action).capitalize() + (f" ({'; '.join(shown)[:200]})" if shown else "")


def describe(a: AuditLog, lk: Lookup) -> str:
    d = a.details if isinstance(a.details, dict) else {}
    try:
        fn = DESCRIBE.get(a.action)
        return _sentence(fn(a, d, lk)) if fn else _sentence(_fallback(a, d))
    except Exception:  # a description must never break the audit trail
        log.warning("could not describe audit entry %s (%s)", a.id, a.action, exc_info=True)
        return _sentence(_fallback(a, d if isinstance(d, dict) else {}))


def record_label(a: AuditLog, lk: Lookup) -> str:
    if a.entity_type == "patient" and a.entity_id:
        p = lk.patients.get(a.entity_id)
        return f"{p.first_name} {p.last_name} · MRN {p.mrn}" if p else f"Patient #{a.entity_id}"
    if a.entity_type == "request" and a.entity_id:
        return lk.requests.get(a.entity_id) or f"Request #{a.entity_id}"
    if a.entity_type == "appointment" and a.entity_id:
        return f"Appointment #{a.entity_id}"
    return {"system": "System", "integration": "Microsoft 365", "user": "User account", "mailbox": "Mailbox",
            "email": "Email", "policy": "Insurance policy"}.get(a.entity_type, _words(a.entity_type).capitalize()) + (
        f" #{a.entity_id}" if a.entity_id and a.entity_type in {"email", "policy", "user"} else "")


def annotate(db: Session, entries: list[AuditLog]) -> list[dict]:
    """Serialize audit entries with `description` and `record_label`. Never raises."""
    from app.api.serializers import audit_entry

    try:
        lk = Lookup(db, entries)
    except Exception:
        log.warning("audit lookups failed; descriptions will omit names", exc_info=True)
        lk = Lookup(db, [])
    out = []
    for a in entries:
        row = audit_entry(a)
        row["description"] = describe(a, lk)
        row["record_label"] = record_label(a, lk)
        out.append(row)
    return out
