"""The SMTP sender against a real local SMTP server (aiosmtpd): success, auth, refusals,
retries, timeouts, validation, and the email gateway's smtp mode."""
import socket
import threading
import time

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

from app.config import get_settings
from app.services import mailer


class Inbox:
    """Handler that records messages; can refuse recipients or fail DATA N times."""

    def __init__(self, refuse=(), fail_data_times=0, fail_code="451 4.3.0 Try again later"):
        self.messages, self.refuse, self.fail_left, self.fail_code = [], set(refuse), fail_data_times, fail_code

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if address in self.refuse:
            return "550 5.1.1 Mailbox unavailable"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        if self.fail_left:
            self.fail_left -= 1
            return self.fail_code
        self.messages.append(envelope)
        return "250 Message accepted"


def _auth(server, session, envelope, mechanism, auth_data):
    ok = isinstance(auth_data, LoginPassword) and auth_data.login == b"app@example.com" and auth_data.password == b"s3cret"
    return AuthResult(success=ok, handled=False)  # let aiosmtpd reply 535 like a real server


@pytest.fixture
def smtp():
    started = []

    def start(handler=None, auth=False):
        handler = handler or Inbox()
        kwargs = {"authenticator": _auth, "auth_require_tls": False} if auth else {}
        with socket.socket() as probe:  # pick a free port (Windows rejects port 0 for the ready check)
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        ctl = Controller(handler, hostname="127.0.0.1", port=port, **kwargs)
        ctl.start()
        started.append(ctl)
        return handler, port

    yield start
    for ctl in started:
        ctl.stop()


def cfg(port, **over):
    base = dict(smtp_host="127.0.0.1", smtp_port=port, smtp_security="none", smtp_username="", smtp_password="",
                smtp_from="scheduling@docshealth.example", smtp_from_name="Docs Health Scheduling",
                smtp_timeout_seconds=3, smtp_max_attempts=3, smtp_retry_base_seconds=0.01)
    base.update(over)
    return get_settings().model_copy(update=base)


def test_sends_text_html_threading_and_attachment(smtp, tmp_path):
    inbox, port = smtp()
    att = tmp_path / "card.pdf"
    att.write_bytes(b"%PDF-1.4 test")
    r = mailer.send_mail("patient@example.com", "Your appointment", "Plain body", body_html="<p>HTML body</p>",
                         cc="staff@example.com", bcc=["audit@example.com"], reply_to="desk@example.com",
                         attachments=[att], in_reply_to="<orig@example.com>", settings=cfg(port))
    assert r.ok and r.attempts == 1 and not r.refused and r.message_id
    env = inbox.messages[0]
    assert set(env.rcpt_tos) == {"patient@example.com", "staff@example.com", "audit@example.com"}
    raw = env.content.decode()
    assert "Subject: Your appointment" in raw and "In-Reply-To: <orig@example.com>" in raw
    assert "audit@example.com" not in raw.split("\r\n\r\n")[0]  # Bcc never appears in headers
    assert 'filename="card.pdf"' in raw and "text/html" in raw and "Docs Health Scheduling" in raw


def test_auth_success_and_wrong_password_not_retried(smtp):
    inbox, port = smtp(auth=True)
    ok = mailer.send_mail("p@example.com", "Hi", "x", settings=cfg(port, smtp_username="app@example.com", smtp_password="s3cret"))
    assert ok.ok
    bad = mailer.send_mail("p@example.com", "Hi", "x", settings=cfg(port, smtp_username="app@example.com", smtp_password="nope"))
    assert not bad.ok and bad.error_kind == "auth" and bad.attempts == 1
    assert "nope" not in (bad.error or "") + (bad.server_reply or "")


def test_partial_and_total_recipient_refusal(smtp):
    inbox, port = smtp(Inbox(refuse={"gone@example.com"}))
    r = mailer.send_mail(["ok@example.com", "gone@example.com"], "Hi", "x", settings=cfg(port))
    assert r.ok and r.partial and "gone@example.com" in r.refused and r.accepted == ["ok@example.com"]
    all_bad = mailer.send_mail("gone@example.com", "Hi", "x", settings=cfg(port))
    assert not all_bad.ok and all_bad.error_kind == "rejected" and all_bad.attempts == 1


