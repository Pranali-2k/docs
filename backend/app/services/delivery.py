"""Outbound email delivery over the configured transport. Every transport is encrypted in transit.

    deliver(to, subject, body_text, ..., method=None) -> MailResult   (never raises)

Methods (EMAIL_OUTBOUND_MODE):
  smtp      any provider (Gmail, Microsoft 365, Yahoo, Zoho, SES, ...) via app.services.mailer:
            STARTTLS/TLS with certificate verification
  graph     Microsoft 365 via Microsoft Graph sendMail over HTTPS, OAuth2 client credentials
  sendgrid  Twilio SendGrid v3 HTTPS API (API key + verified sender)
  mailgun   Mailgun HTTPS API (API key + verified sending domain)
  outbox    not sent; stored for staff (safe default)

All methods share the same validation (addresses, header injection, sizes) and the same
retry policy: only transient failures (timeouts, connection errors, HTTP 429/5xx, SMTP 4xx)
are retried, with exponential backoff and jitter.
"""
from __future__ import annotations

import logging
import random
import time

import httpx

from app.config import Settings, get_settings
from app.services import mailer
from app.services.mailer import MailResult

log = logging.getLogger("docshealth.delivery")
METHODS = ("smtp", "graph", "sendgrid", "mailgun", "outbox")


