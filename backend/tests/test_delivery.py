"""Delivery methods (SMTP / Graph / SendGrid / Mailgun), provider detection, inbound safety,
and the 'Send only' / 'Send & resolve' path actually reaching the patient's address."""
import email
import json
import socket

import httpx
import pytest
from aiosmtpd.controller import Controller

from app.config import get_settings
from app.services import delivery, email_gateway, mail_providers


def settings(**over):
    base = dict(smtp_from="pranali@ashunya.com", smtp_from_name="Docs Health Scheduling", smtp_max_attempts=3,
                smtp_retry_base_seconds=0.01, smtp_timeout_seconds=5, graph_tenant_id="t", graph_client_id="c",
                graph_client_secret="s", scheduling_mailbox="pranali@ashunya.com", sendgrid_api_key="SG.key",
                mailgun_api_key="mg-key", mailgun_domain="mg.example.com", mailgun_region="us")
    base.update(over)
    return get_settings().model_copy(update=base)


def mock(handler):
    calls = []

    def wrapped(request: httpx.Request):
        calls.append(request)
        return handler(request, len(calls))

    return httpx.MockTransport(wrapped), calls


# --------------------------------------------------------------------------- HTTPS API transports

def test_sendgrid_success_payload_has_no_tracking_and_threading():
    transport, calls = mock(lambda req, n: httpx.Response(202))
    r = delivery.deliver("patient@example.com", "Your appointment", "Hello", method="sendgrid",
                         in_reply_to="<orig@x>", settings=settings(), transport=transport)
    assert r.ok and r.accepted == ["patient@example.com"] and r.attempts == 1
    req = calls[0]
    body = json.loads(req.content)
    assert req.headers["Authorization"] == "Bearer SG.key"
    assert body["from"]["email"] == "pranali@ashunya.com"
    assert body["tracking_settings"]["click_tracking"]["enable"] is False  # PHI: no link rewriting
    assert body["headers"]["In-Reply-To"] == "<orig@x>"


def test_mailgun_success_and_eu_region():
    transport, calls = mock(lambda req, n: httpx.Response(200, json={"id": "<x@mg>"}))
    r = delivery.deliver("patient@example.com", "Hi", "x", method="mailgun",
                         settings=settings(mailgun_region="eu"), transport=transport)
    assert r.ok and calls[0].url.host == "api.eu.mailgun.net" and b"o%3Atracking=no" in calls[0].content


def test_graph_gets_token_then_sends_from_mailbox():
    def handler(req, n):
        if "oauth2" in req.url.path:
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(202)

    transport, calls = mock(handler)
    r = delivery.deliver("patient@example.com", "Hi", "x", method="graph", settings=settings(), transport=transport)
    assert r.ok and "/users/pranali@ashunya.com/sendMail" in calls[1].url.path
    assert calls[1].headers["Authorization"] == "Bearer tok"


def test_auth_errors_are_not_retried_and_explained():
    transport, calls = mock(lambda req, n: httpx.Response(401, text='{"errors":[{"message":"bad key"}]}'))
    r = delivery.deliver("p@example.com", "Hi", "x", method="sendgrid", settings=settings(), transport=transport)
    assert not r.ok and r.error_kind == "auth" and r.attempts == 1 and "Mail Send" in r.error


def test_rate_limit_honours_retry_after_then_succeeds():
    transport, calls = mock(lambda req, n: httpx.Response(429, headers={"Retry-After": "2"}) if n == 1 else httpx.Response(202))
    waits = []
    r = delivery.deliver("p@example.com", "Hi", "x", method="sendgrid", settings=settings(), transport=transport,
                         sleep=waits.append)
    assert r.ok and r.attempts == 2 and waits == [2.0]


def test_server_errors_retry_then_give_up():
    transport, calls = mock(lambda req, n: httpx.Response(503))
    r = delivery.deliver("p@example.com", "Hi", "x", method="mailgun", settings=settings(), transport=transport,
                         sleep=lambda _: None)
    assert not r.ok and r.error_kind == "connection" and r.attempts == 3 and len(calls) == 3


def test_missing_credentials_and_bad_input_fail_fast():
    r = delivery.deliver("p@example.com", "Hi", "x", method="sendgrid", settings=settings(sendgrid_api_key=""))
    assert r.error_kind == "config"
    r = delivery.deliver("p@example.com", "Hi\r\nBcc: x@y.z", "x", method="mailgun", settings=settings())
    assert r.error_kind == "validation" and r.attempts == 0
    r = delivery.deliver("p@example.com", "Hi", "x", method="outbox", settings=settings())
    assert r.error_kind == "not_configured"


