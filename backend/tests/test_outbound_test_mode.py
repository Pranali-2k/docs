"""Outbound test mode: replies wait for approval and reach ONLY the test address, never the patient."""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import delivery
from app.services.mailer import MailResult

TESTER = "pranali@ashunya.com"


@pytest.fixture
def admin(db):
    s = get_settings()
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password}).json()
    c.headers["Authorization"] = f"Bearer {tok['access_token']}"
    return c


@pytest.fixture
def wire(monkeypatch):
    sent = []

    def fake(to, subject, body_text=None, **kw):
        sent.append({"to": to, "subject": subject, "body": body_text})
        return MailResult(ok=True, accepted=[to])

    monkeypatch.setattr(delivery, "deliver", fake)
    monkeypatch.setattr(get_settings(), "email_outbound_mode", "smtp")
    return sent


def auto_booked(client):
    r = client.post("/api/demo/samples/auto_book")
    assert r.status_code == 201, r.text
    req = r.json()["request"]
    return req, [e for e in req["emails"] if e["direction"] == "outbound"][0]


def test_replies_wait_and_go_only_to_the_tester(client, admin, wire):
    on = admin.put("/api/integrations/outbound/test-mode", json={"enabled": True, "recipient": TESTER})
    assert on.status_code == 200 and on.json()["test_mode"]["recipient"] == TESTER
    req, reply = auto_booked(client)
    assert reply["send_status"] == "awaiting_approval" and reply["redirect_to"] == TESTER
    assert wire == []  # nothing was sent anywhere, even though outbound email is ON in tests
    assert client.post(f"/api/emails/{reply['id']}/resend").status_code == 409  # can't bypass approval

    ok = client.post(f"/api/emails/{reply['id']}/approve-test")
    assert ok.status_code == 200, ok.text
    assert ok.json()["send_status"] == "test_sent" and ok.json()["to_address"] == reply["to_address"]
    assert [m["to"] for m in wire] == [TESTER]  # delivered to the tester only
    assert reply["to_address"] in wire[0]["subject"] and "TEST MODE" in wire[0]["body"]
    assert client.post(f"/api/emails/{reply['id']}/approve-test").status_code == 409  # once only


def test_failed_test_delivery_stays_waiting_and_explains(client, admin, monkeypatch):
    monkeypatch.setattr(delivery, "deliver", lambda *a, **k: MailResult(ok=False, error_kind="not_configured",
                                                                         error="outbox mode"))
    admin.put("/api/integrations/outbound/test-mode", json={"enabled": True, "recipient": TESTER})
    _, reply = auto_booked(client)
    res = client.post(f"/api/emails/{reply['id']}/approve-test").json()
    assert res["send_status"] == "awaiting_approval" and "could not be sent" in res["send_error"]


def test_held_replies_can_be_approved_and_turning_off_restores_normal_rules(client, admin, wire):
    admin.put("/api/integrations/outbound", json={"enabled": False})
    _, held = auto_booked(client)
    assert held["send_status"] == "held"
    assert client.post(f"/api/emails/{held['id']}/approve-test").status_code == 409  # test mode is off
    admin.put("/api/integrations/outbound/test-mode", json={"enabled": True, "recipient": TESTER})
    assert client.post(f"/api/emails/{held['id']}/approve-test").json()["send_status"] == "test_sent"
    assert [m["to"] for m in wire] == [TESTER]

    admin.put("/api/integrations/outbound/test-mode", json={"enabled": False})
    admin.put("/api/integrations/outbound", json={"enabled": True})
    _, live = auto_booked(client)
    assert live["send_status"] == "sent" and wire[-1]["to"] == live["to_address"]


def test_only_admins_switch_test_mode_and_recipient_is_required(client, admin):
    assert client.put("/api/integrations/outbound/test-mode", json={"enabled": True, "recipient": TESTER}).status_code == 403
    assert admin.put("/api/integrations/outbound/test-mode", json={"enabled": True}).status_code == 422
    assert admin.put("/api/integrations/outbound/test-mode", json={"enabled": True, "recipient": "nope"}).status_code == 422
