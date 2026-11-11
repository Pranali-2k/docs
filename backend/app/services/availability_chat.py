"""Read a doctor's plain-English note about their time ("I'm in surgery tomorrow 2-4 pm") as a schedule change.

Rules, not a model: the sentence is about the doctor's own calendar, so there is nothing to send
anywhere and the reading is the same every time. It understands who (Dr. Rao / Rao / the doctor you
are working on), when (today, tomorrow, Monday, next Tuesday, Oct 20, 10/20, Oct 20-22, "this week")
and which hours (2-4 pm, 2pm to 4pm, morning, afternoon, after 3, before 10, all day), and whether
to keep the time free (busy, unavailable, in surgery, away...), give it back (free up, available
again, remove the block) or just show what is blocked.

Times are the clinic's (Los Angeles) wall clock, naive, like appointments.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DAY_START, DAY_END = time(8, 0), time(18, 0)  # what "all day" means when no working hours are known

UNBLOCK = re.compile(
    r"\b(?:free(?:d)? up|available again|i(?:'m| am) (?:free|available|back)|open(?:ed)? (?:up|it up)?|unblock|"
    r"remove|clear|cancel (?:the |my )?(?:block|busy)|release|make .{0,20}available|back (?:in|at) (?:the )?(?:office|clinic))\b",
    re.I,
)
BLOCK = re.compile(
    r"\b(?:busy|unavailable|not available|n't be (?:in|available)|block|blocked|off|out of (?:the )?office|away|"
    r"surgery|operating|meeting|conference|vacation|leave|holiday|appointments? (?:free|off)|hold|keep .{0,20}(?:free|engaged)|"
    r"engaged|can'?t (?:see|take)|no (?:patients|appointments)|do not (?:book|schedule)|don'?t (?:book|schedule))\b",
    re.I,
)
LIST = re.compile(r"\b(?:show|list|what(?:'s| is)|which|when am i|my (?:availability|blocks|schedule)|blocked)\b", re.I)


@dataclass
class Command:
    action: str  # block | unblock | list | unknown
    provider_id: int | None = None
    ranges: list[tuple[datetime, datetime]] = field(default_factory=list)
    reason: str | None = None
    problems: list[str] = field(default_factory=list)


def _parse_clock(text: str, ampm_hint: str | None = None) -> time | None:
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", text.strip().lower())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    suffix = (m.group(3) or ampm_hint or "").replace(".", "")
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _find_times(text: str) -> tuple[time, time] | None:
    t = text.lower()
    if re.search(r"\ball[ -]?day\b|\bwhole day\b|\bfull day\b", t):
        return DAY_START, DAY_END
    clock = r"\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?"
    rng = re.search(rf"(?:from\s+|between\s+)?({clock})\s*(?:-|–|—|to|until|till|and)\s*({clock})(?!\d)", t)
    if rng:
        end_raw, start_raw = rng.group(2), rng.group(1)
        hint = re.search(r"(am|pm|a\.m\.|p\.m\.)", end_raw)
        end = _parse_clock(end_raw)
        start = _parse_clock(start_raw, hint.group(1).replace(".", "") if hint and not re.search(r"am|pm", start_raw) else None)
        if start and end:
            if end <= start and end.hour < 12 and not re.search(r"am|pm", end_raw):
                end = end.replace(hour=end.hour + 12)  # "9 to 5": 9 am to 5 pm
            if end > start:
                return start, end
    after = re.search(rf"\b(?:after|from|starting(?: at)?)\s+({clock})", t)
    if after and _parse_clock(after.group(1)):
        start = _parse_clock(after.group(1))
        if start.hour < 7 and "am" not in after.group(1):
            start = start.replace(hour=start.hour + 12)  # "after 3"
        return start, DAY_END
    before = re.search(rf"\b(?:before|until|till|by)\s+({clock})", t)
    if before and _parse_clock(before.group(1)):
        end = _parse_clock(before.group(1))
        if end.hour < 7 and "am" not in before.group(1):
            end = end.replace(hour=end.hour + 12)
        return DAY_START, end
    single = re.search(rf"\bat\s+({clock})", t)
    if single and _parse_clock(single.group(1)):
        start = _parse_clock(single.group(1))
        return start, (datetime.combine(date.today(), start) + timedelta(hours=1)).time()
    if re.search(r"\bmorning\b", t):
        return DAY_START, time(12, 0)
    if re.search(r"\bafternoon\b", t):
        return time(12, 0), DAY_END
    if re.search(r"\bevening\b", t):
        return time(16, 0), time(20, 0)
    return None


def _weekday_date(name: str, today: date, *, upcoming: bool) -> date:
    target = WEEKDAYS.index(name)
    ahead = (target - today.weekday()) % 7
    if ahead == 0 and upcoming:
        ahead = 7
    return today + timedelta(days=ahead)


def _month_day(text: str, today: date) -> list[date]:
    out: list[date] = []
    month_names = "|".join(MONTHS) + "|january|february|march|april|june|july|august|september|october|november|december"
    # "Oct 20", "October 20th", "20 Oct", with an optional range "Oct 20-22"
    pattern = rf"\b(?:({month_names})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*(?:-|–|to)\s*(\d{{1,2}}))?|(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_names})[a-z]*\.?)\b"
    for m in re.finditer(pattern, text, re.I):
        if m.group(1):
            month, first, last = MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3) or m.group(2))
        else:
            month, first, last = MONTHS[m.group(5)[:3].lower()], int(m.group(4)), int(m.group(4))
        for day in range(first, max(first, last) + 1):
            try:
                d = date(today.year, month, day)
            except ValueError:
                continue
            out.append(d if d >= today - timedelta(days=60) else d.replace(year=d.year + 1))
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in re.finditer(r"(?<![\d:/])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d:/])", text):
        year = int(m.group(3)) if m.group(3) else today.year
        year += 2000 if year < 100 else 0
        try:
            out.append(date(year, int(m.group(1)), int(m.group(2))))
        except ValueError:
            pass
    return out


def _find_dates(text: str, today: date) -> list[date]:
    t = text.lower()
    dates = _month_day(text, today)
    if "day after tomorrow" in t:
        dates.append(today + timedelta(days=2))
    elif "tomorrow" in t:
        dates.append(today + timedelta(days=1))
    if re.search(r"\btoday\b|\btonight\b|\bthis (?:morning|afternoon|evening)\b", t):
        dates.append(today)
    span = re.search(r"\b(?:from\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*(?:-|–|to|through|thru)\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if span:
        first = _weekday_date(span.group(1), today, upcoming=False)
        last = _weekday_date(span.group(2), first, upcoming=False)
        dates += [first + timedelta(days=i) for i in range((last - first).days + 1)]
    else:
        for m in re.finditer(r"\b(next|this|on)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", t):
            when = _weekday_date(m.group(2), today, upcoming=m.group(1) == "next")
            if m.group(1) == "next" and when - today < timedelta(days=7) and today.weekday() < WEEKDAYS.index(m.group(2)):
                when += timedelta(days=7)  # "next Friday" said on a Monday means the Friday after this one
            dates.append(when)
    if re.search(r"\b(?:this week|rest of (?:the|this) week)\b", t):
        dates += [today + timedelta(days=i) for i in range(0, 5 - today.weekday()) if today.weekday() + i < 5]
    if re.search(r"\bnext week\b", t):
        monday = today + timedelta(days=7 - today.weekday())
        dates += [monday + timedelta(days=i) for i in range(5)]
    return sorted(set(dates))


def _find_reason(text: str) -> str | None:
    m = re.search(r"\b(?:for|because(?: of)?|due to|with)\s+(?:a |an |the |my )?([a-z][a-z \-']{2,40}?)(?=[,.;!?]|$|\s+(?:on|from|at|tomorrow|today|between|after|before)\b)", text, re.I)
    if m and not re.match(r"(?:the )?(?:day|morning|afternoon|week)\b", m.group(1), re.I):
        return m.group(1).strip().capitalize()
    for word in ("surgery", "meeting", "conference", "vacation", "holiday", "lunch", "training", "leave", "procedure"):
        if re.search(rf"\b{word}\b", text, re.I):
            return word.capitalize()
    return None


def find_provider(text: str, providers: list[tuple[int, str]]) -> int | None:
    """The doctor named in the text ("Dr. Rao", "Elena", "Torres"), if exactly one is."""
    t = text.lower()
    hits = []
    for pid, name in providers:
        parts = [p for p in re.split(r"[\s.,]+", name.lower()) if p and p not in {"dr", "md", "do"}]
        if any(len(p) >= 3 and re.search(rf"\b{re.escape(p)}\b", t) for p in parts):
            hits.append(pid)
    return hits[0] if len(hits) == 1 else None


def parse(text: str, providers: list[tuple[int, str]], default_provider_id: int | None, today: date) -> Command:
    """The schedule change a note describes. `problems` says what could not be understood."""
    cmd = Command(action="unknown", provider_id=find_provider(text, providers) or default_provider_id)
    unblock, block, listing = bool(UNBLOCK.search(text)), bool(BLOCK.search(text)), bool(LIST.search(text))
    times = _find_times(text)
    dates = _find_dates(text, today)
    asks = bool(re.match(r"\s*(?:show|list|what|which|when|do i|am i|how many|any)\b", text, re.I)) or "?" in text
    if asks and not unblock and (listing or block):  # "show what is blocked this week": a question, not an order
        listing, block = True, False
    if listing and not dates and not (block or unblock) or (listing and not block and not unblock):
        cmd.action = "list"
        cmd.ranges = [(datetime.combine(d, time.min), datetime.combine(d, time.max)) for d in dates]
        return cmd
    if unblock and not (block and not re.search(r"\bavailable again|\bfree up|\bunblock|\bremove|\bclear|\brelease\b", text, re.I)):
        cmd.action = "unblock"
    elif block or dates or times:
        cmd.action = "block"
    else:
        cmd.problems.append("I could not tell what you want changed.")
        return cmd
    if cmd.provider_id is None:
        cmd.problems.append("Which doctor is this for? Say, for example, \"Dr. Rao is busy tomorrow 2-4 pm\".")
    if not dates:
        cmd.problems.append("Which day? For example: today, tomorrow, Monday, or Oct 20.")
    start_t, end_t = times or (DAY_START, DAY_END)
    for d in dates:
        cmd.ranges.append((datetime.combine(d, start_t), datetime.combine(d, end_t)))
    cmd.reason = _find_reason(text) if cmd.action == "block" else None
    return cmd
