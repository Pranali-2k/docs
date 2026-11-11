"""Admin email-settings API: real test send via a local SMTP server, save-on-success, .env safety."""
import socket

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import envfile


class Inbox:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


@pytest.fixture
def smtp_server():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    inbox = Inbox()
    ctl = Controller(inbox, hostname="127.0.0.1", port=port, auth_require_tls=False,
                     authenticator=lambda *a: AuthResult(success=True))  # accepts any login
    ctl.start()
    yield inbox, port
    ctl.stop()


@pytest.fixture
def admin(db):
    s = get_settings()
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password}).json()
    c.headers["Authorization"] = f"Bearer {tok['access_token']}"
    return c


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SMTP_HOST=smtp.office365.com\nSMTP_PASSWORD=\nEMAIL_OUTBOUND_MODE=outbox\n", encoding="utf-8")
    monkeypatch.setattr(envfile, "ENV_PATH", env)
    from app.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", env)  # reload settings from the temp file too
    yield env
    get_settings.cache_clear()


def body(port, **over):
    b = {"smtp_host": "127.0.0.1", "smtp_port": port, "smtp_security": "none", "smtp_username": "",
         "smtp_password": "", "smtp_from": "pranali@ashunya.com", "to": "pranali@ashunya.com",
         "save_on_success": True, "enable_outbound": True}
    b.update(over)
    return b


def test_only_admins_can_use_it(client):
    assert client.get("/api/system/email-settings").status_code == 403
    assert client.post("/api/system/email-settings/test", json=body(25)).status_code == 403


def test_successful_test_send_saves_settings(admin, smtp_server, temp_env):
    inbox, port = smtp_server
    r = admin.post("/api/system/email-settings/test", json=body(port))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["saved"] and out["accepted"] == ["pranali@ashunya.com"]
    env = dotenv_values(temp_env)
    assert env["SMTP_FROM"] == "pranali@ashunya.com" and env["EMAIL_OUTBOUND_MODE"] == "smtp"
    assert inbox.messages and inbox.messages[0].mail_from == "pranali@ashunya.com"
    log = admin.get("/api/emails", params={"direction": "outbound"}).json()
    assert log[0]["source"] == "test" and log[0]["send_status"] == "sent"


def test_failed_send_saves_nothing_and_reports_reason(admin, temp_env):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    before = temp_env.read_text()
    out = admin.post("/api/system/email-settings/test", json=body(dead)).json()
    assert not out["ok"] and not out["saved"] and out["error_kind"] == "connection"
    assert temp_env.read_text() == before
    log = admin.get("/api/emails", params={"direction": "outbound"}).json()
    assert log[0]["send_status"] == "failed" and "connection" in log[0]["send_error"]


def test_password_is_never_returned(admin, smtp_server, temp_env):
    _, port = smtp_server
    r = admin.post("/api/system/email-settings/test",
                   json=body(port, smtp_username="pranali@ashunya.com", smtp_password="Sup3r$ecret#1"))
    assert r.json()["ok"] and r.json()["saved"]
    assert "Sup3r$ecret#1" not in r.text
    assert "Sup3r$ecret#1" not in admin.get("/api/system/email-settings").text
    assert admin.get("/api/system/email-settings").json()["password_set"] is True


@pytest.mark.parametrize("value", ["p@ss w#rd", "$HOME", 'quo"te', "it's", "back\\slash", "!@#$%^&*()", "Pranal!@123"])
def test_env_values_round_trip_exactly(tmp_path, value):
    env = tmp_path / ".env"
    env.write_text("OTHER=keep\n", encoding="utf-8")
    envfile.update_env({"SMTP_PASSWORD": value}, path=env)
    assert dotenv_values(env)["SMTP_PASSWORD"] == value
    assert dotenv_values(env)["OTHER"] == "keep"
    assert (tmp_path / ".env.bak").exists()


def test_env_rejects_multiline_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        envfile.update_env({"SMTP_PASSWORD": "a\nINJECTED=1"}, path=env)
