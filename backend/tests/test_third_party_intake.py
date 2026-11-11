"""Law-firm / case-manager emails: the patient's details come from the filled-in form, not the sender's signature."""
from datetime import date

from app.services.extraction import ExtractionContext, extract_with_rules

THREAD = """Please schedule it for 12/21 at 10 am.

First & Last name: Jane Sample

Last four of your SSN 4321

DOB 04/12/1961

Gender Female

Phone number 8015550142

Email address jane.sample@example.com

Home address
742 W. Example Ridge Rd.
Springfield, Utah 84780

Thank you,

Pat Paralegal
Senior Litigation Paralegal
100 Main St | Suite 410 | Marina del Rey, California 90292 | USA |
Telephone: (310) 555-2800 | Facsimile: (310) 555-4200 | pparalegal@lawfirm.example |

From: RM Assistant <rmassistant@clinic.example>
Sent: Tuesday, October 6, 2026 12:43 PM
Subject: Re: Jane Sample

Start time would be 8 am. We can schedule the client anytime between 8 am and 12 pm with the exception of 10:30 am.
First & Last name
Last four of your SSN
DOB
Phone number
"""


def ctx():
    return ExtractionContext(reference_date=date(2026, 10, 7), sender_email="pparalegal@lawfirm.example",
                             sender_name="Pat Paralegal", payers=[], providers=["Dr. Priya Rao"])


def test_form_fields_belong_to_the_patient_not_the_sender():
    x = extract_with_rules("RE: Jane Sample", THREAD, ctx())
    p = x.patient
    assert (p.first_name, p.last_name, p.date_of_birth) == ("Jane", "Sample", "1961-04-12")
    assert p.phone == "8015550142"            # not the firm's (310) number
    assert p.email == "jane.sample@example.com"  # not the paralegal's address
    assert p.sex == "female" and p.ssn_last4 == "4321"
    assert (p.address_line1, p.city, p.state, p.postal_code) == ("742 W. Example Ridge Rd.", "Springfield", "UT", "84780")
    assert p.requester_is_patient is False and p.requester_relationship == "third_party"
    assert x.intent == "new_appointment"
    assert x.appointment.preferred_dates[0] == "2026-12-21" and x.appointment.preferred_time == "10:00"


def test_patient_writing_for_themselves_still_uses_sender_details():
    c = ExtractionContext(reference_date=date(2026, 10, 7), sender_email="maria@example.com", sender_name="Maria Gonzalez",
                          payers=[], providers=[])
    x = extract_with_rules("Appointment", "Hi, I'd like to book a check-up next Tuesday at 2:30 pm. DOB 03/14/1985, "
                                          "my cell is 312-555-0101.\nThanks,\nMaria Gonzalez", c)
    assert x.patient.requester_is_patient and x.patient.email == "maria@example.com"
    assert x.patient.phone == "3125550101" and x.appointment.preferred_time == "14:30"


# ---------------------------------------------------------------- API: draft chart + exact-time booking

from datetime import datetime  # noqa: E402

from app.config import get_settings  # noqa: E402


def login(client, who="scheduler"):
    s = get_settings()
    email, pw = (s.seed_scheduler_email, s.seed_scheduler_password) if who == "scheduler" else (s.seed_admin_email, s.seed_admin_password)
    tok = client.post("/api/auth/login", json={"email": email, "password": pw}).json()
    client.headers["Authorization"] = f"Bearer {tok['access_token']}"


def intake(client, body, sender="pparalegal@lawfirm.example", name="Pat Paralegal", subject="RE: new client"):
    r = client.post("/api/intake/email", json={"from_address": sender, "from_name": name, "subject": subject, "body": body})
    assert r.status_code == 201, r.text
    return r.json()["request"]


def when(slot):
    dt = datetime.fromisoformat(slot["start_at"])
    return dt, f"{dt.month}/{dt.day} at {dt.strftime('%I:%M %p').lstrip('0').lower()}"


FORM = ("Please schedule an appointment {when}.\n\nFirst & Last name: {first} Sample\nDOB 04/12/1961\nGender Female\n"
        "Phone number 8015550142\nEmail address {first}.sample@example.com\nHome address\n742 W. Example Ridge Rd.\n"
        "Springfield, Utah 84780\n\nThanks,\nPat Paralegal\nTelephone: (310) 555-2800")


