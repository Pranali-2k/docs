"""Patient-facing reply composition.

Auto-sent replies are built from vetted templates filled with facts from the
database. When a remote LLM is configured, it may rephrase the template. The
rewrite is accepted only if every fact (dates, times, provider, reference)
survives verbatim, so the model can never change what the patient is told.
"""
from __future__ import annotations

from app.config import get_settings
from app.llm.providers import LLMError, LLMProvider

SIGNOFF = "\n\nWarm regards,\n{clinic} Scheduling Team\n\nReference: {reference}\n" \
          "If this is a medical emergency, call 911. Please do not reply with sensitive medical details."

TEMPLATES = {
    "booked": (
        "Hello {first_name},\n\nThank you for contacting {clinic}. Your appointment is confirmed:\n\n"
        "  Provider: {provider}\n  Date/time: {when}\n  Location: {location}\n{coverage_line}\n"
        "Please arrive 15 minutes early and bring your photo ID and insurance card. "
        "If you need to change this appointment, just reply to this email."
    ),
    "rescheduled": (
        "Hello {first_name},\n\nYour appointment has been rescheduled.\n\n"
        "  Previous: {old_when}\n  New: {when} with {provider}\n  Location: {location}\n\n"
        "Reply to this email if you need anything else."
    ),
    "cancelled": (
        "Hello {first_name},\n\nYour appointment on {old_when} with {provider} has been cancelled as requested. "
        "When you're ready to book again, just reply to this email."
    ),
    "offer_slots": (
        "Hello {first_name},\n\nThank you for contacting {clinic}. We have the following openings{with_provider}:\n\n"
        "{slot_list}\n\nPlease reply with the option that works best for you and we will confirm it."
    ),
    "unavailable": (
        "Hello {first_name},\n\nThank you for contacting {clinic}. Unfortunately {provider_name} is not available "
        "{asked_when}. Please reschedule your appointment for one of the nearest openings{with_provider}:\n\n"
        "{slot_list}\n\nJust reply with the option that works best for you and we will book it."
    ),
    "not_scheduling": (
        "Hello {first_name},\n\nThank you for your message. This mailbox handles appointment scheduling. "
        "We received {topic_line}and have passed it to the team that can help; they will reply to you directly "
        "if anything more is needed.\n\nIf you meant to book, change or cancel an appointment, please reply "
        "with the patient's full name and date of birth and what you need."
    ),
    "request_details": (
        "Hello {first_name},\n\nThank you for contacting {clinic}. We would be glad to see you as a new patient. "
        "To set up your chart and hold your appointment, please reply to this email with:\n\n{details_list}\n\n"
        "{slot_line}We will confirm your appointment as soon as we have these details."
    ),
    "review_hold": (
        "Hello {first_name},\n\nThank you for contacting {clinic}. We received your request and a member of our "
        "scheduling team is reviewing it. We will get back to you within one business day."
    ),
    "red_flag": (
        "Hello {first_name},\n\nWe received your message. If you are experiencing a medical emergency, such as chest "
        "pain or difficulty breathing, please call 911 or go to the nearest emergency room now. A member of our team "
        "will contact you as soon as possible."
    ),
}


def compose(kind: str, facts: dict, llm: LLMProvider | None = None, *, reference: str) -> tuple[str, str]:
    """Return (subject, body)."""
    settings = get_settings()
    facts = {"clinic": settings.clinic_name, "first_name": "there", "coverage_line": "", "with_provider": "",
             "location": "Main Clinic", **{k: v for k, v in facts.items() if v is not None}}
    body = TEMPLATES[kind].format(**facts)
    subjects = {
        "booked": "Your appointment is confirmed",
        "rescheduled": "Your appointment has been rescheduled",
        "cancelled": "Your appointment has been cancelled",
        "offer_slots": "Appointment options",
        "unavailable": "Your doctor is not available then - please reschedule",
        "not_scheduling": "We received your message",
        "request_details": "A few details to set up your appointment",
        "review_hold": "We received your request",
        "red_flag": "We received your message - important",
    }
    if llm and not llm.is_offline and kind in {"booked", "rescheduled", "cancelled", "offer_slots"}:
        body = _polish(llm, body, facts, kind)
    subject = f"{subjects[kind]} [{reference}]"
    return subject, body + SIGNOFF.format(clinic=settings.clinic_name, reference=reference)


def _polish(llm: LLMProvider, body: str, facts: dict, kind: str) -> str:
    must_keep = [str(facts[k]) for k in ("when", "old_when", "provider", "slot_list") if facts.get(k)]
    if kind == "offer_slots" and facts.get("slot_list"):
        must_keep = [line.strip() for line in facts["slot_list"].splitlines() if line.strip()]
    system = (
        "You polish short emails from a medical scheduling office. Keep it warm, clear and brief. "
        "Do not add, remove or change any dates, times, names, locations, prices or instructions. "
        "Do not give medical advice. Return only the email body with no subject and no signature."
    )
    try:
        text = llm.complete(system, body).strip()
    except LLMError:
        return body
    if text and all(fact in text for fact in must_keep) and len(text) < 3 * len(body):
        return text
    return body
