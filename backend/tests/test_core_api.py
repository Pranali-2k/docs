"""The API on top of the core database logic: procedures behind the endpoints, stamping and history through real
requests, reports and role limits, and concurrent bookings."""
import threading
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def inject(client, key):
    r = client.post(f"/api/demo/samples/{key}")
    assert r.status_code == 201, r.text
    return r.json()["request"]


@pytest.fixture()
def admin(db):
    s = get_settings()
    c = TestClient(app)
    token = c.post("/api/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password}).json()
    c.headers["Authorization"] = f"Bearer {token['access_token']}"
    return c


@pytest.fixture()
def ids(client):
    """A patient and a provider that exist in the demo seed, plus a Monday far enough ahead to be free."""
    patients = client.get("/api/patients", params={"limit": 50}).json()
    providers = client.get("/api/providers").json()
    day = datetime.now() + timedelta(days=60)
    day += timedelta(days=(7 - day.weekday()) % 7 or 7)       # the next Monday after 60 days
    return {"p1": patients[0]["id"], "p2": patients[1]["id"], "prov": providers[0]["id"], "day": day.replace(hour=9, minute=0, second=0, microsecond=0)}


def book(client, patient, provider, when):
    return client.post("/api/appointments", json={"patient_id": patient, "provider_id": provider, "start_at": when.isoformat(),
                                                  "visit_reason": "check"})


# ----------------------------------------------------------------------- scheduling through the procedures

def test_book_and_cancel_through_the_api_are_audited_and_stamped(client, ids):
    r = book(client, ids["p1"], ids["prov"], ids["day"])
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert client.post(f"/api/appointments/{aid}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/appointments/{aid}/cancel").status_code == 409
    history = " ".join(e["description"] for e in client.get(f"/api/patients/{ids['p1']}/history").json())
    assert "booked" in history.lower() and "cancelled" in history.lower()


def test_double_booking_is_refused_with_a_clear_message(client, ids):
    assert book(client, ids["p1"], ids["prov"], ids["day"]).status_code == 201
    clash = book(client, ids["p2"], ids["prov"], ids["day"])
    assert clash.status_code == 409 and "already booked" in clash.json()["detail"]
    same_patient = book(client, ids["p1"], client.get("/api/providers").json()[1]["id"], ids["day"])
    assert same_patient.status_code == 409 and "already has an appointment" in same_patient.json()["detail"]


def test_a_blocked_time_cannot_be_booked(client, admin, ids, db):
    from app.models import ProviderBlock

    db.add(ProviderBlock(provider_id=ids["prov"], start_at=ids["day"], end_at=ids["day"] + timedelta(hours=1), reason="Lunch",
                         created_by="test"))
    db.commit()
    r = book(client, ids["p1"], ids["prov"], ids["day"] + timedelta(minutes=15))
    assert r.status_code == 409 and "Lunch" in r.json()["detail"]


def test_two_people_booking_the_same_slot_at_once_get_exactly_one_visit(client, ids):
    results = []

    def go(patient):
        c = TestClient(app)
        c.headers.update(client.headers)
        results.append(book(c, patient, ids["prov"], ids["day"]).status_code)

    threads = [threading.Thread(target=go, args=(p,)) for p in (ids["p1"], ids["p2"])]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [201, 409], results


def test_failed_staff_reschedule_keeps_the_original_booking(client, ids):
    req = inject(client, "new_patient")        # any request, to book against
    mine = book(client, ids["p1"], ids["prov"], ids["day"]).json()["id"]
    book(client, ids["p2"], ids["prov"], ids["day"] + timedelta(hours=1))                    # the target slot is taken
    detail = client.get(f"/api/requests/{req['id']}").json()
    client.post(f"/api/requests/{req['id']}/link-patient", json={"patient_id": ids["p1"]})
    r = client.post(f"/api/requests/{req['id']}/book", json={"provider_id": ids["prov"],
                                                              "start_at": (ids["day"] + timedelta(hours=1)).isoformat(),
                                                              "cancel_appointment_id": mine, "send_confirmation": False})
    assert r.status_code == 409, (r.status_code, r.text, detail.get("status"))
    appts = client.get("/api/appointments", params={"start": ids["day"].date().isoformat(), "days": 1}).json()
    assert [a["status"] for a in appts if a["id"] == mine] == ["booked"]


# ----------------------------------------------------------------------- request workflow procedures

def test_assign_resolve_and_reopen_through_the_api(client):
    req = inject(client, "new_patient")
    assert req["status"] == "needs_review"
    assigned = client.post(f"/api/requests/{req['id']}/assign", json={}).json()
    assert assigned["status"] == "in_review" and assigned["assigned_to"]
    assert client.post(f"/api/requests/{req['id']}/resolve", json={"note": ""}).status_code == 422
    done = client.post(f"/api/requests/{req['id']}/resolve", json={"note": "Called the patient"}).json()
    assert done["status"] == "resolved" and done["resolved_by"] and done["resolved_at"]
    reopened = client.post(f"/api/requests/{req['id']}/reopen").json()
    assert reopened["status"] == "needs_review" and reopened["resolved_at"] is None
    assert client.post("/api/requests/999999/reopen").status_code == 404


def test_a_closed_request_cannot_be_resolved_again_without_reopening(client):
    req = inject(client, "general_question")
    client.post(f"/api/requests/{req['id']}/resolve", json={"note": "Not needed", "close": True})
    r = client.post(f"/api/requests/{req['id']}/resolve", json={"note": "Again"})
    assert r.status_code == 409 and "reopened" in r.json()["detail"]
    client.post(f"/api/requests/{req['id']}/reopen")
    assert client.post(f"/api/requests/{req['id']}/resolve", json={"note": "Now answered"}).json()["status"] == "resolved"


# ----------------------------------------------------------------------- stamping and history through the API

def test_changes_made_through_the_api_are_stamped_and_recorded(client, admin, ids):
    s = get_settings()
    client.patch(f"/api/patients/{ids['p1']}", json={"city": "Fresno", "ssn": "123-45-6789"})
    hist = admin.get(f"/api/history/patients/{ids['p1']}").json()
    update = next(h for h in hist if h["operation"] == "update" and "city" in h["changes"])
    assert update["changed_by"] == s.seed_scheduler_email
    assert update["changes"]["city"]["to"] == "Fresno"
    assert update["changes"]["ssn_encrypted"] == {"changed": True}
    assert "123-45-6789" not in str(hist)
    chart = client.get(f"/api/patients/{ids['p1']}").json()
    assert chart["city"] == "Fresno"


def test_history_is_for_admins_and_limited_to_core_tables(client, admin, ids):
    assert client.get(f"/api/history/patients/{ids['p1']}").status_code == 403
    assert admin.get("/api/history/users/1").status_code == 404
    assert admin.get("/api/history/patients/999999").json() == []


# ----------------------------------------------------------------------- dashboard and reports

def test_dashboard_comes_from_the_database_function(client):
    for key in ("auto_book", "new_patient", "red_flag"):
        inject(client, key)
    d = client.get("/api/dashboard/summary", params={"days": 30}).json()
    assert d["total_requests"] == 3 and d["window_days"] == 30
    assert d["auto_responded"] == 1 and d["needs_review"] == 2 and d["auto_response_rate"] == 0.333
    assert d["by_status"]["resolved"] == 1 and d["by_status"]["needs_review"] == 2
    assert "CLINICAL_RED_FLAG" in d["review_reasons"] and d["high_priority_open"] >= 1
    counts = list(d["review_reasons"].values())
    assert counts == sorted(counts, reverse=True)
    assert sum(x["received"] for x in d["daily"]) == 3 and isinstance(d["mailboxes"], list)
    assert client.get("/api/dashboard/summary", params={"days": 0}).json()["window_days"] == 1


def test_turnaround_report_is_admin_only(client, admin):
    inject(client, "auto_book")
    inject(client, "general_question")
    assert client.get("/api/reports/turnaround").status_code == 403
    rows = admin.get("/api/reports/turnaround", params={"days": 30}).json()
    assert {r["intent"] for r in rows} >= {"new_appointment", "general_question"}
    assert sum(r["total"] for r in rows) == 2
    assert admin.get("/api/reports/turnaround", params={"days": 0}).status_code == 422


def test_requests_need_a_signed_in_user(db):
    anon = TestClient(app)
    for url in ("/api/dashboard/summary", "/api/reports/turnaround", "/api/history/patients/1"):
        assert anon.get(url).status_code == 401