def test_draft_chart_is_proposed_then_approved_with_booking(client):
    login(client)
    r = intake(client, FORM.format(when="next week in the morning", first="Jane"))
    d = client.get(f"/api/requests/{r['id']}").json()
    draft = d["draft_chart"]
    assert d["status"] == "needs_review" and d["patient"] is None and draft["status"] == "pending"
    assert draft["patient"]["last_name"] == "Sample" and draft["patient"]["state"] == "UT"
    assert draft["missing_required"] == [] and draft["proposed"] and draft["requester"]["is_patient"] is False
    assert any(x["code"] == "UNMATCHED_PATIENT" and "draft chart" in x["message"] for x in d["review_reasons"])

    # a second client asks for the exact slot the AI just proposed -> it is proposed as an exact match
    dt, phrase = when(draft["proposed"])
    r2 = intake(client, FORM.format(when=f"on {phrase}", first="June"))
    d2 = client.get(f"/api/requests/{r2['id']}").json()["draft_chart"]
    assert d2["proposed"]["start_at"] == draft["proposed"]["start_at"] and d2["matches_request"] is True

    # nothing is created until staff approve; then chart + booking + reply happen together
    before = client.get("/api/patients", params={"q": "Sample"}).json()
    ok = client.post(f"/api/requests/{r['id']}/draft-chart/approve", json={
        "patient": {"phone": "801-555-0143"}, "book": {"provider_id": draft["proposed"]["provider_id"],
                                                        "start_at": draft["proposed"]["start_at"]}})
    assert ok.status_code == 200, ok.text
    done = ok.json()
    assert done["status"] == "resolved" and done["patient"]["mrn"] and done["appointment"]
    assert done["draft_chart"]["status"] == "approved" and done["draft_chart"]["mrn"] == done["patient"]["mrn"]
    chart = client.get(f"/api/patients/{done['patient']['id']}").json()
    assert "".join(c for c in chart["phone"] if c.isdigit()) == "8015550143" and chart["sex"] == "female" and not chart.get("ssn_masked")
    reply = [e for e in done["emails"] if e["direction"] == "outbound"][0]
    assert reply["to_address"] == "pparalegal@lawfirm.example"  # answered to whoever wrote in
    assert len(client.get("/api/patients", params={"q": "Sample"}).json()) == len(before) + 1

    # the slot is now taken: approving the second draft for the same slot fails and creates nothing
    bad = client.post(f"/api/requests/{r2['id']}/draft-chart/approve", json={
        "book": {"provider_id": d2["proposed"]["provider_id"], "start_at": d2["proposed"]["start_at"]}})
    assert bad.status_code == 409
    assert len(client.get("/api/patients", params={"q": "Sample"}).json()) == len(before) + 1
    assert client.get(f"/api/requests/{r2['id']}").json()["draft_chart"]["status"] == "pending"
    # approve twice / discard
    assert client.post(f"/api/requests/{r['id']}/draft-chart/approve", json={}).status_code == 409
    gone = client.post(f"/api/requests/{r2['id']}/draft-chart/discard", json={"reason": "duplicate referral"}).json()
    assert gone["draft_chart"]["status"] == "discarded"


def test_draft_requires_dob_and_catches_duplicates(client):
    login(client)
    r = intake(client, "Please book an appointment next week.\nFirst & Last name: Noah Nodob\nPhone number 8015550150")
    draft = client.get(f"/api/requests/{r['id']}").json()["draft_chart"]
    assert draft["missing_required"] == ["date_of_birth"]
    res = client.post(f"/api/requests/{r['id']}/draft-chart/approve", json={})
    assert res.status_code == 409 and "date of birth" in res.text
    ok = client.post(f"/api/requests/{r['id']}/draft-chart/approve", json={"patient": {"date_of_birth": "1990-01-02"}})
    assert ok.status_code == 200 and ok.json()["patient"]

    again = intake(client, "Please book an appointment next week.\nFirst & Last name: Noah Nodob\nDOB 01/02/1990")
    d = client.get(f"/api/requests/{again['id']}").json()
    # same name + DOB as the chart just created: offered to staff as a match to link, never drafted again
    assert d["draft_chart"] is None, d["draft_chart"]
    assert d["patient"] or (d["match_details"]["status"] == "ambiguous"
                            and ok.json()["patient"]["id"] in [c["patient_id"] for c in d["match_details"]["candidates"]])


