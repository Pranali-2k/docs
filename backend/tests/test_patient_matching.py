"""Identifying the patient behind an email: how sure, and why.

The percentage on the request page's bar is the top candidate's score, so these tests pin what earns
a high number (a known patient, even with a misspelled name or a nickname) and what earns none.
"""
from __future__ import annotations

from datetime import date

from app.models import Patient
from app.services.extraction import ExtractionContext, ExtractedRequest, extract_with_rules
from app.services.matching import find_candidates, match_patient
from app.services.normalize import are_nicknames, dob_relation, soundex

THRESHOLD = 0.80
SCREENSHOT_SUBJECT = "Re: Sharon Sumpter; DOB: 1-16-1964; DOL: 3-30-26 // PRP + Dr. Siddique"
SCREENSHOT_BODY = (
    "All, Ms. Sumpter has been trying to reach Dr. Masih’s office for a week with no reply. See below. "
    "Can you please reach out to her?\n\nThanks,\nSarah\n\nFrom: Sharon Sumpter <sharon.sumpter@example.com>\n"
    "Sent: Monday\nSubject: PRP\nHi, I have been trying to reach the office."
)


def chart(db, first, last, dob, mrn, **extra) -> Patient:
    p = Patient(first_name=first, last_name=last, date_of_birth=dob, mrn=mrn, **extra)
    db.add(p)
    db.flush()
    return p


def request_about(first=None, last=None, dob=None, email=None, phone=None) -> ExtractedRequest:
    x = ExtractedRequest()
    x.patient.first_name, x.patient.last_name = first, last
    x.patient.date_of_birth = dob.isoformat() if dob else None
    x.patient.email, x.patient.phone = email, phone
    return x


# ------------------------------------------------------------------------- the email in the screenshot
def test_the_screenshot_email_names_the_patient_not_the_sender() -> None:
    ctx = ExtractionContext(reference_date=date(2026, 10, 15), sender_email="schristian@gilbertstern.com",
                            sender_name="Claudia Aguilera", payers=[], providers=[])
    x = extract_with_rules(SCREENSHOT_SUBJECT, SCREENSHOT_BODY, ctx)
    assert (x.patient.first_name, x.patient.last_name) == ("Sharon", "Sumpter")
    assert x.patient.date_of_birth == "1964-01-16"  # written 1-16-1964
    assert x.patient.email == "sharon.sumpter@example.com"  # from the forwarded message, not the sender's
    assert x.patient.requester_is_patient is False


def test_a_known_patient_in_that_email_shows_a_high_percentage(db) -> None:
    chart(db, "Sharon", "Sumpter", date(1964, 1, 16), "MRN-SS1")
    chart(db, "Sam", "Sumner", date(1980, 5, 5), "MRN-SS2")
    ctx = ExtractionContext(reference_date=date(2026, 10, 15), sender_email="schristian@gilbertstern.com",
                            sender_name="Claudia Aguilera", payers=[], providers=[])
    x = extract_with_rules(SCREENSHOT_SUBJECT, SCREENSHOT_BODY, ctx)
    m = match_patient(db, x, THRESHOLD)
    assert m.status == "matched" and m.confidence >= 0.9
    assert m.candidates[0].name == "Sharon Sumpter"
    assert {"date of birth", "last name", "first name"} <= set(m.candidates[0].evidence)


def test_a_misspelled_surname_still_finds_the_chart_and_says_so(db) -> None:
    chart(db, "Sharon", "Sumter", date(1964, 1, 16), "MRN-SS3")  # the chart spells it Sumter
    m = match_patient(db, request_about("Sharon", "Sumpter", date(1964, 1, 16)), THRESHOLD)
    assert m.status == "matched" and m.confidence >= 0.85
    assert "last name (similar spelling)" in m.candidates[0].evidence


def test_a_stranger_shows_nothing(db) -> None:
    chart(db, "Sharon", "Sumpter", date(1964, 1, 16), "MRN-SS4")
    m = match_patient(db, request_about("Nobody", "Atall", date(1999, 9, 9)), THRESHOLD)
    assert m.status == "unmatched" and m.confidence == 0.0 and m.candidates == []


# ---------------------------------------------------------------------------------------- degrees of sure
def test_the_percentage_grows_with_the_evidence(db) -> None:
    p = chart(db, "Sharon", "Sumpter", date(1964, 1, 16), "MRN-SS5", email="sharon@example.com", phone="5551230000")
    only_name = find_candidates(db, request_about("Sharon", "Sumpter"))[0].score
    name_and_dob = find_candidates(db, request_about("Sharon", "Sumpter", date(1964, 1, 16)))[0].score
    with_email = find_candidates(db, request_about("Sharon", "Sumpter", date(1964, 1, 16), email="sharon@example.com"))[0].score
    assert 0.5 < only_name < name_and_dob < with_email <= 1.0
    assert name_and_dob >= 0.9 and p.id


