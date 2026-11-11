"""Robust SMTP sender.

`send_mail()` never raises. Every outcome, success or failure, comes back as a
`MailResult` with a machine-readable `error_kind`, so callers decide what to do
(retry later, show staff, fall back to the outbox) without try/except of their own.

What it handles:
  * validation: addresses (RFC 5322 + DNS-free syntax), CR/LF header injection,
    empty subject/body, size limits, attachment existence/size/type;
  * configuration: host/port/security/credentials sanity, fail fast with `config`;
  * transport: STARTTLS (587) or implicit TLS (465) with certificate verification,
    or plain (local test servers only); connect/read timeouts;
  * retries with exponential backoff + jitter, only for transient failures
    (timeouts, dropped connections, 4xx "try again" replies, 421/450/451/452);
    never for auth failures or permanent 5xx rejections;
  * partial delivery: recipients the server refuses are reported individually;
  * cleanup: the SMTP connection is always closed; credentials are never logged.
"""
from __future__ import annotations

import logging
import mimetypes
import random
import re
import smtplib
import socket
import ssl
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from email_validator import EmailNotValidError, validate_email

from app.config import Settings, get_settings

log = logging.getLogger("docshealth.mailer")

MAX_SUBJECT = 998
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS_BYTES = 20 * 1024 * 1024
MAX_RECIPIENTS = 100
TRANSIENT_CODES = {421, 450, 451, 452, 454}


@dataclass
class MailResult:
    ok: bool
    message_id: str | None = None
    accepted: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)  # address -> server reply
    attempts: int = 0
    error_kind: str | None = None  # config | validation | auth | tls | connection | timeout | rejected | rate_limited | unknown
    error: str | None = None
    server_reply: str | None = None
    elapsed_ms: int = 0

    @property
    def partial(self) -> bool:
        return self.ok and bool(self.refused)


class _Fail(Exception):
    def __init__(self, kind: str, message: str, transient: bool = False, reply: str | None = None):
        super().__init__(message)
        self.kind, self.transient, self.reply = kind, transient, reply


# --------------------------------------------------------------------------- validation

def _clean_address(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Fail("validation", f"{label} address is empty")
    if re.search(r"[\r\n]", value):
        raise _Fail("validation", f"{label} address contains a line break (header injection blocked)")
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise _Fail("validation", f"{label} address '{value.strip()}' is invalid: {exc}") from None


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v for v in re.split(r"[,;]", value) if v.strip()]
    return list(value)


def _check_config(s: Settings) -> None:
    if not s.smtp_host:
        raise _Fail("config", "SMTP_HOST is not set")
    if not (0 < int(s.smtp_port) < 65536):
        raise _Fail("config", f"SMTP_PORT {s.smtp_port} is invalid")
    if s.smtp_security not in {"starttls", "ssl", "none"}:
        raise _Fail("config", "SMTP_SECURITY must be starttls, ssl or none")
    if s.smtp_security == "none" and s.smtp_host not in {"localhost", "127.0.0.1", "::1"}:
        raise _Fail("config", "Unencrypted SMTP is only allowed to localhost; use starttls or ssl")
    if bool(s.smtp_username) != bool(s.smtp_password):
        raise _Fail("config", "Set both SMTP_USERNAME and SMTP_PASSWORD, or neither")
    if not s.smtp_from:
        raise _Fail("config", "SMTP_FROM is not set")


