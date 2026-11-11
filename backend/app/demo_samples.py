"""Sample inbound emails covering each PRD path, for staff demos and validation.

Each sample is rendered against the current seed data, so dates such as
"my appointment on ..." always point at a real upcoming appointment.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appointment, Patient

SAMPLES: dict[str, dict] = {
    "auto_book": {
        "title": "Existing patient books a physical",
        "expected": "Auto-response: eligibility active, in network, appointment booked, request resolved",
        "from_name": "Maria Gonzalez",
        "from_address": "maria.gonzalez@example.com",
        "subject": "Appointment request - annual physical",
        "body": "Hi,\n\nI'd like to schedule my annual physical with Dr. Rao next Tuesday, preferably in the "
                "morning.\nMy DOB is 03/14/1985 and my phone is (312) 555-0142.\n\nThank you,\nMaria Gonzalez",
    },
    "auto_reschedule": {
        "title": "Reschedule an existing appointment",
        "expected": "Auto-response: old appointment cancelled, new one booked, request resolved",
        "from_name": "James Carter",
        "from_address": "james.carter@example.com",
        "subject": "Need to reschedule",
        "body": "Hello,\n\nSomething came up at work. Can I reschedule my appointment on {james_appt} to Thursday "
                "afternoon instead?\nDOB 11/02/1972.\n\nThanks,\nJames Carter",
    },
    "auto_cancel": {
        "title": "Cancel an appointment",
        "expected": "Auto-response: appointment cancelled, request resolved",
        "from_name": "Priya Shah",
        "from_address": "priya.shah@example.com",
        "subject": "Cancel appointment",
        "body": "Hi, please cancel my appointment on {priya_appt}. I'll reach out later to book again.\n"
                "Date of birth: 07/21/1990\n\nBest,\nPriya Shah",
    },
    "new_patient": {
        "title": "New patient (no chart yet)",
        "expected": "Manual review: UNMATCHED_PATIENT. Staff use 'Register patient from request' and the AI then books",
        "from_name": "Olivia Bennett",
        "from_address": "olivia.bennett@example.com",
        "subject": "New patient appointment",
        "body": "Hello,\n\nI'm a new patient and would like to see Dr. Patel for a check-up sometime next week. "
                "My name is Olivia Bennett, date of birth 08/30/1992, phone 312-555-0199.\n"
                "Insurance: Aetna, member ID AET777888999, group 45120.\n\nThank you!\nOlivia",
    },
    "eligibility_failed": {
        "title": "Insurance eligibility fails",
        "expected": "Manual review: ELIGIBILITY_FAILED (coverage inactive)",
        "from_name": "Linda Brooks",
        "from_address": "linda.brooks@example.com",
        "subject": "Follow-up visit",
        "body": "Hi, I need a follow-up visit with Dr. Rao for my blood pressure, any day next week works.\n"
                "DOB 05/09/1966.\nThanks,\nLinda Brooks",
    },
    "out_of_network": {
        "title": "Provider out of network",
        "expected": "Manual review: OUT_OF_NETWORK (Dr. Patel does not take Humana)",
        "from_name": "Daniel Lee",
        "from_address": "daniel.lee@example.com",
        "subject": "Book with Dr. Patel",
        "body": "Hello, I'd like to book a consultation with Dr. Patel on Monday afternoon for knee pain.\n"
                "DOB 09/15/1995, phone 312-555-0110.\n\nRegards,\nDaniel Lee",
    },
    "conflicting_insurance": {
        "title": "Insurance in email conflicts with the chart",
        "expected": "Manual review: CONFLICTING_INFO (payer and member ID differ from the chart)",
        "from_name": "Robert Kim",
        "from_address": "robert.kim@example.com",
        "subject": "Appointment + new insurance",
        "body": "Hi, I switched to Humana this month, member ID HUM12340000. I'd like to book a follow-up with "
                "Dr. Patel next Wednesday.\nDOB 01/30/1958.\n\nThanks,\nRobert Kim",
    },
    "ambiguous_match": {
        "title": "Ambiguous patient (twins on one family account)",
        "expected": "Manual review: AMBIGUOUS_MATCH (two charts share email, DOB and last name)",
        "from_name": "Sarah Walker",
        "from_address": "walker.family@example.com",
        "subject": "Check-up for my son",
        "body": "Hi there,\n\nCan we schedule a check-up with Dr. Torres for my son on Friday? DOB 06/01/2015.\n\n"
                "Thanks,\nSarah Walker",
    },
    "red_flag": {
        "title": "Possible emergency in an email",
        "expected": "Manual review: CLINICAL_RED_FLAG, high priority, safety draft reply",
        "from_name": "Maria Gonzalez",
        "from_address": "maria.gonzalez@example.com",
        "subject": "Need to be seen",
        "body": "Hi, I've had chest pain and shortness of breath since last night. Can I get an appointment "
                "with Dr. Rao today?\nDOB 03/14/1985\nMaria Gonzalez",
    },
    "general_question": {
        "title": "General question (not a scheduling action)",
        "expected": "Manual review: UNSUPPORTED_INTENT, with a drafted reply for staff",
        "from_name": "Priya Shah",
        "from_address": "priya.shah@example.com",
        "subject": "Question",
        "body": "Hello, what are your office hours on Saturdays? And do you accept Medicare?\nThanks, Priya Shah",
    },
    "incomplete": {
        "title": "Incomplete request",
        "expected": "Manual review: INCOMPLETE_DATA and UNMATCHED_PATIENT",
        "from_name": None,
        "from_address": "sam.r.1990@example.net",
        "subject": "appt",
        "body": "need an appointment next week pls",
    },
}


def _upcoming(db: Session, email: str) -> str:
    p = db.scalar(select(Patient).where(Patient.email == email))
    if not p:
        return "next Monday"
    a = db.scalar(select(Appointment).where(Appointment.patient_id == p.id, Appointment.status == "booked")
                  .order_by(Appointment.start_at))
    return f"{a.start_at:%B} {a.start_at.day}" if a else "next Monday"


def render(db: Session, key: str) -> dict:
    s = SAMPLES[key]
    ctx = {"james_appt": _upcoming(db, "james.carter@example.com"),
           "priya_appt": _upcoming(db, "priya.shah@example.com")}
    return {**s, "key": key, "body": s["body"].format(**ctx)}
