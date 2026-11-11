"""Staff assists on a request: chart suggestions for autofill, and a drafted reply."""
from tests.test_workflow import inject


def test_suggestions_find_the_existing_chart_and_carry_its_demographics(client):
    req = inject(client, "auto_book")  # Maria Gonzalez, an existing patient, DOB 03/14/1985
    found = client.get(f"/api/requests/{req['id']}/patient-suggestions")
    assert found.status_code == 200, found.text
    top = found.json()[0]
    assert top["last_name"] == "Gonzalez" and top["date_of_birth"] == "1985-03-14"
    assert "name" in top["evidence"] and "date of birth" in top["evidence"]
    for key in ("mrn", "phone", "email", "address_line1", "sex", "primary_payer"):
        assert key in top


def test_a_stranger_has_no_suggestions(client):
    req = inject(client, "new_patient")  # Olivia Bennett has no chart yet
    assert client.get(f"/api/requests/{req['id']}/patient-suggestions").json() == []


def test_a_reply_is_drafted_from_the_database_facts_when_no_model_is_available(client):
    booked = inject(client, "auto_book")
    reply = client.post(f"/api/requests/{booked['id']}/draft-reply", json={})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["engine"] == "template" and body["kind"] == "booked"
    assert booked["reference"] in body["subject"]
    when = client.get(f"/api/requests/{booked['id']}").json()["appointment"]
    assert when["provider_name"] in body["body"]
    assert "language model" in body.get("note", "")  # it says why it is not an AI-written reply

    review = inject(client, "new_patient")
    held = client.post(f"/api/requests/{review['id']}/draft-reply", json={}).json()
    assert held["kind"] in {"offer_slots", "review_hold", "request_details"} and held["body"]


class _FakeLLM:
    name, is_offline = "fake", False

    def __init__(self, keep_facts: bool):
        self.keep_facts = keep_facts

    def complete(self, system, user, json_mode=False):
        facts = user.split("Facts you must keep exactly:\n", 1)[1]
        return "Happy to help! " + (facts if self.keep_facts else "Your visit is sorted.")


def test_a_model_written_reply_is_used_only_if_every_fact_survives(client, monkeypatch):
    from app.llm import providers

    booked = inject(client, "auto_book")
    monkeypatch.setattr(providers, "get_llm", lambda *a, **k: _FakeLLM(keep_facts=True))
    good = client.post(f"/api/requests/{booked['id']}/draft-reply", json={"instructions": "friendly"}).json()
    assert good["engine"] == "llm:fake" and good["body"].startswith("Happy to help!")

    monkeypatch.setattr(providers, "get_llm", lambda *a, **k: _FakeLLM(keep_facts=False))
    bad = client.post(f"/api/requests/{booked['id']}/draft-reply", json={}).json()
    assert bad["engine"] == "template" and "changed a fact" in bad["note"]  # the model dropped the date: refused


def test_a_staff_confirmed_match_reads_as_certain_and_books(client):
    req = inject(client, "new_patient")  # no chart yet, so the automatic match is 0
    assert req["match_confidence"] == 0
    payers = {p["name"]: p["id"] for p in client.get("/api/payers").json()}
    done = client.post(f"/api/requests/{req['id']}/create-patient", json={"payer_id": payers["Aetna"]}).json()
    assert done["match_confidence"] == 1.0  # confirmed by staff: the bar must not say 0%
    assert done["status"] == "resolved" and done["appointment"] is not None  # and it booked straight away


def test_the_chosen_doctor_changes_the_offered_slots(client):
    req = inject(client, "new_patient")
    doctors = {p["full_name"]: p["id"] for p in client.get("/api/providers").json()}
    torres = client.get(f"/api/requests/{req['id']}/slots", params={"provider_id": doctors["Dr. Elena Torres"]}).json()
    patel = client.get(f"/api/requests/{req['id']}/slots", params={"provider_id": doctors["Dr. Marcus Patel"]}).json()
    assert torres["slots"] and all(s["provider_name"] == "Dr. Elena Torres" for s in torres["slots"])
    assert patel["slots"] and all(s["provider_name"] == "Dr. Marcus Patel" for s in patel["slots"])