def _build(s: Settings, to, cc, bcc, subject, body_text, body_html, reply_to, attachments, headers, message_id,
           in_reply_to) -> tuple[EmailMessage, list[str]]:
    if not isinstance(subject, str) or not subject.strip():
        raise _Fail("validation", "Subject is empty")
    if re.search(r"[\r\n]", subject):
        raise _Fail("validation", "Subject contains a line break (header injection blocked)")
    if len(subject) > MAX_SUBJECT:
        raise _Fail("validation", f"Subject is longer than {MAX_SUBJECT} characters")
    if not (body_text or body_html):
        raise _Fail("validation", "Message body is empty")
    if len((body_text or "").encode()) + len((body_html or "").encode()) > MAX_BODY_BYTES:
        raise _Fail("validation", "Message body exceeds 5 MB")

    sender = _clean_address(s.smtp_from, "From")
    to_list = [_clean_address(a, "To") for a in _as_list(to)]
    cc_list = [_clean_address(a, "Cc") for a in _as_list(cc)]
    bcc_list = [_clean_address(a, "Bcc") for a in _as_list(bcc)]
    if not to_list:
        raise _Fail("validation", "At least one To recipient is required")
    rcpts = list(dict.fromkeys(to_list + cc_list + bcc_list))
    if len(rcpts) > MAX_RECIPIENTS:
        raise _Fail("validation", f"More than {MAX_RECIPIENTS} recipients")

    msg = EmailMessage()
    msg["From"] = formataddr((s.smtp_from_name, sender)) if s.smtp_from_name else sender
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    if reply_to:
        msg["Reply-To"] = _clean_address(reply_to, "Reply-To")
    msg["Subject"] = subject.strip()
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = message_id or make_msgid(domain=sender.split("@", 1)[1])
    if in_reply_to:
        if re.search(r"[\r\n]", in_reply_to):
            raise _Fail("validation", "In-Reply-To contains a line break")
        msg["In-Reply-To"] = msg["References"] = in_reply_to
    for name, value in (headers or {}).items():
        if re.search(r"[\r\n:]", name) or re.search(r"[\r\n]", str(value)):
            raise _Fail("validation", f"Header {name!r} is invalid (header injection blocked)")
        msg[name] = str(value)

    msg.set_content(body_text or re.sub(r"<[^>]+>", "", body_html or ""))
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    total = 0
    for item in attachments or []:
        path = Path(item)
        if not path.is_file():
            raise _Fail("validation", f"Attachment not found: {path.name}")
        data = path.read_bytes()
        total += len(data)
        if total > MAX_ATTACHMENTS_BYTES:
            raise _Fail("validation", "Attachments exceed 20 MB in total")
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return msg, rcpts


# --------------------------------------------------------------------------- transport

def _classify(exc: Exception) -> _Fail:
    if isinstance(exc, _Fail):
        return exc
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        reply = _reply(exc)
        hint = ""
        if "SmtpClientAuthentication is disabled" in reply or "5.7.139" in reply:
            hint = (" Microsoft 365 has SMTP AUTH disabled for this mailbox/tenant: an admin must enable "
                    "'Authenticated SMTP' for it (or use EMAIL_OUTBOUND_MODE=graph).")
        elif "5.7.3" in reply or "535" in reply:
            hint = " Check SMTP_USERNAME/SMTP_PASSWORD; with MFA on, use an app password."
        return _Fail("auth", "SMTP login failed." + hint, reply=reply)
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return _Fail("config", f"Server does not support a required feature: {exc}")
    if isinstance(exc, smtplib.SMTPResponseException) and exc.smtp_code == 530:
        return _Fail("auth", "The server requires login: set SMTP_USERNAME and SMTP_PASSWORD "
                             "(run python -m scripts.configure_smtp)", reply=_reply(exc))
    if isinstance(exc, smtplib.SMTPSenderRefused):
        code = exc.smtp_code
        return _Fail("rate_limited" if code in TRANSIENT_CODES else "rejected",
                     f"Sender {exc.sender} refused" + (" (SMTP_USERNAME may not be allowed to send as SMTP_FROM)"
                                                        if code in (550, 554) else ""),
                     transient=code in TRANSIENT_CODES, reply=_reply(exc))
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return _Fail("rejected", "All recipients were refused",
                     reply="; ".join(f"{a}: {c} {m.decode(errors='replace') if isinstance(m, bytes) else m}"
                                     for a, (c, m) in exc.recipients.items()))
    if isinstance(exc, smtplib.SMTPDataError):
        code = exc.smtp_code
        return _Fail("rate_limited" if code in TRANSIENT_CODES else "rejected", "Server rejected the message",
                     transient=code in TRANSIENT_CODES or 400 <= code < 500, reply=_reply(exc))
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        if "timed out" in str(exc).lower():  # smtplib wraps read timeouts as "disconnected"
            return _Fail("timeout", "Timed out waiting for the SMTP server", transient=True)
        return _Fail("connection", "Server closed the connection", transient=True)
    if isinstance(exc, smtplib.SMTPConnectError):
        return _Fail("connection", f"Could not connect: {_reply(exc)}", transient=True, reply=_reply(exc))
    if isinstance(exc, smtplib.SMTPResponseException):
        code = exc.smtp_code
        return _Fail("rate_limited" if code in TRANSIENT_CODES else "rejected", f"Server replied {code}",
                     transient=code in TRANSIENT_CODES or 400 <= code < 500, reply=_reply(exc))
    if isinstance(exc, ssl.SSLCertVerificationError):
        return _Fail("tls", f"TLS certificate verification failed: {exc.verify_message}")
    if isinstance(exc, ssl.SSLError):
        return _Fail("tls", f"TLS error: {exc.reason or exc}", transient=True)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return _Fail("timeout", "Timed out talking to the SMTP server", transient=True)
    if isinstance(exc, socket.gaierror):
        return _Fail("connection", f"SMTP host could not be resolved: {exc}")
    if isinstance(exc, (ConnectionError, OSError)):
        return _Fail("connection", f"Network error: {exc}", transient=True)
    return _Fail("unknown", f"{type(exc).__name__}: {exc}")