def test_a_known_surname_alone_is_a_possibility_not_a_match(db) -> None:
    chart(db, "Sharon", "Sumpter", date(1964, 1, 16), "MRN-SS6")
    m = match_patient(db, request_about(None, "Sumpter"), THRESHOLD)  # "Ms. Sumpter"
    assert m.status == "unmatched" and 0 < m.confidence < 0.5  # shown on the bar, but staff must confirm
    assert m.candidates[0].name == "Sharon Sumpter"


def test_a_nickname_and_a_previous_name_still_count(db) -> None:
    chart(db, "Elizabeth", "Brown", date(1980, 3, 3), "MRN-EB1", previous_name="Elizabeth Carter")
    nick = match_patient(db, request_about("Liz", "Brown", date(1980, 3, 3)), THRESHOLD)
    assert nick.status == "matched" and "first name (nickname)" in nick.candidates[0].evidence
    married = match_patient(db, request_about("Elizabeth", "Carter", date(1980, 3, 3)), THRESHOLD)
    assert married.status == "matched" and "last name" in married.candidates[0].evidence


def test_a_day_month_swap_in_the_date_of_birth_is_forgiven_a_different_date_is_not(db) -> None:
    chart(db, "Sharon", "Sumpter", date(1964, 1, 16), "MRN-SS7")
    slip = find_candidates(db, request_about("Sharon", "Sumpter", date(1964, 1, 6)))  # 6 Jan for 16 Jan: one digit off
    assert slip and "date of birth (one part differs)" in slip[0].evidence and slip[0].conflicts  # counted, and flagged for staff
    chart(db, "Dana", "Lee", date(1990, 3, 4), "MRN-DL1")
    sw = find_candidates(db, request_about("Dana", "Lee", date(1990, 4, 3)))[0]  # written 3-4 as 4-3
    assert "date of birth (day and month swapped)" in sw.evidence and sw.score >= 0.6
    wrong = find_candidates(db, request_about("Dana", "Lee", date(1975, 7, 7)))[0]
    assert wrong.score < sw.score and wrong.conflicts


def test_two_charts_that_fit_equally_are_ambiguous_not_matched(db) -> None:
    chart(db, "Sam", "Rivera", date(1970, 1, 1), "MRN-R1")
    chart(db, "Sam", "Rivera", date(1970, 1, 1), "MRN-R2")  # a duplicate chart
    m = match_patient(db, request_about("Sam", "Rivera", date(1970, 1, 1)), THRESHOLD)
    assert m.status == "ambiguous" and m.patient_id is None and len(m.candidates) == 2


# --------------------------------------------------------------------------------------------- helpers
def test_the_name_and_date_helpers() -> None:
    assert soundex("Smith") == soundex("Smyth") and soundex("Smith") != soundex("Jones")
    assert are_nicknames("Liz", "Elizabeth") and are_nicknames("Bob", "Robert") and not are_nicknames("Sam", "Sally")
    assert dob_relation(date(1990, 4, 3), date(1990, 3, 4)) == "swapped"
    assert dob_relation(date(1991, 3, 4), date(1990, 3, 4)) == "typo"
    assert dob_relation(date(1975, 7, 7), date(1990, 3, 4)) == "different"
    assert dob_relation(None, date(1990, 3, 4)) == "unknown"


def test_a_staff_signature_in_the_quoted_thread_is_not_the_patients_email() -> None:
    """The real thread: lien coordinators' signatures say 'Email: ...@docshealth.com'. None of it is Sharon's."""
    body = ("Thanks Amanda. Did this get resolved?\n\nThanks,\n\nSarah\n\nFrom: Amanda Barrett <amanda.barrett@docshealth.com>\n"
            "Sent: Thursday\nSubject: Re: Sharon Sumpter; DOB: 1-16-1964\n\nThank you, Susan.\n\nSincerely,\n\n"
            "Susan Governale\nLien Coordinator\nmain: 310.746.5918 ext 903\nEmail: Susan.Governale@DOCSHealth.com\n")
    ctx = ExtractionContext(reference_date=date(2026, 10, 16), sender_email="schristian@gilbertstern.com",
                            sender_name="Sarah Christian", payers=[], providers=[])
    x = extract_with_rules("RE: " + SCREENSHOT_SUBJECT, body, ctx)
    assert (x.patient.first_name, x.patient.last_name, x.patient.date_of_birth) == ("Sharon", "Sumpter", "1964-01-16")
    assert x.patient.email is None and x.patient.phone is None
