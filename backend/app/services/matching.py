"""Deterministic patient matching (identify the patient behind a request).

The model never decides identity. Every chart that shares anything with the email is scored
on the evidence, and anything short of a clear, unambiguous winner goes to review.

The score is a probability, built the way record-linkage systems build one: each piece of
evidence adds to (or takes from) a log-odds total, and the total is turned into a percentage.
Strong identifiers (email, phone, insurance member ID, date of birth) weigh most; names count
in degrees, so a misspelling ("Sumter" for "Sumpter"), a nickname ("Liz" for "Elizabeth"),
an initial, a previous or preferred name, or a day/month swap in the date of birth still
counts for something instead of reading as "no match". A real clash (a different date of
birth) counts against. The percentage is what the request page's bar shows, so a chart that
is probably the person shows a high number and a stranger shows nothing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import InsurancePolicy, Patient
from app.services.extraction import ExtractedRequest
from app.services.normalize import (
    are_nicknames,
    dob_relation,
    edit_distance,
    name_similarity,
    normalize_member_id,
    normalize_name,
    parse_date,
    soundex,
)

#: Log-odds before any evidence: a random chart is very unlikely to be the person.
PRIOR = -3.0
EVIDENCE = {
    "dob_equal": 3.0, "dob_swapped": 1.4, "dob_typo": 1.0, "dob_different": -1.6,
    "last_exact": 2.2, "last_similar": 1.6, "last_close": 0.9,
    "first_exact": 1.5, "first_nickname": 1.2, "first_similar": 1.0, "first_initial": 0.6,
    "email": 3.5, "phone": 3.0, "member_id": 3.5,
    "name_clash": -1.5,
}


@dataclass
class Candidate:
    patient_id: int
    name: str
    mrn: str
    score: float
    evidence: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class MatchResult:
    status: str  # matched | ambiguous | unmatched
    patient_id: int | None
    confidence: float
    candidates: list[Candidate]
    conflicts: list[str]

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "patient_id": self.patient_id,
            "confidence": self.confidence,
            "conflicts": self.conflicts,
            "candidates": [c.as_dict() for c in self.candidates],
        }


def _probability(log_odds: float) -> float:
    return 1.0 / (1.0 + math.exp(-log_odds))


def _last_name_evidence(written: str | None, patient: Patient) -> tuple[float, str | None, float]:
    """(log-odds, label, best similarity) comparing with the chart's last name and any previous name."""
    wanted = normalize_name(written)
    if not wanted:
        return 0.0, None, 0.0
    names = [patient.last_name, *(patient.previous_name or "").replace(",", " ").split()[-1:]]
    best = 0.0
    for chart in (n for n in names if n):
        have = normalize_name(chart)
        sim = name_similarity(wanted, have)
        best = max(best, sim)
        if wanted == have:
            return EVIDENCE["last_exact"], "last name", 1.0
        near = min(len(wanted), len(have)) >= 4 and (edit_distance(wanted, have) <= 1 or soundex(wanted) == soundex(have) and sim >= 0.8)
        if sim >= 0.85 or near:
            return EVIDENCE["last_similar"], "last name (similar spelling)", sim
    if best >= 0.75:
        return EVIDENCE["last_close"], "last name (close)", best
    return 0.0, None, best


def _first_name_evidence(written: str | None, patient: Patient) -> tuple[float, str | None]:
    wanted = normalize_name(written)
    if not wanted:
        return 0.0, None
    options = [patient.first_name, patient.preferred_name]
    for chart in (o for o in options if o):
        have = normalize_name(chart)
        if wanted == have:
            return EVIDENCE["first_exact"], "first name"
        if are_nicknames(wanted, have):
            return EVIDENCE["first_nickname"], "first name (nickname)"
        if name_similarity(wanted, have) >= 0.8:
            return EVIDENCE["first_similar"], "first name (similar spelling)"
    have = normalize_name(patient.first_name)
    if have and (len(wanted) == 1 and have.startswith(wanted) or len(have) == 1 and wanted.startswith(have)):
        return EVIDENCE["first_initial"], "first initial"
    if have and wanted[:3] == have[:3] and len(wanted) >= 3:
        return EVIDENCE["first_initial"], "first name (partial)"
    return 0.0, None