def test_transient_failure_is_retried_then_succeeds(smtp):
    inbox, port = smtp(Inbox(fail_data_times=2))
    sleeps = []
    r = mailer.send_mail("p@example.com", "Hi", "x", settings=cfg(port), sleep=sleeps.append)
    assert r.ok and r.attempts == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0] * 1.2  # backoff grows
    assert len(inbox.messages) == 1


def test_permanent_data_rejection_not_retried(smtp):
    inbox, port = smtp(Inbox(fail_data_times=5, fail_code="554 5.6.0 Message rejected"))
    r = mailer.send_mail("p@example.com", "Hi", "x", settings=cfg(port))
    assert not r.ok and r.error_kind == "rejected" and r.attempts == 1 and "554" in r.server_reply


def test_connection_refused_retries_then_reports():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again: nothing listening
    sleeps = []
    r = mailer.send_mail("p@example.com", "Hi", "x", settings=cfg(port), sleep=sleeps.append)
    assert not r.ok and r.error_kind == "connection" and r.attempts == 3 and len(sleeps) == 2


def test_silent_server_times_out():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    held = []
    threading.Thread(target=lambda: [held.append(srv.accept()) for _ in range(2)], daemon=True).start()
    t0 = time.perf_counter()
    r = mailer.send_mail("p@example.com", "Hi", "x", settings=cfg(port, smtp_timeout_seconds=0.5, smtp_max_attempts=2),
                         sleep=lambda _: None)
    assert not r.ok and r.error_kind == "timeout" and r.attempts == 2 and time.perf_counter() - t0 < 5
    srv.close()


@pytest.mark.parametrize("kwargs,needle", [
    ({"to": "p@example.com", "subject": "Hi\r\nBcc: evil@example.com"}, "header injection"),
    ({"to": "not-an-email", "subject": "Hi"}, "invalid"),
    ({"to": "p@example.com\r\nBcc: x@example.com", "subject": "Hi"}, "header injection"),
    ({"to": "", "subject": "Hi"}, "To recipient"),
    ({"to": "p@example.com", "subject": "Hi", "body_text": ""}, "body is empty"),
    ({"to": "p@example.com", "subject": "Hi", "attachments": ["C:/no/such/file.pdf"]}, "not found"),
    ({"to": "p@example.com", "subject": "Hi", "headers": {"X-Evil": "a\r\nBcc: b@example.com"}}, "injection"),
])
def test_validation_never_touches_the_network(kwargs, needle):
    kwargs.setdefault("body_text", "x")
    r = mailer.send_mail(settings=cfg(1), **kwargs)  # port 1: would fail if it tried to connect
    assert not r.ok and r.error_kind == "validation" and r.attempts == 0 and needle in r.error


@pytest.mark.parametrize("over,needle", [
    ({"smtp_host": ""}, "SMTP_HOST"),
    ({"smtp_host": "smtp.example.com", "smtp_security": "none"}, "only allowed to localhost"),
    ({"smtp_username": "u@example.com"}, "both SMTP_USERNAME"),
    ({"smtp_security": "tls1.0"}, "SMTP_SECURITY"),
])
def test_bad_configuration_fails_fast(over, needle):
    r = mailer.send_mail("p@example.com", "Hi", "x", settings=cfg(1, **over))
    assert not r.ok and r.error_kind == "config" and needle in r.error


def test_gateway_smtp_mode_delivers_and_records(client, smtp, monkeypatch):
    inbox, port = smtp()
    s = get_settings()
    for k, v in cfg(port).model_dump().items():
        if k.startswith("smtp_"):
            monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(s, "email_outbound_mode", "smtp")
    r = client.post("/api/demo/samples/auto_book").json()["request"]
    out = [e for e in r["emails"] if e["direction"] == "outbound"]
    assert out[0]["send_status"] == "sent" and inbox.messages
    assert inbox.messages[0].rcpt_tos == ["maria.gonzalez@example.com"]
