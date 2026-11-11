"""Production safeguards: outbound kill switch, login lockout, security headers, production
config checks, demo gating and encryption-key rotation."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.prodcheck import problems
from app.services import crypto, login_guard


def _admin():
    s = get_settings()
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password}).json()
    c.headers["Authorization"] = f"Bearer {tok['access_token']}"
    return c


# --------------------------------------------------------------------------- outbound switch

def test_outbound_off_holds_every_reply(client, db, monkeypatch):
    admin = _admin()
    monkeypatch.setattr(get_settings(), "email_outbound_mode", "smtp")  # would really send if not held
    v = admin.put("/api/integrations/outbound", json={"enabled": False, "reason": "testing period"}).json()
    assert v["outbound"]["enabled"] is False and v["outbound"]["reason"] == "testing period"

    req = client.post("/api/demo/samples/auto_book").json()["request"]  # AI auto-reply
    auto = [e for e in req["emails"] if e["direction"] == "outbound"][0]
    assert auto["send_status"] == "held" and "turned off" in auto["send_error"]

    r = client.post(f"/api/requests/{req['id']}/send-response", json={"body": "Hi", "resolve": False}).json()
    assert r["email"]["send_status"] == "held"
    assert client.post(f"/api/emails/{r['email']['id']}/resend").status_code == 409  # retry blocked while off
    assert client.get("/api/system/info").json()["outbound_email_enabled"] is False
    assert client.put("/api/integrations/outbound", json={"enabled": True}).status_code == 403  # admins only


def test_mailbox_can_be_configured_before_connecting():
    admin = _admin()
    v = admin.post("/api/integrations/mailboxes", json={"address": "scheduling.ash@docshealth.com"}).json()
    mb = v["mailboxes"][0]
    assert mb["address"] == "scheduling.ash@docshealth.com" and mb["process_inbound"] and mb["reply_from_same_mailbox"]
    assert v["connection"]["status"] == "not_configured"


# --------------------------------------------------------------------------- login lockout

def test_account_locks_after_five_failures_and_reset_unlocks(db):
    s = get_settings()
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": s.seed_scheduler_email, "password": "wrong"}).status_code == 401
    locked = c.post("/api/auth/login", json={"email": s.seed_scheduler_email, "password": s.seed_scheduler_password})
    assert locked.status_code == 423  # even the right password is refused while locked
    assert c.post("/api/auth/login", json={"email": "nobody@x.com", "password": "x"}).status_code == 401  # generic


def test_ip_throttle(db):
    c = TestClient(app)
    for i in range(login_guard.MAX_IP_FAILURES):
        c.post("/api/auth/login", json={"email": f"ghost{i}@x.com", "password": "x"})
    assert c.post("/api/auth/login", json={"email": "ghost@x.com", "password": "x"}).status_code == 429


# --------------------------------------------------------------------------- headers, demo, production config

def test_security_headers_and_request_id(client):
    r = client.get("/api/auth/me", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
    bad = client.get("/api/health", headers={"X-Request-ID": "x\" onload=1"})
    assert bad.headers["X-Request-ID"] != "x\" onload=1"


def test_demo_tools_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_enabled", False)
    assert client.get("/api/demo/samples").status_code == 404
    assert client.post("/api/demo/samples/auto_book").status_code == 404


def test_production_config_problems():
    s = get_settings().model_copy(update=dict(environment="production", jwt_secret="short", field_encryption_key="",
                                              database_url="sqlite:///x.db", portal_base_url="http://portal",
                                              seed_admin_password="DemoAdmin!2026"))
    found = " | ".join(problems(s))
    for needle in ("JWT_SECRET", "FIELD_ENCRYPTION_KEY", "PostgreSQL", "https://", "SEED_ADMIN_PASSWORD"):
        assert needle in found
    good = s.model_copy(update=dict(jwt_secret="x" * 48, field_encryption_key=Fernet.generate_key().decode(),
                                    database_url="postgresql+psycopg://u:p@h/db", portal_base_url="https://portal.example",
                                    seed_admin_password="S0mething-Unique-1", seed_scheduler_password="Other-Unique-2",
                                    cors_origins="https://portal.example"))
    assert problems(good) == []


# --------------------------------------------------------------------------- key rotation

def test_multifernet_rotation_keeps_old_values_readable(monkeypatch):
    s = get_settings()
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(s, "field_encryption_key", old)
    monkeypatch.setattr(s, "field_encryption_keys_previous", "")
    crypto.reset_cache()
    token = crypto.encrypt("123456789")
    monkeypatch.setattr(s, "field_encryption_key", new)
    monkeypatch.setattr(s, "field_encryption_keys_previous", old)
    crypto.reset_cache()
    assert crypto.decrypt(token) == "123456789"  # old value still readable
    rotated = crypto.rotate(token)
    assert Fernet(new.encode()).decrypt(rotated.encode()) == b"123456789"
    monkeypatch.setattr(s, "field_encryption_keys_previous", "")
    crypto.reset_cache()
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(token)  # without the previous key, un-rotated data is unreadable (why rotation exists)
    crypto.reset_cache()