def _score(p: Patient, x: ExtractedRequest) -> Candidate:
    info, ins = x.patient, x.insurance
    c = Candidate(patient_id=p.id, name=p.full_name, mrn=p.mrn, score=0.0)
    total = PRIOR

    relation = dob_relation(parse_date(info.date_of_birth), p.date_of_birth)
    if relation == "equal":
        total += EVIDENCE["dob_equal"]
        c.evidence.append("date of birth")
    elif relation == "swapped":
        total += EVIDENCE["dob_swapped"]
        c.evidence.append("date of birth (day and month swapped)")
    elif relation == "typo":
        total += EVIDENCE["dob_typo"]
        c.evidence.append("date of birth (one part differs)")
        c.conflicts.append(f"DOB in email {info.date_of_birth} nearly matches chart {p.date_of_birth.isoformat()}")
    elif relation == "different":
        total += EVIDENCE["dob_different"]
        c.conflicts.append(f"DOB in email {info.date_of_birth} differs from chart {p.date_of_birth.isoformat()}")

    last_log, last_label, last_sim = _last_name_evidence(info.last_name, p)
    first_log, first_label = _first_name_evidence(info.first_name, p)
    total += last_log + first_log
    if last_label:
        c.evidence.append(last_label)
    if first_label:
        c.evidence.append(first_label)

    if info.email and p.email and info.email == p.email:
        total += EVIDENCE["email"]
        c.evidence.append("email")
    if info.phone and p.phone and info.phone == p.phone:
        total += EVIDENCE["phone"]
        c.evidence.append("phone")
    member = normalize_member_id(ins.member_id)
    if member and member in {normalize_member_id(pol.member_id) for pol in p.policies}:
        total += EVIDENCE["member_id"]
        c.evidence.append("insurance member ID")

    # A written last name that is nothing like the chart's, while other evidence points here.
    if info.last_name and p.last_name and last_sim < 0.6 and total > PRIOR + 1.0:
        written = f"{info.first_name or ''} {info.last_name}".strip()
        c.conflicts.append(f"Name in email '{written}' differs from chart '{p.full_name}'")
        total += EVIDENCE["name_clash"]

    c.score = round(_probability(total), 3) if c.evidence else 0.0
    return c


def _swapped(value: date | None) -> date | None:
    """The same date with day and month exchanged, when that is a real date."""
    if value is None or value.day > 12:
        return None
    try:
        return value.replace(month=value.day, day=value.month)
    except ValueError:
        return None


def find_candidates(db: Session, x: ExtractedRequest, limit: int = 5) -> list[Candidate]:
    info = x.patient
    clauses = []
    if info.email:
        clauses.append(Patient.email == info.email)
    if info.phone:
        clauses.append(Patient.phone == info.phone)
    dob = parse_date(info.date_of_birth)
    if dob:
        clauses.append(Patient.date_of_birth == dob)
        if _swapped(dob):
            clauses.append(Patient.date_of_birth == _swapped(dob))
    last = normalize_name(info.last_name)
    if last:
        # Same first letter or same ending catches misspellings without scanning every chart in SQL.
        clauses.append(func.lower(func.substr(Patient.last_name, 1, 1)) == last[0])
        clauses.append(func.lower(Patient.last_name).like(f"%{last[-3:]}"))
    first = normalize_name(info.first_name)
    if first and not last:
        clauses.append(func.lower(func.substr(Patient.first_name, 1, 3)) == first[:3])
    member = normalize_member_id(x.insurance.member_id)
    if member:
        clauses.append(Patient.id.in_(select(InsurancePolicy.patient_id).where(InsurancePolicy.member_id == member)))
    if not clauses:
        return []
    patients = db.scalars(select(Patient).where(or_(*clauses)).limit(500)).all()
    scored = sorted((_score(p, x) for p in patients), key=lambda c: c.score, reverse=True)
    return [c for c in scored if c.score > 0.05][:limit]


def match_patient(db: Session, x: ExtractedRequest, threshold: float) -> MatchResult:
    candidates = find_candidates(db, x)
    if not candidates:
        return MatchResult("unmatched", None, 0.0, [], [])
    top = candidates[0]
    runner_up = candidates[1].score if len(candidates) > 1 else 0.0
    if top.score >= threshold and top.score - runner_up >= 0.2:
        return MatchResult("matched", top.patient_id, top.score, candidates, top.conflicts)
    if top.score >= 0.5:
        return MatchResult("ambiguous", None, top.score, candidates, top.conflicts)
    return MatchResult("unmatched", None, top.score, candidates, top.conflicts)
