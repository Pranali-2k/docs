"""Password reset with email identity verification, end to end through the API."""
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models import EmailMessage, PasswordResetToken

NEW_PASSWORD = "Fresh-Start-2026!"
GENERIC = "If an account exists for that email"


def _latest_link(db, to):
    db.expire_all()
    msg = db.scalars(
        select(EmailMessage).where(EmailMessage.to_address == to, EmailMessage.subject.like("Reset your%"))
        .order_by(EmailMessage.id.desc())
    ).first()
    assert msg is not None, "no reset email was sent"
    m = re.search(r"http://portal\.test/reset-password\?token=([\w-]+)", msg.body_text)
    assert m, msg.body_text
    return msg, m.group(1)


def test_full_reset_flow(db):
    s = get_settings()
    c = TestClient(app)
    email = s.seed_scheduler_email
    old = c.post("/api/auth/login", json={"email": email, "password": s.seed_scheduler_password}).json()["access_token"]

    r = c.post("/api/auth/forgot-password", json={"email": email.upper()})
    assert r.status_code == 202 and GENERIC in r.json()["message"]
    msg, token = _latest_link(db, email)
    assert msg.send_status == "stored" and msg.source == "security"
    assert any(Path(os.environ["MAIL_DROP_DIR"]).glob("*.eml")), "outbox mode writes an .eml for local use"

    v = c.get("/api/auth/reset-password/verify", params={"token": token})
    assert v.status_code == 200 and v.json()["email"].endswith("@docshealth.local")
    assert email not in v.json()["email"]  # masked

    weak = c.post("/api/auth/reset-password", json={"token": token, "new_password": "short"})
    assert weak.status_code == 400 and "at least" in weak.json()["detail"]

    ok = c.post("/api/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD})
    assert ok.status_code == 200, ok.text

    # new password works, old one doesn't, old sessions are revoked, the link is single-use
    assert c.post("/api/auth/login", json={"email": email, "password": NEW_PASSWORD}).status_code == 200
    assert c.post("/api/auth/login", json={"email": email, "password": s.seed_scheduler_password}).status_code == 401
    me = c.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"})
    assert me.status_code == 401 and "password change" in me.json()["detail"]
    reuse = c.post("/api/auth/reset-password", json={"token": token, "new_password": "Another-Pass-2026!"})
    assert reuse.status_code == 400 and "already been used" in reuse.json()["detail"]

    # the user is told the password changed
    db.expire_all()
    assert db.scalar(select(EmailMessage).where(EmailMessage.subject.like("%password was changed")))


def test_unknown_email_gets_identical_answer_and_no_mail(db):
    c = TestClient(app)
    r = c.post("/api/auth/forgot-password", json={"email": "nobody@nowhere.example"})
    assert r.status_code == 202 and GENERIC in r.json()["message"]
    assert db.scalar(select(EmailMessage).where(EmailMessage.to_address == "nobody@nowhere.example")) is None


def test_expired_invalid_and_superseded_links(db):
    s = get_settings()
    c = TestClient(app)
    c.post("/api/auth/forgot-password", json={"email": s.seed_admin_email})
    _, first = _latest_link(db, s.seed_admin_email)
    c.post("/api/auth/forgot-password", json={"email": s.seed_admin_email})
    _, second = _latest_link(db, s.seed_admin_email)
    assert first != second
    assert c.get("/api/auth/reset-password/verify", params={"token": first}).status_code == 400  # superseded
    assert c.get("/api/auth/reset-password/verify", params={"token": "not-a-real-token"}).status_code == 400

    row = db.scalars(select(PasswordResetToken).order_by(PasswordResetToken.id.desc())).first()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    r = c.post("/api/auth/reset-password", json={"token": second, "new_password": NEW_PASSWORD})
    assert r.status_code == 400 and "expired" in r.json()["detail"]


def test_reset_requests_are_throttled(db):
    s = get_settings()
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/forgot-password", json={"email": s.seed_admin_email}).status_code == 202
    db.expire_all()
    sent = db.scalars(select(EmailMessage).where(EmailMessage.to_address == s.seed_admin_email)).all()
    assert len(sent) == s.password_reset_max_per_window


def test_reset_rejects_current_password(db):
    s = get_settings()
    c = TestClient(app)
    c.post("/api/auth/forgot-password", json={"email": s.seed_scheduler_email})
    _, token = _latest_link(db, s.seed_scheduler_email)
    r = c.post("/api/auth/reset-password", json={"token": token, "new_password": s.seed_scheduler_password})
    assert r.status_code == 400 and "different" in r.json()["detail"]
    # the link survives a rejected attempt
    assert c.get("/api/auth/reset-password/verify", params={"token": token}).status_code == 200
