"""The API turns failures into clear JSON errors instead of crashes or stack traces."""
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api import system
from app.main import app


def test_unhandled_error_returns_reference_not_traceback(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(system.scheduling, "clinic_now", boom)
    c = TestClient(app, raise_server_exceptions=False)
    c.headers.update(client.headers)
    r = c.get("/api/dashboard/summary")
    assert r.status_code == 500
    body = r.json()
    assert "Reference:" in body["detail"] and body["error_id"]
    assert "secret internal detail" not in r.text


def test_database_outage_maps_to_503(client, monkeypatch):
    def down(*a, **k):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(system.scheduling, "clinic_now", down)
    c = TestClient(app, raise_server_exceptions=False)
    c.headers.update(client.headers)
    r = c.get("/api/dashboard/summary")
    assert r.status_code == 503 and "temporarily unavailable" in r.json()["detail"]


def test_validation_and_not_found(client):
    assert client.post("/api/intake/email", json={"from_address": "not-an-email", "body": "x"}).status_code == 422
    assert client.get("/api/requests/999999").status_code == 404
    assert client.get("/api/patients/999999").status_code == 404
    assert client.post("/api/appointments/999999/cancel").status_code == 404


def test_health_reports_database(db):
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_failed_email_delivery_is_recorded_not_raised(client, monkeypatch):
    from app.config import get_settings
    from app.services import delivery

    def boom(*a, **k):  # even if a transport misbehaves and raises, the request must still succeed
        raise ConnectionRefusedError("smtp down")

    monkeypatch.setattr(get_settings(), "email_outbound_mode", "smtp")
    monkeypatch.setattr(delivery, "deliver", boom)
    r = client.post("/api/demo/samples/auto_book")
    assert r.status_code == 201
    out = [e for e in r.json()["request"]["emails"] if e["direction"] == "outbound"]
    assert out and out[0]["send_status"] == "failed" and "smtp down" in out[0]["send_error"]
    assert out[0]["delivery_method"] == "smtp"
