"""Small normalization helpers shared by extraction, matching and registration."""
from __future__ import annotations

import re
from datetime import date, datetime
from difflib import SequenceMatcher

from dateutil import parser as dateparser


def digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def normalize_phone(value: str | None) -> str | None:
    d = digits(value)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else None


def format_phone(value: str | None) -> str:
    d = normalize_phone(value)
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if d else (value or "")


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().strip("<>()[]\"',;:").rstrip(".").lower()
    return value if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) else None


def normalize_member_id(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return cleaned or None


def normalize_name(value: str | None) -> str:
    return re.sub(r"[^a-z]", "", (value or "").lower())


def name_similarity(a: str | None, b: str | None) -> float:
    a, b = normalize_name(a), normalize_name(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


#: Common English short forms, so "Liz" meets "Elizabeth" and "Bob" meets "Robert".
_NICKNAME_GROUPS = (
    {"elizabeth", "liz", "lizzy", "beth", "betty", "eliza", "libby"}, {"robert", "rob", "bob", "bobby", "robbie"},
    {"william", "will", "bill", "billy", "liam"}, {"richard", "rick", "ricky", "dick", "rich"},
    {"james", "jim", "jimmy", "jamie"}, {"john", "jack", "johnny", "jon"}, {"michael", "mike", "mick", "mikey"},
    {"margaret", "maggie", "meg", "peggy", "marge", "greta"}, {"katherine", "catherine", "kate", "katie", "kathy", "cathy", "kat"},
    {"jennifer", "jen", "jenny"}, {"patricia", "pat", "patty", "trish"}, {"deborah", "debbie", "deb"},
    {"susan", "sue", "susie", "suzy"}, {"sharon", "shari", "sherry"}, {"barbara", "barb", "barbie"},
    {"christopher", "chris"}, {"daniel", "dan", "danny"}, {"matthew", "matt"}, {"anthony", "tony"},
    {"joseph", "joe", "joey"}, {"thomas", "tom", "tommy"}, {"charles", "charlie", "chuck"}, {"edward", "ed", "eddie", "ted"},
    {"alexander", "alex", "xander"}, {"samuel", "sam", "sammy"}, {"benjamin", "ben", "benny"}, {"nicholas", "nick"},
    {"stephen", "steven", "steve"}, {"andrew", "andy", "drew"}, {"david", "dave", "davey"}, {"jessica", "jess", "jessie"},
    {"rebecca", "becky", "becca"}, {"victoria", "vicky", "tori"}, {"alexandra", "alex", "sandra", "lexi"},
)
_NICKNAMES: dict[str, set[str]] = {}
for _group in _NICKNAME_GROUPS:
    for _name in _group:
        _NICKNAMES.setdefault(_name, set()).update(_group)


def are_nicknames(a: str | None, b: str | None) -> bool:
    a, b = normalize_name(a), normalize_name(b)
    return bool(a and b and a != b and b in _NICKNAMES.get(a, ()))


def soundex(value: str | None) -> str:
    """American Soundex: names that sound alike share a code ("Sumpter" and "Sumter")."""
    word = normalize_name(value)
    if not word:
        return ""
    codes = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3"),
             "l": "4", **dict.fromkeys("mn", "5"), "r": "6"}
    out, last = word[0].upper(), codes.get(word[0], "")
    for ch in word[1:]:
        code = codes.get(ch, "")
        if code and code != last:
            out += code
        if ch not in "hw":  # h and w do not break a run of the same sound
            last = code
    return (out + "000")[:4]


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (small strings: names)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def dob_relation(written: date | None, chart: date | None) -> str:
    """How an emailed date of birth relates to the chart's: equal | swapped | typo | different | unknown.

    People write 1-16-1964 as 16-1-1964, or slip a digit in the year; those are the same person
    far more often than a different one, so they count for something instead of as a clash.
    """
    if written is None or chart is None:
        return "unknown"
    if written == chart:
        return "equal"
    if written.year == chart.year and written.month == chart.day and written.day == chart.month:
        return "swapped"
    same_parts = (written.month == chart.month) + (written.day == chart.day) + (written.year == chart.year)
    if same_parts == 2 and (written.year != chart.year and abs(written.year - chart.year) in (1, 10, 100)
                            or written.year == chart.year):
        return "typo"
    return "different"


def parse_date(value, *, dayfirst: bool = False) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return dateparser.parse(str(value), dayfirst=dayfirst, fuzzy=False).date()
    except (ValueError, OverflowError, TypeError):
        return None