def test_known_patient_gets_the_exact_time_asked_for(client):
    login(client)
    probe = intake(client, "Hi, I'd like to schedule my annual physical with Dr. Rao next week. DOB 03/14/1985.\n"
                           "Thanks,\nMaria Gonzalez", sender="maria.gonzalez@example.com", name="Maria Gonzalez")
    rao = next(p for p in client.get("/api/providers").json() if "Rao" in p["full_name"])
    slots = client.get(f"/api/requests/{probe['id']}/slots", params={"provider_id": rao["id"]}).json()["slots"]
    target = slots[-1]  # not the first free slot, so a match proves the exact time was honoured
    dt, phrase = when(target)
    if probe["appointment"]:
        client.post(f"/api/appointments/{probe['appointment']['id']}/cancel")
    r = intake(client, f"Hi, please book my annual physical with Dr. Rao on {phrase}. DOB 03/14/1985.\n"
                       "Thanks,\nMaria Gonzalez", sender="maria.gonzalez@example.com", name="Maria Gonzalez")
    assert r["status"] == "resolved", r["review_reasons"]
    assert datetime.fromisoformat(r["appointment"]["start_at"]) == dt

    # same time again: it's taken, so the assistant answers at once with Dr. Rao's nearest openings and books nothing
    r2 = intake(client, f"Hi, please book a follow-up with Dr. Rao on {phrase}. DOB 03/14/1985.\nThanks,\nMaria Gonzalez",
                sender="maria.gonzalez@example.com", name="Maria Gonzalez")
    assert r2["status"] == "auto_responded" and not r2["appointment"], r2["review_reasons"]
    reply = next(e for e in r2["emails"] if e["direction"] == "outbound")
    assert "Dr. Anita Rao" in reply["body_text"] and "is not available on" in reply["body_text"] and "Please reschedule" in reply["body_text"]


def test_address_after_a_blank_line_is_found():
    from app.services.extraction import _find_address

    text = "Home address\n\n1054 E. Example Hills Dr.\nSpringfield, Utah 84780\n"
    assert _find_address(text) == {"address_line1": "1054 E. Example Hills Dr.", "city": "Springfield",
                                   "state": "UT", "postal_code": "84780"}


def test_sentence_ending_email_and_parent_contact():
    from app.services.normalize import normalize_email

    assert normalize_email("avery.brooks@example.com.") == "avery.brooks@example.com"
    assert normalize_email("<a@b.org>,") == "a@b.org"
    x = extract_with_rules("Appt", "My email is avery.brooks@example.com. Please book a visit next week. DOB 05/14/1988.\n"
                                   "Thanks,\nAvery Brooks", ctx())
    assert x.patient.email == "avery.brooks@example.com"
    kid = extract_with_rules("Appointment for my daughter", "Hi, I'd like to book a check-up for my daughter Mia Park "
                             "next week. Her DOB is 02/11/2016. You can reach me at 312-555-0199.\nThanks,\nJordan Park",
                             ExtractionContext(reference_date=date(2026, 10, 7), sender_email="jordan.park@example.com",
                                               sender_name="Jordan Park", payers=[], providers=[]))
    assert (kid.patient.first_name, kid.patient.requester_relationship) == ("Mia", "daughter")
    assert kid.patient.phone is None and kid.patient.email is None  # never identifies the child by the parent
    assert (kid.patient.contact_phone, kid.patient.contact_email) == ("3125550199", "jordan.park@example.com")


def test_cancel_with_month_day_identifies_the_appointment():
    x = extract_with_rules("Cancel", "Hi, please cancel my appointment on 10/12. DOB 05/14/1988.\nThanks,\nAvery Brooks", ctx())
    assert x.intent == "cancel" and x.appointment.current_appointment_date == "2026-10-12"
    assert "2026-10-12" not in x.appointment.preferred_dates


def test_new_request_on_a_date_is_not_an_existing_appointment():
    x = extract_with_rules("Appt", "Please schedule an appointment on 10/12 at 9:00 am.\nFirst & Last name: Jane Sample", ctx())
    assert x.appointment.current_appointment_date is None and x.appointment.preferred_dates[0] == "2026-10-12"