def test_a_doubled_title_still_names_the_doctor():
    from app.services.extraction import ExtractionContext, _find_provider
    from datetime import date

    ctx = ExtractionContext(reference_date=date(2026, 10, 13), sender_email=None, sender_name=None, payers=[],
                            providers=["Dr. Anita Rao", "Dr. Elena Torres", "Dr. Marcus Patel"])
    assert _find_provider("schedule me for Dr. Dr. Elena Torres next week", ctx) == "Dr. Elena Torres"
    assert _find_provider("please book Doctor Doctor Patel", ctx) == "Dr. Marcus Patel"
    assert _find_provider("with Dr. Smith please", ctx) == "Dr. Smith"  # not on staff: reported as asked


def test_an_existing_patient_with_no_insurance_is_booked_with_the_named_doctor(client):
    client.post("/api/patients", json={"first_name": "Dana", "last_name": "Plain", "date_of_birth": "1981-04-17",
                                       "phone": "3125550187", "email": "dana.plain@example.com"})
    sent = client.post("/api/intake/email", json={
        "from_address": "dana.plain@example.com", "from_name": "Dana Plain", "subject": "Appointment",
        "body": "Hi, this is Dana Plain. I would like to schedule an appointment for Dr. Dr. Elena Torres, any "
                "morning next week. My date of birth is 04/17/1981 and my phone number is (312) 555-0187.\n\n"
                "Thank you,\nDana Plain", "process": True}).json()["request"]
    assert sent["status"] == "resolved", sent["review_reasons"]
    assert sent["appointment"]["provider_name"] == "Dr. Elena Torres"
    assert "No insurance is on file" in sent["resolution_note"]  # booked, and staff are told what is left to do
    assert "bring your insurance card" in sent["draft_response"]


def test_a_day_with_no_opening_for_the_doctor_is_offered_not_silently_swapped(client):
    from datetime import date, timedelta

    client.post("/api/patients", json={"first_name": "Eli", "last_name": "Dayoff", "date_of_birth": "1975-05-05",
                                       "phone": "3125550111", "email": "eli.dayoff@example.com"})
    doctors = {p["full_name"]: p["id"] for p in client.get("/api/providers").json()}
    # Find a weekday on which Dr. Torres has no slot at all, and ask for exactly that day.
    day = next(d for d in (date.today() + timedelta(days=n) for n in range(2, 21))
               if d.weekday() < 5 and not client.get(f"/api/providers/{doctors['Dr. Elena Torres']}/slots",
                                                     params={"day": d.isoformat()}).json())
    sent = client.post("/api/intake/email", json={
        "from_address": "eli.dayoff@example.com", "from_name": "Eli Dayoff", "subject": "Appointment",
        "body": f"Hi, this is Eli Dayoff. Please book me with Dr. Elena Torres on {day.isoformat()}. My date of "
                "birth is 05/05/1975 and my phone number is (312) 555-0111.\n\nThanks,\nEli Dayoff",
        "process": True}).json()["request"]
    # Not booked on a day she is not in; answered at once with her nearest openings instead of waiting for staff.
    assert sent["status"] == "auto_responded" and sent["appointment"] is None, sent["review_reasons"]
    assert "Dr. Elena Torres" in sent["draft_response"]
    reply = next(e for e in sent["emails"] if e["direction"] == "outbound")
    assert "Dr. Elena Torres" in reply["body_text"] and "1." in reply["body_text"]  # the openings, numbered


def test_a_new_patient_is_asked_for_the_demographics_the_email_lacked(client):
    req = inject(client, "new_patient")  # Olivia Bennett: gave name, date of birth, phone and insurance
    reply = req["draft_response"]
    assert "reply to this email with" in reply
    assert "home address" in reply and "Your sex" in reply  # what the email did not say
    assert "date of birth" not in reply.lower().split("reply to this email with")[1]  # already given: not asked again
    assert "insurance company, member ID" not in reply  # she gave Aetna and a member ID
    again = client.post(f"/api/requests/{req['id']}/draft-reply", json={}).json()
    assert again["kind"] == "request_details" and "home address" in again["body"]


def test_a_website_form_with_only_a_name_and_contact_is_asked_for_everything_else(client):
    sent = client.post("/api/intake/email", json={
        "from_address": "notifications@cognitoforms.com", "from_name": "Website Form",
        "subject": "Message from website - Las Vegas",
        "body": "Website Form\nNew Appointment Request\nName GARRETT CUTTING\nEmail scsbgc@hotmail.com\n"
                "Phone Number (702) 281-0653\nHow Can We Help? lower back pain, new patient, please schedule me\n",
        "process": True}).json()["request"]
    if sent["patient"] is None and sent["intent"] == "new_appointment":
        body = sent["draft_response"]
        assert "Hello Garrett" in body or "Hello GARRETT" in body or "Hello there" in body
        assert "date of birth" in body.lower() and "insurance" in body.lower()
        assert "phone number" not in body.lower()  # the form gave one