def _reply(exc) -> str:
    msg = getattr(exc, "smtp_error", b"")
    msg = msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg)
    code = getattr(exc, "smtp_code", "")
    return f"{code} {msg}".strip()


def _deliver(s: Settings, msg: EmailMessage, rcpts: list[str]) -> dict:
    timeout = float(s.smtp_timeout_seconds)
    context = ssl.create_default_context()  # verifies certificates and host names
    if s.smtp_security == "ssl":
        server = smtplib.SMTP_SSL(s.smtp_host, int(s.smtp_port), timeout=timeout, context=context)
    else:
        server = smtplib.SMTP(s.smtp_host, int(s.smtp_port), timeout=timeout)
    try:
        server.ehlo()
        if s.smtp_security == "starttls":
            if not server.has_extn("starttls"):
                raise _Fail("tls", "Server does not offer STARTTLS; refusing to send credentials in clear text")
            server.starttls(context=context)
            server.ehlo()
        if s.smtp_username:
            server.login(s.smtp_username, s.smtp_password)
        return server.send_message(msg, from_addr=msg["From"].addresses[0].addr_spec, to_addrs=rcpts)
    finally:
        try:
            server.quit()
        except Exception:  # already closed / broken pipe: nothing else to clean up
            try:
                server.close()
            except Exception:
                pass


def send_mail(
    to,
    subject: str,
    body_text: str | None = None,
    *,
    body_html: str | None = None,
    cc=None,
    bcc=None,
    reply_to: str | None = None,
    attachments: list | None = None,
    headers: dict | None = None,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    settings: Settings | None = None,
    sleep=time.sleep,
) -> MailResult:
    """Send one email over SMTP. Never raises; inspect `MailResult.ok` / `error_kind`."""
    s = settings or get_settings()
    started = time.perf_counter()
    result = MailResult(ok=False)
    try:
        _check_config(s)
        msg, rcpts = _build(s, to, cc, bcc, subject, body_text, body_html, reply_to, attachments, headers,
                            message_id, in_reply_to)
        result.message_id = msg["Message-ID"]
    except Exception as exc:  # config/validation problems: no point retrying
        f = _classify(exc)
        result.error_kind, result.error = f.kind, str(f)
        log.warning("mail not sent (%s): %s", f.kind, f)
        return result

    attempts = max(1, int(s.smtp_max_attempts))
    for attempt in range(1, attempts + 1):
        result.attempts = attempt
        try:
            refused = _deliver(s, msg, rcpts) or {}
            result.ok = True
            result.refused = {a: f"{c} {m.decode(errors='replace') if isinstance(m, bytes) else m}"
                              for a, (c, m) in refused.items()}
            result.accepted = [r for r in rcpts if r not in refused]
            result.error_kind = result.error = None
            log.info("mail sent %s to %d recipient(s) in %d attempt(s)%s", result.message_id, len(result.accepted),
                     attempt, f"; refused: {list(refused)}" if refused else "")
            break
        except Exception as exc:  # classified below; never propagates
            f = _classify(exc)
            result.error_kind, result.error, result.server_reply = f.kind, str(f), f.reply
            log.warning("mail attempt %d/%d failed (%s): %s", attempt, attempts, f.kind, f)
            if not f.transient or attempt == attempts:
                break
            sleep(min(30.0, float(s.smtp_retry_base_seconds) * 2 ** (attempt - 1)) * (0.75 + random.random() / 2))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result