# --------------------------------------------------------------------------- provider detection

@pytest.mark.parametrize("address,key", [("someone@gmail.com", "google"), ("a@outlook.com", "outlook_consumer"),
                                         ("a@yahoo.com", "yahoo"), ("a@icloud.com", "icloud")])
def test_detect_consumer_domains(address, key):
    p = mail_providers.detect(address)
    assert p.key == key and p.smtp_host and p.smtp_port


@pytest.mark.parametrize("mx,key,outbound", [("company-com.mail.protection.outlook.com", "microsoft365", "graph"),
                                              ("aspmx.l.google.com", "google", "smtp"),
                                              ("mx.zoho.com", "zoho", "smtp")])
def test_detect_company_domains_by_mx(monkeypatch, mx, key, outbound):
    mail_providers.detect.cache_clear()
    monkeypatch.setattr(mail_providers, "_mx", lambda d: mx)
    p = mail_providers.detect("staff@company-name.com")
    assert p.key == key and p.recommended_outbound == outbound and p.source == "mx"


def test_detect_unknown_falls_back_safely(monkeypatch):
    mail_providers.detect.cache_clear()
    monkeypatch.setattr(mail_providers, "_mx", lambda d: None)
    monkeypatch.setattr(mail_providers, "_ispdb", lambda d: None)
    p = mail_providers.detect("x@unknown-clinic.org")
    assert p.key == "generic" and p.smtp_host == "smtp.unknown-clinic.org"
    assert mail_providers.detect("not-an-address").key == "invalid"


# --------------------------------------------------------------------------- inbound safety

def test_inbound_loop_protection(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "scheduling_mailbox", "pranali@ashunya.com")
    assert email_gateway._skip_reason("pranali@ashunya.com", {}) is not None
    assert email_gateway._skip_reason("p@example.com", {"Auto-Submitted": "auto-replied"}) is not None
    assert email_gateway._skip_reason("MAILER-DAEMON@example.com", {}) is not None
    assert email_gateway._skip_reason("p@example.com", {"Auto-Submitted": "no"}) is None


def test_html_only_email_body_is_extracted():
    msg = email.message_from_string(
        "Content-Type: text/html; charset=utf-8\n\n<html><style>p{}</style><p>I need an <b>appointment</b></p></html>")
    assert email_gateway._plain_body(msg) == "I need an appointment"


# --------------------------------------------------------------------------- Send only -> patient inbox

class Inbox:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


@pytest.fixture
def local_smtp(monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    inbox = Inbox()
    ctl = Controller(inbox, hostname="127.0.0.1", port=port)
    ctl.start()
    s = get_settings()
    for k, v in dict(smtp_host="127.0.0.1", smtp_port=port, smtp_security="none", smtp_username="", smtp_password="",
                     smtp_from="pranali@ashunya.com", smtp_max_attempts=1).items():
        monkeypatch.setattr(s, k, v)
    yield inbox
    ctl.stop()


def test_send_only_in_outbox_mode_is_reported_honestly_then_resend_delivers(client, local_smtp, monkeypatch):
    req = client.post("/api/demo/samples/general_question").json()["request"]
    patient = req["from_address"]

    monkeypatch.setattr(get_settings(), "email_outbound_mode", "outbox")
    r = client.post(f"/api/requests/{req['id']}/send-response", json={"body": "Our hours are 8-5.", "resolve": False}).json()
    assert r["email"]["send_status"] == "stored" and "not configured" in r["email"]["send_error"]
    assert not local_smtp.messages

    monkeypatch.setattr(get_settings(), "email_outbound_mode", "smtp")
    again = client.post(f"/api/emails/{r['email']['id']}/resend").json()
    assert again["send_status"] == "sent" and again["delivery_method"] == "smtp" and again["delivered_at"]
    assert local_smtp.messages[-1].rcpt_tos == [patient]
    assert client.post(f"/api/emails/{r['email']['id']}/resend").status_code == 409  # already delivered


def test_send_and_resolve_delivers_to_patient(client, local_smtp, monkeypatch):
    monkeypatch.setattr(get_settings(), "email_outbound_mode", "smtp")
    req = client.post("/api/demo/samples/eligibility_failed").json()["request"]
    r = client.post(f"/api/requests/{req['id']}/send-response",
                    json={"body": "Please call us about your coverage.", "resolve": True}).json()
    assert r["email"]["send_status"] == "sent" and r["request"]["status"] == "resolved"
    env = local_smtp.messages[-1]
    assert env.rcpt_tos == [req["from_address"]] and env.mail_from == "pranali@ashunya.com"
    assert b"Please call us about your coverage." in env.content
