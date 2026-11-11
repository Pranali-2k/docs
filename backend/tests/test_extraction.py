from datetime import date

from app.llm.providers import LLMError, LLMProvider, parse_json_object
from app.services.extraction import ExtractionContext, extract_request, extract_with_rules

CTX = ExtractionContext(
    reference_date=date(2026, 10, 1),  # a Thursday
    sender_email="maria.gonzalez@example.com",
    sender_name="Maria Gonzalez",
    payers=[("Blue Cross Blue Shield", ["BCBS"]), ("Aetna", [])],
    providers=["Dr. Anita Rao", "Dr. Marcus Patel"],
)


def test_rules_extract_new_appointment():
    x = extract_with_rules(
        "Appointment request",
        "I'd like to schedule a physical with Dr. Rao next Tuesday morning. DOB 03/14/1985, phone (312) 555-0142."
        "\nMy BCBS member ID is BCB-123456789.\n\nThanks,\nMaria Gonzalez",
        CTX,
    )
    assert x.intent == "new_appointment"
    assert (x.patient.first_name, x.patient.last_name) == ("Maria", "Gonzalez")
    assert x.patient.date_of_birth == "1985-03-14"
    assert x.patient.phone == "3125550142"
    assert x.insurance.payer_name == "Blue Cross Blue Shield"
    assert x.insurance.member_id == "BCB123456789"
    assert x.appointment.provider_name == "Dr. Anita Rao"
    assert x.appointment.preferred_dates == ["2026-10-06"]
    assert x.appointment.preferred_time_of_day == "morning"
    assert x.confidence >= 0.8


def test_rules_reschedule_separates_current_and_preferred_dates():
    x = extract_with_rules("", "Can I reschedule my appointment on October 9 to October 14 in the afternoon?", CTX)
    assert x.intent == "reschedule"
    assert x.appointment.current_appointment_date == "2026-10-09"
    assert x.appointment.preferred_dates == ["2026-10-14"]
    assert x.appointment.preferred_time_of_day == "afternoon"


def test_rules_flag_clinical_red_flags_and_dependents():
    x = extract_with_rules("", "My son Ethan Walker has chest pain, can he be seen today?", CTX)
    assert x.clinical_red_flag
    assert x.patient.first_name == "Ethan" and not x.patient.requester_is_patient
    assert x.patient.email is None  # the sender's email is not the patient's


class BrokenLLM(LLMProvider):
    name = "broken"

    def complete(self, system, user, json_mode=False):
        raise LLMError("timeout")


class FakeLLM(LLMProvider):
    name = "fake"

    def complete(self, system, user, json_mode=False):
        return (
            '```json\n{"intent": "cancel", "confidence": 0.93, "patient": {"first_name": "maria", '
            '"last_name": "gonzalez", "date_of_birth": "1985-03-14", "phone": "+1 312 555 0142"}, '
            '"appointment": {"preferred_dates": "2020-01-01"}}\n```'
        )


def test_llm_output_is_normalized():
    x = extract_request(FakeLLM(None), "", "Please cancel. Maria", CTX)
    assert x.intent == "cancel" and x.engine == "fake"
    assert x.patient.first_name == "Maria" and x.patient.phone == "3125550142"
    assert x.appointment.preferred_dates == []  # past dates dropped
    assert x.patient.email == "maria.gonzalez@example.com"


def test_llm_failure_falls_back_to_rules_with_error():
    x = extract_request(BrokenLLM(None), "", "Please cancel my appointment. Maria Gonzalez", CTX)
    assert x.intent == "cancel"
    assert x.engine_error and "fallback" in x.engine


def test_parse_json_object_tolerates_chatter():
    assert parse_json_object('Sure! {"a": 1} hope this helps') == {"a": 1}


def test_rules_expand_next_week_to_weekdays():
    x = extract_with_rules("", "I'd like a check-up sometime next week please. Maria Gonzalez", CTX)
    assert x.appointment.preferred_dates == ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
