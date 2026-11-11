"""Email -> structured scheduling request.

There are two engines behind one function:
  * LLM extraction (OpenAI / Azure OpenAI / Gemini / Hugging Face), which uses a
    strict JSON contract and is normalized and validated afterwards;
  * a deterministic rule engine, used when LLM_PROVIDER=mock and as a fallback
    when the remote model fails. A fallback always forces manual review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel, Field

from app.llm.providers import LLMError, LLMProvider
from app.services.normalize import normalize_email, normalize_member_id, normalize_phone, parse_date

INTENTS = ["new_appointment", "reschedule", "cancel", "insurance_update", "billing_records", "general_question", "other"]


class PatientInfo(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: str | None = None  # ISO yyyy-mm-dd
    phone: str | None = None  # 10 digits
    email: str | None = None
    sex: str | None = None  # female | male | other
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None  # 2-letter
    postal_code: str | None = None
    ssn_last4: str | None = None
    # requester's contact when a family member writes for the patient (chart contact only, never used for matching)
    contact_phone: str | None = None
    contact_email: str | None = None
    requester_is_patient: bool = True
    requester_relationship: str | None = None


class InsuranceInfo(BaseModel):
    payer_name: str | None = None
    member_id: str | None = None
    group_number: str | None = None


class AppointmentInfo(BaseModel):
    provider_name: str | None = None
    visit_reason: str | None = None
    preferred_dates: list[str] = Field(default_factory=list)  # ISO dates, in priority order
    preferred_time_of_day: str = "any"  # morning | afternoon | any
    preferred_time: str | None = None  # exact requested start "HH:MM" (24h), e.g. "at 10 am" -> "10:00"
    current_appointment_date: str | None = None  # for reschedule/cancel
    is_urgent: bool = False


class ExtractedRequest(BaseModel):
    intent: str = "other"
    confidence: float = 0.0
    summary: str = ""
    patient: PatientInfo = Field(default_factory=PatientInfo)
    insurance: InsuranceInfo = Field(default_factory=InsuranceInfo)
    appointment: AppointmentInfo = Field(default_factory=AppointmentInfo)
    is_new_patient: bool | None = None
    clinical_red_flag: bool = False
    engine: str = "rules"
    engine_error: str | None = None
    classifier: str | None = None  # which classifier read the message (see email_classifier.py)


@dataclass
class ExtractionContext:
    reference_date: date
    sender_email: str | None
    sender_name: str | None
    payers: list[tuple[str, list[str]]]  # (canonical name, aliases)
    providers: list[str]  # provider full names, e.g. "Dr. Anita Rao"


# --------------------------------------------------------------------------- rules

MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_TOKEN = rf"(\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}|(?<!\d)\d{{1,2}}[-.]\d{{1,2}}[-.]\d{{2,4}}(?!\d)|{MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?)"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NAME = r"([A-Z][a-zA-Z'\-]+)\s+([A-Z][a-zA-Z'\-]+)"

RED_FLAGS = re.compile(
    r"chest pain|can'?t breathe|cannot breathe|shortness of breath|difficulty breathing|suicid|stroke|"
    r"severe bleeding|unconscious|overdose|seizure|anaphyla",
    re.I,
)
URGENT = re.compile(r"\burgent|\basap\b|as soon as possible|right away|earliest (?:available|possible)", re.I)
BILLING_RECORDS = re.compile(
    r"\b(?:attached (?:the )?(?:bill|invoice|statement)|the bill\b|bill(?:s|ing)?\b|invoice|statement of account|balance due|"
    r"payment|medical records?|records? request|authorization form|release of information|lien|letter of protection|"
    r"subpoena|case status)\b",
    re.I,
)
STRONG_SCHEDULE = re.compile(
    r"\b(?:schedul\w*|book(?:ing)?|make an appointment|set up an appointment|new patient|see dr\.?|get in to see|"
    r"appointment (?:request|for|with|on)|would like to (?:be seen|see)|need to see|can i come in)\b",
    re.I,
)


def _name_from_subject(subject: str) -> tuple[str, str] | None:
    """'Re: Alfredo Perez (DOB: 11/20/1971)' names the patient: the person writing is a different person."""
    name = r"([A-Z][a-zA-Z'\-]+)\s+([A-Z][a-zA-Z'\-]+)"
    prefix = r"^(?:\s*(?i:re|fw|fwd)\s*:\s*)*"
    text = (subject or "").strip()
    m = re.search(rf"{prefix}{name}\s*[\(\-\u2013,;:|/]+\s*(?:DOB|D\.O\.B\.?|date of birth)\b", text)
    if m is None:
        # 'Re: Sharon Sumpter; DOL: 3-30-26 // PRP': a name followed by a semicolon is the patient too
        m = re.search(rf"{prefix}{name}\s*;", text)
    return (m.group(1), m.group(2)) if m else None


INTENT_PATTERNS = {
    "cancel": re.compile(r"\bcancel", re.I),
    "reschedule": re.compile(
        r"\breschedul|\bmove my appointment|\bchange (?:my|the) appointment|\bpush (?:back )?my appointment|"
        r"\bdifferent (?:day|time)|\bmove (?:it|the appointment) to",
        re.I,
    ),
    "insurance_update": re.compile(
        r"new insurance|insurance (?:has )?changed|chang(?:e|ed|ing) (?:my )?insurance|update (?:my )?insurance|"
        r"new (?:insurance )?card|switched (?:to|insurance)",
        re.I,
    ),
    "new_appointment": re.compile(
        r"\bschedule\b|\bbook\b|make an appointment|set up an appointment|\bappointment\b|\bsee dr\.?|"
        r"get in to see|\bvisit\b|\bconsult",
        re.I,
    ),
}


def _parse_date_token(token: str, ref: date, *, allow_past: bool) -> date | None:
    has_year = bool(re.search(r"\d{4}", token)) or bool(re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2}", token.strip()))
    d = parse_date(re.sub(r"(\d)(st|nd|rd|th)", r"\1", token))
    if d is None:
        return None
    if not has_year:
        d = d.replace(year=ref.year)
        if not allow_past and d < ref:
            d = d.replace(year=ref.year + 1)
    return d


def _find_dob(text: str, ref: date) -> tuple[date | None, tuple[int, int] | None]:
    m = re.search(
        rf"(?:\bDOB\b|D\.O\.B\.?|date of birth|birth ?date|\bborn(?: on)?)\s*(?:is|:|-|=)?\s*{DATE_TOKEN}", text, re.I
    )
    if not m:
        return None, None
    d = parse_date(m.group(1))
    if d and d > ref:
        d = d.replace(year=d.year - 100)
    return d, m.span(1)


def _find_name(text: str, ctx: ExtractionContext) -> tuple[str | None, str | None, bool, str | None]:
    dependent = re.search(
        rf"(?i:for my|my)\s+(son|daughter|child|kid|mother|mom|father|dad|wife|husband|spouse|partner),?\s+{NAME}",
        text,
    )
    if dependent:
        return dependent.group(2), dependent.group(3), False, dependent.group(1).lower()
    form = re.search(rf"(?i:(?:first\s*(?:&|and)\s*last|full|patient(?:'s)?)\s*name){_SEP}{NAME}", text)
    if form:
        first, last = form.group(1), form.group(2)
        # a filled-in form from someone else (attorney, case manager...) is about the patient, not the sender
        sender = (ctx.sender_name or "").lower()
        is_self = not sender or last.lower() in sender
        return first, last, is_self, None if is_self else "third_party"
    for pattern in (
        rf"(?i:my name is|this is|i am|i'm)\s+{NAME}",
        rf"(?i:patient(?: name)?|name)\s*[:\-]\s*{NAME}",
        # website-form notifications list fields without a colon, one per line: "Name GARRETT CUTTING"
        rf"(?m)^\s*(?i:patient\s+)?(?i:name)\s+{NAME}\s*$",
    ):
        m = re.search(pattern, text)
        if m and m.group(1).lower() not in {"dr", "doctor"}:
            return m.group(1), m.group(2), True, None
    sig = re.search(rf"(?i:thanks|thank you|regards|best|sincerely|cheers)[,!.]?\s*\n+\s*{NAME}\s*$", text.strip(), re.M)
    if sig:
        return sig.group(1), sig.group(2), True, None
    if ctx.sender_name:
        parts = ctx.sender_name.replace(",", " ").split()
        if len(parts) >= 2:
            if "," in ctx.sender_name:  # "Last, First"
                return parts[1], parts[0], True, None
            return parts[0], parts[-1], True, None
    return None, None, True, None


def _find_payer(text: str, ctx: ExtractionContext) -> str | None:
    lower = text.lower()
    best: tuple[int, str] | None = None
    for canonical, aliases in ctx.payers:
        for alias in [canonical, *aliases]:
            if alias and re.search(rf"\b{re.escape(alias.lower())}\b", lower):
                if best is None or len(alias) > best[0]:
                    best = (len(alias), canonical)
    return best[1] if best else None


def _find_provider(text: str, ctx: ExtractionContext) -> str | None:
    # "Dr. Dr. Elena Torres" (a doubled title) must still read as Dr. Elena Torres.
    text = re.sub(r"\b(Dr\.?|Doctor)(?:\s+(?:Dr\.?|Doctor)(?![A-Za-z]))+", r"\1", text, flags=re.I)
    unknown = None
    for m in re.finditer(r"\b(?:Dr\.?|Doctor)\s+([A-Z][a-zA-Z'\-]+)(?:\s+([A-Z][a-zA-Z'\-]+))?", text):
        words = [t for t in m.groups() if t and t.lower().strip(".") not in {"dr", "doctor"}]
        tokens = {t.lower() for t in words}
        if not tokens:
            continue
        for full in ctx.providers:
            name_tokens = {t.lower().strip(".") for t in full.split()}
            if tokens & (name_tokens - {"dr", "doctor"}):
                return full
        if unknown is None:
            unknown = f"Dr. {' '.join(words)}"
    return unknown


def _find_reason(text: str) -> str | None:
    known = re.search(
        r"\b(annual (?:physical|exam|check-?up|wellness visit)|physical exam|physical|follow[- ]up(?: visit)?|"
        r"check-?up|wellness visit|new patient visit|consultation|vaccination|flu shot|lab work|blood work)\b",
        text,
        re.I,
    )
    complaint = re.search(r"\b(?:for|regarding|about|because of|due to)\s+(?:a |an |my |some )?"
                          r"((?:[a-z]+\s){0,3}(?:pain|rash|cough|fever|headaches?|injury|infection|allergies|"
                          r"symptoms|swelling|anxiety|diabetes|blood pressure))\b", text, re.I)
    if complaint:
        return complaint.group(1).strip().lower()
    if known:
        return known.group(1).lower()
    return None


def _find_preferred_dates(text: str, ref: date, skip: list[tuple[int, int]]) -> tuple[list[date], date | None]:
    current: date | None = None
    # "my/the appointment on 10/12" is an existing booking; "an appointment on 10/12" is a new request
    cur = re.search(rf"(?<!\ban\s)(?<!\ba\s)(?:appointment|appt)\s+(?:is\s+|was\s+)?(?:on|for|scheduled for|set for)\s+"
                    rf"(?:{DATE_TOKEN}|(\d{{1,2}}/\d{{1,2}})(?![\d/]))", text, re.I)
    if cur:
        if cur.group(1):
            current = _parse_date_token(cur.group(1), ref, allow_past=False)
        else:  # "my appointment on 10/12": month/day, next occurrence
            month, day = (int(n) for n in cur.group(2).split("/"))
            try:
                current = date(ref.year, month, day)
                if current < ref:
                    current = current.replace(year=ref.year + 1)
            except ValueError:
                current = None
        skip = [*skip, cur.span(cur.lastindex)]
    # quoted-thread headers ("Sent: Tuesday, October 6, 2026 …") are not requested dates
    skip = [*skip, *(h.span() for h in re.finditer(r"^[ \t]*(?:sent|date|received)\s*:.*$|^.{0,200}\bwrote:\s*$",
                                                   text, re.I | re.M))]
    found: list[tuple[int, date]] = []
    # month/day without a year, when it reads as a date someone wants: "schedule it for 12/21", "on 3/4"
    for m in re.finditer(r"\b(?:on|for|by|date|after|before|around)\s+(\d{1,2})/(\d{1,2})(?![\d/])", text, re.I):
        if any(s[0] <= m.start(1) < s[1] for s in skip):
            continue
        month, day = int(m.group(1)), int(m.group(2))
        try:
            d = date(ref.year, month, day)
        except ValueError:
            continue
        if d < ref:
            d = d.replace(year=ref.year + 1)
        found.append((m.start(1), d))
    for m in re.finditer(DATE_TOKEN, text, re.I):
        if any(s[0] <= m.start() < s[1] for s in skip):
            continue
        d = _parse_date_token(m.group(1), ref, allow_past=False)
        if d and d >= ref:
            found.append((m.start(), d))
    for m in re.finditer(r"\b(tomorrow|" + "|".join(WEEKDAYS) + r")s?\b", text, re.I):
        if any(s[0] <= m.start() < s[1] for s in skip):
            continue
        word = m.group(1).lower()
        if word == "tomorrow":
            d = ref + timedelta(days=1)
        else:
            delta = (WEEKDAYS.index(word) - ref.weekday()) % 7 or 7
            d = ref + timedelta(days=delta)
        if current and d == current and cur and abs(m.start() - cur.start()) < 60:
            continue
        found.append((m.start(), d))
    for m in re.finditer(r"\b(next|this|the following) week\b", text, re.I):
        monday = ref + timedelta(days=(7 - ref.weekday()) % 7 or 7)
        if m.group(1).lower() == "this":
            monday = ref + timedelta(days=1)
        for i in range(5):
            d = monday + timedelta(days=i)
            if d.weekday() < 5 and d != current:
                found.append((m.start() + i, d))
    ordered: list[date] = []
    for _, d in sorted(found):
        if d not in ordered and d != current:
            ordered.append(d)
    return ordered[:7], current


US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
_SEP = r"[ \t]*[:\-#=]?[ \t]*"  # "Label: value", "Label - value" or "Label value", on the same line
PHONE_RE = r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
EMAIL_RE = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"


def _state_code(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().strip(".")
    if len(v) == 2 and v.upper() in US_STATES.values():
        return v.upper()
    return US_STATES.get(v.lower())


def _labeled_phone(text: str) -> str | None:
    """'Phone number 435…' / 'Cell: …' beats the first number in the email (often a signature line)."""
    m = re.search(rf"\b(?:phone|cell|mobile|contact number)(?:\s*(?:number|no\.?|#))?{_SEP}({PHONE_RE})", text, re.I)
    return normalize_phone(m.group(1)) if m else None


def _labeled_email(text: str) -> str | None:
    m = re.search(rf"\b(?:e-?mail)(?:\s*address)?{_SEP}({EMAIL_RE})", text, re.I)
    return normalize_email(m.group(1)) if m else None


def _find_sex(text: str) -> str | None:
    m = re.search(rf"\b(?:gender|sex){_SEP}(female|male|woman|man|f|m|non-?binary|other)\b", text, re.I)
    if not m:
        return None
    v = m.group(1).lower()
    return {"f": "female", "woman": "female", "m": "male", "man": "male"}.get(v, "other" if "binary" in v else v)


def _find_ssn_last4(text: str) -> str | None:
    m = re.search(r"\b(?:last\s*(?:4|four)(?:\s*digits)?(?:\s*of\s*(?:your|the|his|her|their)?\s*"
                  r"(?:ssn|social(?:\s*security)?(?:\s*(?:number|#))?))?|ssn\s*(?:last\s*(?:4|four))?)"
                  rf"{_SEP}(?:x{{3}}-?x{{2}}-?)?(\d{{4}})\b", text, re.I)
    return m.group(1) if m else None


def _find_address(text: str) -> dict:
    m = re.search(
        r"\b(?:home\s+|mailing\s+|street\s+|patient\s+)?address[ \t]*[:\-]?[ \t]*(?:\n[ \t]*){0,2}"
        r"(?P<line1>\d+[A-Za-z0-9 .#'\-]{3,80}?)[ \t]*(?:,[ \t]*|\n[ \t]*)"
        r"(?P<city>[A-Za-z][A-Za-z .'\-]{1,40}?),?[ \t]+(?P<state>[A-Za-z]{2}|[A-Za-z][A-Za-z .]{2,20}?)[ \t,]+"
        r"(?P<zip>\d{5}(?:-\d{4})?)\b",
        text, re.I)
    if not m:
        return {}
    state = _state_code(m.group("state"))
    if not state:
        return {}
    return {"address_line1": m.group("line1").strip(" ,"), "city": m.group("city").strip(" ,").title(),
            "state": state, "postal_code": m.group("zip")}


def _find_time(text: str) -> str | None:
    """A specific requested start time: 'schedule it for 12/21 at 10 am' -> '10:00'. Booking-verb phrases win."""
    t = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)"
    m = (re.search(rf"\b(?:schedule|book|appointment|appt|come in|see (?:her|him|them|me))\b[^\n.]{{0,60}}?\bat\s+{t}", text, re.I)
         or re.search(rf"\bat\s+{t}", text, re.I))
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    if not (1 <= hour <= 12 and 0 <= minute < 60):
        return None
    hour = hour % 12 + (12 if ampm.startswith("p") else 0)
    return f"{hour:02d}:{minute:02d}"


def _time_of_day(text: str) -> str:
    if re.search(r"\bmorning|before noon|\b(?:[7-9]|1[01])\s?am\b|early in the day", text, re.I):
        return "morning"
    if re.search(r"afternoon|evening|after (?:lunch|work|noon)|\b(?:1[2-9]?|[1-5])\s?pm\b", text, re.I):
        return "afternoon"
    return "any"


def extract_with_rules(subject: str, body: str, ctx: ExtractionContext) -> ExtractedRequest:
    text = f"{subject}\n{body}"
    ref = ctx.reference_date

    # Intent is judged on what the person just wrote: the quoted thread and the legal footer are not theirs.
    from app.services.email_classifier import latest_message

    core = f"{subject}\n{latest_message(body)}"
    if len(core) < 40:  # a bare "Thanks": the question is in the thread
        core = text
    hits = {k: bool(p.search(core)) for k, p in INTENT_PATTERNS.items()}
    # A message about a bill or records that asks for nothing to be scheduled: "visit"/"appointment" in passing
    # does not make it a scheduling request.
    billing_only = bool(BILLING_RECORDS.search(core)) and not STRONG_SCHEDULE.search(core)
    if billing_only:
        hits["new_appointment"] = False
    if billing_only and not (hits["cancel"] or hits["reschedule"]):
        intent, conf = "billing_records", 0.85
    elif hits["cancel"] and not hits["reschedule"]:
        intent, conf = "cancel", 0.9
    elif hits["reschedule"]:
        intent, conf = "reschedule", 0.9 if not hits["cancel"] else 0.75
    elif hits["new_appointment"]:
        intent, conf = "new_appointment", 0.9
    elif hits["insurance_update"]:
        intent, conf = "insurance_update", 0.85
    elif "?" in text:
        intent, conf = "general_question", 0.6
    else:
        intent, conf = "other", 0.4

    dob, dob_span = _find_dob(text, ref)
    first, last, is_self, relationship = _find_name(body, ctx)
    subject_name = _name_from_subject(subject)
    if subject_name:  # the subject line names the patient; whoever wrote is writing about them
        sender = (ctx.sender_name or "").lower()
        if not (sender and subject_name[1].lower() in sender):
            first, last, is_self, relationship = subject_name[0], subject_name[1], False, "third_party"
    phone_m = re.search(r"(?<!\d)(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)", text)
    email_in_body = re.search(EMAIL_RE, body)
    member = re.search(
        r"(?:member|subscriber|policy|insurance)\s*(?:id|#|number|no\.?)\s*(?:is|:|#|-)?\s*#?\s*([A-Z0-9][A-Z0-9\-]{4,})",
        text,
        re.I,
    )
    group = re.search(r"\bgroup\s*(?:#|number|no\.?|id)?\s*(?:is|:|-)?\s*#?\s*([A-Z0-9][A-Z0-9\-]{2,})", text, re.I)
    dates, current = _find_preferred_dates(text, ref, [dob_span] if dob_span else [])

    # Someone writing about a patient (an attorney, a coordinator) has a signature and a quoted thread full of
    # other people's "Email:" and phone lines. Only what they wrote themselves can be the patient's contact.
    about_someone = not is_self and relationship == "third_party"
    lookup = core if about_someone else text
    patient_email = _labeled_email(lookup) or (normalize_email(email_in_body.group(0)) if email_in_body and is_self else None)
    if not patient_email and is_self:
        patient_email = normalize_email(ctx.sender_email)
    if not patient_email and first and last and not is_self:
        # A forwarded message from the patient herself: "From: Sharon Sumpter <sharon@example.com>"
        quoted = re.search(
            rf"(?im)^\s*from:\s*\"?{re.escape(first)}\s+{re.escape(last)}\"?\s*<([^>\s]+@[^>\s]+)>", body)
        if quoted:
            patient_email = normalize_email(quoted.group(1))
    phone = _labeled_phone(lookup) or (normalize_phone(phone_m.group(0)) if phone_m and is_self else None)
    # a parent/spouse writing for family: their details are the contact for the new chart, but never used to
    # identify the patient (they would match the family member's own chart)
    family = not is_self and relationship not in {None, "third_party"}
    contact_phone = normalize_phone(phone_m.group(0)) if family and phone_m and not phone else None
    contact_email = normalize_email(ctx.sender_email) if family and not patient_email else None
    address = _find_address(text)

    if not first or not last:
        conf -= 0.15
    new_patient = None
    if re.search(r"new patient|never been (?:seen|there|in)|first (?:visit|time)", text, re.I):
        new_patient = True
    elif re.search(r"existing patient|established patient|current patient|seen (?:by )?dr|last visit", text, re.I):
        new_patient = False

    result = ExtractedRequest(
        intent=intent,
        confidence=conf,
        patient=PatientInfo(
            first_name=first,
            last_name=last,
            date_of_birth=dob.isoformat() if dob else None,
            phone=phone,
            email=patient_email,
            contact_phone=contact_phone,
            contact_email=contact_email,
            sex=_find_sex(text),
            ssn_last4=_find_ssn_last4(text),
            **address,
            requester_is_patient=is_self,
            requester_relationship=relationship,
        ),
        insurance=InsuranceInfo(
            payer_name=_find_payer(text, ctx),
            member_id=normalize_member_id(member.group(1)) if member else None,
            group_number=group.group(1).upper() if group else None,
        ),
        appointment=AppointmentInfo(
            provider_name=_find_provider(text, ctx),
            visit_reason=_find_reason(text),
            preferred_dates=[d.isoformat() for d in dates],
            preferred_time_of_day=_time_of_day(text),
            preferred_time=_find_time(text),
            current_appointment_date=current.isoformat() if current else None,
            is_urgent=bool(URGENT.search(text)),
        ),
        is_new_patient=new_patient,
        clinical_red_flag=bool(RED_FLAGS.search(text)),
        engine="rules",
    )
    return finalize(result, ref)


# --------------------------------------------------------------------------- LLM

SYSTEM_PROMPT = """You are the intake assistant for a medical practice's scheduling team.
Read ONE patient email and extract a scheduling request as strict JSON.
Rules:
- Never invent data. If a value is not stated in the email, use null (or [] for lists).
- Dates must be ISO format YYYY-MM-DD. Resolve relative dates ("next Tuesday") against REFERENCE_DATE.
- preferred_dates: dates the patient WANTS, most preferred first. Do not include the date of an existing
  appointment they want to move/cancel; put that in current_appointment_date.