class _HttpFail(Exception):
    def __init__(self, kind: str, message: str, transient: bool, reply: str | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.kind, self.transient, self.reply, self.retry_after = kind, transient, reply, retry_after


def _http_fail(provider: str, resp: httpx.Response) -> _HttpFail:
    code = resp.status_code
    body = resp.text[:400]
    retry_after = None
    try:
        retry_after = float(resp.headers.get("Retry-After")) if resp.headers.get("Retry-After") else None
    except ValueError:
        pass
    if code in (401, 403):
        hint = {"graph": " (check GRAPH_* credentials, Mail.Send application permission, admin consent and the "
                         "application access policy for this mailbox)",
                "sendgrid": " (check SENDGRID_API_KEY has 'Mail Send' permission and the sender is verified)",
                "mailgun": " (check MAILGUN_API_KEY and MAILGUN_DOMAIN/region)"}.get(provider, "")
        return _HttpFail("auth", f"{provider} rejected the credentials ({code}){hint}", False, body)
    if code == 404:
        return _HttpFail("config", f"{provider} endpoint or mailbox not found (404)", False, body)
    if code == 429:
        return _HttpFail("rate_limited", f"{provider} rate limit (429)", True, body, retry_after)
    if code >= 500:
        return _HttpFail("connection", f"{provider} server error ({code})", True, body, retry_after)
    return _HttpFail("rejected", f"{provider} rejected the message ({code})", False, body)


def _fields(msg) -> dict:
    """Pull normalized values back out of the validated MIME message."""
    body = msg.get_body(("plain",))
    html = msg.get_body(("html",))
    return {
        "from_addr": msg["From"].addresses[0].addr_spec,
        "from_name": msg["From"].addresses[0].display_name or None,
        "to": [a.addr_spec for a in msg["To"].addresses],
        "cc": [a.addr_spec for a in msg["Cc"].addresses] if msg["Cc"] else [],
        "reply_to": msg["Reply-To"].addresses[0].addr_spec if msg["Reply-To"] else None,
        "subject": str(msg["Subject"]),
        "text": body.get_content() if body is not None else "",
        "html": html.get_content() if html is not None and html is not body else None,
        "message_id": str(msg["Message-ID"]),
        "in_reply_to": str(msg["In-Reply-To"]) if msg["In-Reply-To"] else None,
    }


# --------------------------------------------------------------------------- HTTP transports

def _graph_send(s: Settings, f: dict, bcc: list[str], client: httpx.Client) -> None:
    if not (s.graph_tenant_id and s.graph_client_id and s.graph_client_secret):
        raise _HttpFail("config", "Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET", False)
    tok = client.post(f"https://login.microsoftonline.com/{s.graph_tenant_id}/oauth2/v2.0/token", data={
        "client_id": s.graph_client_id, "client_secret": s.graph_client_secret,
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"})
    if tok.status_code != 200:
        raise _http_fail("graph", tok)
    rcpt = lambda addrs: [{"emailAddress": {"address": a}} for a in addrs]  # noqa: E731
    message = {
        "subject": f["subject"],
        "body": {"contentType": "HTML" if f["html"] else "Text", "content": f["html"] or f["text"]},
        "toRecipients": rcpt(f["to"]), "ccRecipients": rcpt(f["cc"]), "bccRecipients": rcpt(bcc),
        "internetMessageHeaders": [{"name": "X-DocsHealth-Message-Id", "value": f["message_id"]}],
    }
    if f["reply_to"]:
        message["replyTo"] = rcpt([f["reply_to"]])
    r = client.post(f"https://graph.microsoft.com/v1.0/users/{f['from_addr']}/sendMail",
                    headers={"Authorization": f"Bearer {tok.json()['access_token']}"},
                    json={"message": message, "saveToSentItems": True})
    if r.status_code != 202:
        raise _http_fail("graph", r)


def _sendgrid_send(s: Settings, f: dict, bcc: list[str], client: httpx.Client) -> None:
    if not s.sendgrid_api_key:
        raise _HttpFail("config", "Set SENDGRID_API_KEY", False)
    headers = {"In-Reply-To": f["in_reply_to"], "References": f["in_reply_to"]} if f["in_reply_to"] else {}
    personalization = {"to": [{"email": a} for a in f["to"]]}
    if f["cc"]:
        personalization["cc"] = [{"email": a} for a in f["cc"]]
    if bcc:
        personalization["bcc"] = [{"email": a} for a in bcc]
    payload = {
        "personalizations": [personalization],
        "from": {"email": f["from_addr"], **({"name": f["from_name"]} if f["from_name"] else {})},
        "subject": f["subject"],
        "content": [{"type": "text/plain", "value": f["text"]}] + (
            [{"type": "text/html", "value": f["html"]}] if f["html"] else []),
        "custom_args": {"docshealth_message_id": f["message_id"]},
        # PHI: no click/open tracking (would rewrite links and add pixels)
        "tracking_settings": {"click_tracking": {"enable": False}, "open_tracking": {"enable": False}},
    }
    if headers:
        payload["headers"] = headers
    if f["reply_to"]:
        payload["reply_to"] = {"email": f["reply_to"]}
    r = client.post("https://api.sendgrid.com/v3/mail/send", json=payload,
                    headers={"Authorization": f"Bearer {s.sendgrid_api_key}"})
    if r.status_code != 202:
        raise _http_fail("sendgrid", r)


def _mailgun_send(s: Settings, f: dict, bcc: list[str], client: httpx.Client) -> None:
    if not (s.mailgun_api_key and s.mailgun_domain):
        raise _HttpFail("config", "Set MAILGUN_API_KEY and MAILGUN_DOMAIN", False)
    base = "https://api.eu.mailgun.net" if s.mailgun_region == "eu" else "https://api.mailgun.net"
    sender = f'{f["from_name"]} <{f["from_addr"]}>' if f["from_name"] else f["from_addr"]
    data = {"from": sender, "to": f["to"], "subject": f["subject"], "text": f["text"],
            "h:Message-Id": f["message_id"], "o:tracking": "no"}
    for key, val in (("cc", f["cc"]), ("bcc", bcc), ("html", f["html"]), ("h:Reply-To", f["reply_to"]),
                     ("h:In-Reply-To", f["in_reply_to"]), ("h:References", f["in_reply_to"])):
        if val:
            data[key] = val
    r = client.post(f"{base}/v3/{s.mailgun_domain}/messages", data=data, auth=("api", s.mailgun_api_key))
    if r.status_code != 200:
        raise _http_fail("mailgun", r)


HTTP_SENDERS = {"graph": _graph_send, "sendgrid": _sendgrid_send, "mailgun": _mailgun_send}


# --------------------------------------------------------------------------- entry point

def deliver(
    to, subject: str, body_text: str | None = None, *, body_html: str | None = None, cc=None, bcc=None,
    reply_to: str | None = None, message_id: str | None = None, in_reply_to: str | None = None,
    method: str | None = None, settings: Settings | None = None, transport: httpx.BaseTransport | None = None,
    sleep=time.sleep,
) -> MailResult:
    """Send one email with the configured (or given) method. Never raises."""
    s = settings or get_settings()
    method = (method or s.email_outbound_mode or "outbox").lower()
    if method not in METHODS:
        return MailResult(ok=False, error_kind="config", error=f"Unknown EMAIL_OUTBOUND_MODE '{method}'")
    if method == "outbox":
        return MailResult(ok=False, error_kind="not_configured",
                          error="Email delivery is not configured (outbox mode): the message was stored, not sent")
    if method == "smtp":
        return mailer.send_mail(to, subject, body_text, body_html=body_html, cc=cc, bcc=bcc, reply_to=reply_to,
                                message_id=message_id, in_reply_to=in_reply_to, settings=s, sleep=sleep)

    started = time.perf_counter()
    result = MailResult(ok=False)
    try:
        sender = s.smtp_from if method != "graph" else (s.scheduling_mailbox or s.smtp_from)
        msg, rcpts = mailer._build(s.model_copy(update={"smtp_from": sender}), to, cc, bcc, subject, body_text,
                                   body_html, reply_to, None, None, message_id, in_reply_to)
        f = _fields(msg)
        bcc_list = [r for r in rcpts if r not in f["to"] + f["cc"]]
        result.message_id = f["message_id"]
    except Exception as exc:  # validation problems: not retried
        fail = mailer._classify(exc)
        result.error_kind, result.error = fail.kind, str(fail)
        return result

    attempts = max(1, int(s.smtp_max_attempts))
    with httpx.Client(timeout=float(s.smtp_timeout_seconds), transport=transport) as client:
        for attempt in range(1, attempts + 1):
            result.attempts = attempt
            try:
                HTTP_SENDERS[method](s, f, bcc_list, client)
                result.ok, result.accepted = True, rcpts
                result.error_kind = result.error = result.server_reply = None
                log.info("mail %s sent via %s to %d recipient(s)", result.message_id, method, len(rcpts))
                break
            except _HttpFail as exc:
                result.error_kind, result.error, result.server_reply = exc.kind, str(exc), exc.reply
                transient, wait = exc.transient, exc.retry_after
            except httpx.TimeoutException:
                result.error_kind, result.error, transient, wait = "timeout", f"{method} request timed out", True, None
            except httpx.HTTPError as exc:
                result.error_kind, result.error, transient, wait = "connection", f"{method} network error: {exc}", True, None
            except Exception as exc:  # unexpected payload/JSON issues: report, don't crash the caller
                result.error_kind, result.error, transient, wait = "unknown", f"{type(exc).__name__}: {exc}", False, None
            log.warning("mail attempt %d/%d via %s failed (%s): %s", attempt, attempts, method, result.error_kind,
                        result.error)
            if not transient or attempt == attempts:
                break
            backoff = min(30.0, float(s.smtp_retry_base_seconds) * 2 ** (attempt - 1)) * (0.75 + random.random() / 2)
            sleep(min(60.0, wait) if wait else backoff)
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result