BILL_BODY = (
    "Hello,\n\nPlease see the attached bill.\n\nThank you,\nYahaira Padilla\n\nPhone: 949.743.9595 | Fax: 949.743.1633\n\n"
    "Please allow 24-48 hours for an emailed response.\n\nIMPORTANT WARNING: This message is intended for the use of the "
    "person or entity to which it is addressed and may contain privileged and confidential information. If you are not "
    "the intended recipient, schedule no visit and delete this appointment-related message."
)


def test_a_bill_about_a_patient_is_not_treated_as_an_appointment_request(client):
    sent = client.post("/api/intake/email", json={
        "from_address": "ypadilla@mdcsmso.com", "from_name": "Yahaira Padilla",
        "subject": "Re: Alfredo Perez (DOB: 11/20/1971)", "body": BILL_BODY, "process": True}).json()["request"]
    assert sent["intent"] == "billing_records" and sent["status"] == "needs_review"
    codes = {r["code"] for r in sent["review_reasons"]}
    assert "UNSUPPORTED_INTENT" in codes and "UNMATCHED_PATIENT" not in codes  # not a registration problem
    patient = sent["extracted"]["patient"]
    assert (patient["first_name"], patient["last_name"], patient["date_of_birth"]) == ("Alfredo", "Perez", "1971-11-20")
    assert patient["requester_is_patient"] is False  # the person writing is not the patient
    reply = sent["draft_response"]
    assert "handles appointment scheduling" in reply and "new patient" not in reply
    assert reply.startswith("Hello Yahaira")  # addressed to the sender, not the patient named in the subject


def test_a_real_request_with_a_footer_still_books(client):
    req = inject(client, "auto_book")
    assert req["intent"] == "new_appointment" and req["status"] == "resolved"


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


class _Http:
    def __init__(self, status=200, payload=None):
        self.status, self.payload, self.calls = status, payload, 0

    def post(self, url, headers=None, json=None):
        self.calls += 1
        return _Resp(self.status, self.payload)

    def close(self):
        pass


def _settings(monkeypatch, key="hf_test"):
    from types import SimpleNamespace

    from app.services import email_classifier as ec

    monkeypatch.setattr(ec, "get_settings", lambda: SimpleNamespace(
        huggingface_api_key=key, email_classifier="auto", email_classifier_model="facebook/bart-large-mnli"))
    monkeypatch.setattr(ec, "_down_until", 0.0)
    return ec


def test_the_huggingface_classifier_can_move_a_bill_out_of_scheduling(monkeypatch):
    ec = _settings(monkeypatch)
    ok = _Http(200, [{"label": "a bill, invoice, payment, medical records or legal case document", "score": 0.93},
                     {"label": "a request to book a new appointment or see a doctor", "score": 0.04}])
    monkeypatch.setattr(ec.httpx, "Client", lambda **k: ok)
    intent, conf, note = ec.refine("new_appointment", 0.9, "Re: bill", "Please see the attached bill.")
    assert (intent, conf) == ("billing_records", 0.93) and note.startswith("huggingface:")
    # but an explicit cancel is never overridden
    assert ec.refine("cancel", 0.9, "cancel", "please cancel my visit")[0] == "cancel"


def test_without_credits_the_rules_decide_and_the_model_is_not_retried(monkeypatch):
    ec = _settings(monkeypatch)
    down = _Http(402, {"error": "no remaining credits"})
    monkeypatch.setattr(ec.httpx, "Client", lambda **k: down)
    first = ec.refine("new_appointment", 0.9, "s", "book me")
    assert first[0] == "new_appointment" and "HTTP 402" in first[2]
    ec.refine("new_appointment", 0.9, "s", "book me")
    assert down.calls == 1  # the second email did not call the model again
    monkeypatch.setattr(ec, "_down_until", 0.0)
    ec2 = _settings(monkeypatch, key="")
    assert ec2.refine("other", 0.4, "s", "x")[2] == "rules"  # no key: rules, no call