- phone: 10 digits only. member_id: letters/digits only, uppercase.
- payer_name: map to one of KNOWN_PAYERS when the email clearly refers to it, otherwise the name as written.
- provider_name: map to one of KNOWN_PROVIDERS when clearly referenced.
- requester_is_patient=false when the sender writes on behalf of someone else (child, parent, spouse, attorney,
  case manager). Patient phone/email/address are the PATIENT's, never the sender's signature details.
- Emails may contain a quoted thread; the newest message is first. Use details from anywhere in the thread.
- clinical_red_flag=true for possible emergencies (chest pain, trouble breathing, stroke signs, suicidal thoughts, severe bleeding).
- confidence: 0..1, your confidence that intent AND key fields are correct. Lower it for ambiguity or contradictions.
- summary: one neutral sentence for the scheduling staff.
Output JSON with exactly this shape:
{"intent": one of %s,
 "confidence": number, "summary": string,
 "patient": {"first_name","last_name","date_of_birth","phone","email","sex" (female|male|other),"address_line1","city",
             "state" (2-letter),"postal_code","ssn_last4" (only the last 4 digits, never a full SSN),
             "requester_is_patient","requester_relationship"},
 "insurance": {"payer_name","member_id","group_number"},
 "appointment": {"provider_name","visit_reason","preferred_dates","preferred_time_of_day" (morning|afternoon|any),
                 "preferred_time" (exact requested start as HH:MM 24h, or null),
                 "current_appointment_date","is_urgent"},
 "is_new_patient": true|false|null, "clinical_red_flag": boolean}"""


def extract_with_llm(llm: LLMProvider, subject: str, body: str, ctx: ExtractionContext) -> ExtractedRequest:
    user = (
        f"REFERENCE_DATE: {ctx.reference_date.isoformat()} ({WEEKDAYS[ctx.reference_date.weekday()]})\n"
        f"KNOWN_PAYERS: {', '.join(p for p, _ in ctx.payers)}\n"
        f"KNOWN_PROVIDERS: {', '.join(ctx.providers)}\n"
        f"SENDER: {ctx.sender_name or ''} <{ctx.sender_email or ''}>\n"
        f"SUBJECT: {subject}\n---\n{body}\n---"
    )
    data = llm.complete_json(SYSTEM_PROMPT % INTENTS, user)
    result = ExtractedRequest.model_validate(_coerce(data))
    result.engine = llm.name
    # Deterministic backstop: fill fields the model left empty but regexes can prove.
    rules = extract_with_rules(subject, body, ctx)
    for section in ("patient", "insurance"):
        target, source = getattr(result, section), getattr(rules, section)
        for field in ("date_of_birth", "phone", "member_id", "group_number", "sex", "ssn_last4",
                      "address_line1", "city", "state", "postal_code"):
            if hasattr(target, field) and not getattr(target, field) and getattr(source, field, None):
                setattr(target, field, getattr(source, field))
    if result.patient.requester_is_patient and not result.patient.email:
        result.patient.email = normalize_email(ctx.sender_email)
    result.clinical_red_flag = result.clinical_red_flag or rules.clinical_red_flag
    if not result.appointment.preferred_time:
        result.appointment.preferred_time = rules.appointment.preferred_time
    return finalize(result, ctx.reference_date)


def _coerce(data: dict) -> dict:
    """Make model output validate: drop unknown keys, fix obvious type slips."""
    data = dict(data)
    for section in ("patient", "insurance", "appointment"):
        if not isinstance(data.get(section), dict):
            data[section] = {}
    appt = data["appointment"]
    if isinstance(appt.get("preferred_dates"), str):
        appt["preferred_dates"] = [appt["preferred_dates"]]
    appt["preferred_dates"] = [str(d) for d in (appt.get("preferred_dates") or []) if d]
    appt["is_urgent"] = bool(appt.get("is_urgent"))
    appt["preferred_time_of_day"] = appt.get("preferred_time_of_day") or "any"
    pat = data["patient"]
    pat["requester_is_patient"] = pat.get("requester_is_patient") is not False
    for k in ("phone", "member_id", "group_number", "date_of_birth", "ssn_last4", "postal_code"):
        for sec in (pat, data["insurance"]):
            if k in sec and sec[k] is not None:
                sec[k] = str(sec[k])
    try:
        data["confidence"] = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    data["clinical_red_flag"] = bool(data.get("clinical_red_flag"))
    data["summary"] = data.get("summary") or ""
    return data


# --------------------------------------------------------------------------- shared

def finalize(r: ExtractedRequest, ref: date) -> ExtractedRequest:
    if r.intent not in INTENTS:
        r.intent = "other"
    r.confidence = round(max(0.0, min(1.0, r.confidence)), 2)
    p = r.patient
    p.phone = normalize_phone(p.phone)
    p.email = normalize_email(p.email)
    p.contact_phone = normalize_phone(p.contact_phone)
    p.contact_email = normalize_email(p.contact_email)
    dob = parse_date(p.date_of_birth)
    p.date_of_birth = dob.isoformat() if dob and dob < ref else None
    for attr in ("first_name", "last_name"):
        v = getattr(p, attr)
        setattr(p, attr, v.strip().title() if isinstance(v, str) and v.strip() else None)
    sex = (p.sex or "").strip().lower()
    p.sex = {"f": "female", "m": "male", "woman": "female", "man": "male"}.get(sex, sex) if sex else None
    if p.sex not in {None, "female", "male", "other"}:
        p.sex = "other"
    p.state = _state_code(p.state)
    p.ssn_last4 = p.ssn_last4 if isinstance(p.ssn_last4, str) and re.fullmatch(r"\d{4}", p.ssn_last4) else None
    p.postal_code = p.postal_code if isinstance(p.postal_code, str) and re.fullmatch(r"\d{5}(-\d{4})?", p.postal_code) else None
    r.insurance.member_id = normalize_member_id(r.insurance.member_id)
    a = r.appointment
    if not (isinstance(a.preferred_time, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", a.preferred_time)):
        a.preferred_time = None
    dates = []
    for d in a.preferred_dates:
        pd = parse_date(d)
        if pd and pd >= ref and pd.isoformat() not in dates:
            dates.append(pd.isoformat())
    a.preferred_dates = dates[:7]
    cur = parse_date(a.current_appointment_date)
    a.current_appointment_date = cur.isoformat() if cur else None
    if a.preferred_time_of_day not in {"morning", "afternoon", "any"}:
        a.preferred_time_of_day = "any"
    if not r.summary:
        who = f"{p.first_name or '?'} {p.last_name or '?'}"
        what = r.intent.replace("_", " ")
        when = f" preferring {', '.join(a.preferred_dates)}" if a.preferred_dates else ""
        with_ = f" with {a.provider_name}" if a.provider_name else ""
        r.summary = f"{who} requests {what}{with_}{when}."
    return r


def _classified(result: ExtractedRequest, subject: str, body: str) -> ExtractedRequest:
    """Let the Hugging Face classifier correct the intent (never raises; a no-op without a key)."""
    from app.services import email_classifier

    intent, confidence, note = email_classifier.refine(result.intent, result.confidence, subject, body)
    result.classifier = note
    if intent != result.intent:
        result.intent, result.confidence = intent, round(confidence, 2)
    return result


def extract_request(
    llm: LLMProvider, subject: str, body: str, ctx: ExtractionContext
) -> ExtractedRequest:
    if llm.is_offline:
        return _classified(extract_with_rules(subject, body, ctx), subject, body)
    try:
        return _classified(extract_with_llm(llm, subject, body, ctx), subject, body)
    except (LLMError, ValueError) as exc:
        fallback = extract_with_rules(subject, body, ctx)
        fallback.engine = f"rules (fallback from {llm.name})"
        fallback.engine_error = str(exc)[:500]
        return _classified(fallback, subject, body)
