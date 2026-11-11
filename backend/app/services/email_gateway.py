"""Email integration: Outlook / Microsoft 365 via Graph, generic IMAP/SMTP, or a safe outbox.

Inbound  (EMAIL_INBOUND_MODE):  graph | imap | none  (webhook/manual intake is always available)
Outbound (EMAIL_OUTBOUND_MODE): smtp | graph | sendgrid | mailgun | outbox, see app.services.delivery
"""
from __future__ import annotations

import email
import email.header
import imaplib
import logging
import secrets
from datetime import datetime, timezone
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EmailMessage, PortalRequest, RequestStatus
from app.services import audit
from app.services.text_clean import clean_body, html_to_text, looks_like_html

GRAPH = "https://graph.microsoft.com/v1.0"
HELD = "Outbound email is turned off (Settings → Integrations); held in the portal, not sent"
AWAITING = "Test mode: waiting for approval. Approving sends it to {to} only, not to the patient"
log = logging.getLogger("docshealth.email")


# --------------------------------------------------------------------------- intake

def new_reference() -> str:
    return f"DH-{datetime.now(timezone.utc):%y%m%d}-{secrets.token_hex(2).upper()}"


def ingest_email(
    db: Session,
    *,
    from_address: str,
    subject: str,
    body: str,
    from_name: str | None = None,
    to_address: str | None = None,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    received_at: datetime | None = None,
    source: str = "manual",
    actor: str = "system",
    mailbox_id: int | None = None,
    provider_message_id: str | None = None,
    conversation_id: str | None = None,
) -> tuple[PortalRequest, bool]:
    """Store an inbound email and open a portal request for it. Returns (request, created)."""
    message_id = message_id or make_msgid(domain="intake.docshealth.local")
    existing = db.scalar(select(EmailMessage).where(EmailMessage.message_id == message_id))
    if existing and existing.request_id:
        return db.get(PortalRequest, existing.request_id), False
    msg = EmailMessage(
        message_id=message_id,
        direction="inbound",
        source=source,
        from_address=from_address.strip().lower(),
        from_name=from_name,
        to_address=to_address or get_settings().scheduling_mailbox,
        subject=subject or "",
        body_text=clean_body(body),  # Outlook/Word HTML -> readable text (no CSS/markup)
        body_raw=body if looks_like_html(body) else None,
        in_reply_to=in_reply_to,
        received_at=received_at or datetime.now(timezone.utc),
        mailbox_id=mailbox_id,
        provider_message_id=provider_message_id,
        conversation_id=conversation_id,
    )
    db.add(msg)
    db.flush()
    req = PortalRequest(reference=new_reference(), status=RequestStatus.RECEIVED.value, source_email_id=msg.id)
    db.add(req)
    db.flush()
    msg.request_id = req.id
    audit.record(db, "request", req.id, "request_created", actor, source=source, email_id=msg.id,
                 from_address=msg.from_address, subject=msg.subject)
    return req, True


# --------------------------------------------------------------------------- outbound

def send_reply(
    db: Session,
    req: PortalRequest,
    *,
    to_address: str,
    subject: str,
    body: str,
    actor: str = "system",
) -> EmailMessage:
    """Reply to the patient. If the request arrived in a connected Microsoft 365 mailbox, reply
    from that same mailbox in the same mail chain; otherwise use the configured outbound method."""
    source = req.source_email
    mailbox = _thread_mailbox(db, source)
    if mailbox is not None and source.from_address.lower() == to_address.lower():
        out = _send_thread_reply(db, req, mailbox, source, body, actor)
    else:
        out = send_email(
            db, to_address=to_address, subject=subject, body=body, actor=actor, source="portal",
            in_reply_to=source.message_id if source else None, request_id=req.id,
        )
    if req.first_response_at is None and out.send_status in {"sent", "stored"}:
        req.first_response_at = datetime.now(timezone.utc)
    audit.record(db, "request", req.id, "response_sent", actor, email_id=out.id, method=out.delivery_method,
                 send_status=out.send_status, to=to_address, mailbox=out.from_address)
    return out


def _thread_mailbox(db: Session, source: EmailMessage | None):
    """The integration mailbox to reply from, if this email came from one and is set to reply in-thread."""
    from app.models import IntegrationMailbox
    from app.services import msgraph

    if source is None or not source.mailbox_id or not source.provider_message_id:
        return None
    mb = db.get(IntegrationMailbox, source.mailbox_id)
    if mb is None or not mb.enabled or not mb.reply_from_same_mailbox:
        return None
    conn = msgraph.get_connection(db)
    if conn is None or conn.status not in ("connected", "reauth_required"):
        return None
    return mb


def _send_thread_reply(db: Session, req: PortalRequest, mailbox, source: EmailMessage, body: str,
                       actor: str) -> EmailMessage:
    subject = source.subject if (source.subject or "").lower().startswith("re:") else f"RE: {source.subject}"
    out = EmailMessage(
        message_id=make_msgid(domain=mailbox.address.split("@", 1)[-1]), direction="outbound", source="portal",
        from_address=mailbox.address, to_address=source.from_address, subject=subject, body_text=body,
        in_reply_to=source.message_id, request_id=req.id, sent_by=actor, send_status="queued",
        delivery_method="graph-reply", mailbox_id=mailbox.id, provider_message_id=source.provider_message_id,
        conversation_id=source.conversation_id,
    )
    db.add(out)
    db.flush()
    if not _hold_if_disabled(db, out):
        _attempt_thread_reply(db, out, mailbox)
    return out


def _attempt_thread_reply(db: Session, out: EmailMessage, mailbox) -> None:
    from app.services import msgraph

    try:
        msgraph.reply_in_thread(db, mailbox.address, out.provider_message_id, out.body_text)
        out.send_status, out.send_error, out.delivered_at = "sent", None, datetime.now(timezone.utc)
    except msgraph.GraphError as exc:
        out.send_status, out.send_error = "failed", f"[{exc.kind}] {exc}"[:1000]
        log.warning("in-thread reply %s from %s failed: %s", out.id, mailbox.address, exc)
    except Exception as exc:  # never let a reply crash the request
        log.exception("in-thread reply %s crashed", out.id)
        out.send_status, out.send_error = "failed", f"[unknown] {exc}"[:1000]


def send_email(
    db: Session,
    *,
    to_address: str,
    subject: str,
    body: str,
    actor: str = "system",
    source: str = "portal",
    in_reply_to: str | None = None,
    request_id: int | None = None,
) -> EmailMessage:
    """Record and deliver one outbound email via app.services.delivery (SMTP / Graph / API).
    Never raises: the outcome is stored on the message (send_status sent | stored | failed,
    send_error, delivery_method) so staff can see it and retry."""
    settings = get_settings()
    mode = settings.email_outbound_mode or "outbox"
    sender = settings.scheduling_mailbox if mode in ("graph", "outbox") else settings.smtp_from
    out = EmailMessage(
        message_id=make_msgid(domain=(sender.split("@", 1)[-1] if "@" in sender else "docshealth.local")),
        direction="outbound",
        source=source,
        from_address=sender,
        to_address=to_address,
        subject=subject,
        body_text=body,
        in_reply_to=in_reply_to,
        request_id=request_id,
        sent_by=actor,
        send_status="queued",
    )
    db.add(out)
    db.flush()
    if _hold_if_disabled(db, out):
        return out
    _attempt(out)
    return out


def _hold_if_disabled(db: Session, out: EmailMessage) -> bool:
    """The gate every reply passes. Test mode: keep it for approval (delivered only to the test address).
    Kill switch off: record it as held. Either way no mail server is contacted here."""
    from app.services.runtime_settings import outbound_enabled, test_mode

    tm = test_mode(db)
    if tm:
        out.send_status, out.send_error = "awaiting_approval", AWAITING.format(to=tm["recipient"])
        out.redirect_to = tm["recipient"]
        log.info("outbound email %s to %s awaiting approval (test mode -> %s)", out.id, out.to_address, tm["recipient"])
        return True
    if outbound_enabled(db):
        return False
    out.send_status, out.send_error = "held", HELD
    out.delivery_method = out.delivery_method or "held"
    log.info("outbound email %s to %s held (outbound disabled)", out.id, out.to_address)
    return True


def redeliver(db: Session, out: EmailMessage, *, actor: str) -> EmailMessage:
    """Retry an outbound message that was stored (outbox) or failed, with the current settings."""
    if out.direction != "outbound":
        raise ValueError("Only outbound messages can be re-sent")
    if out.send_status == "sent":
        raise ValueError("This message was already delivered")
    from app.services.runtime_settings import outbound_enabled, test_mode

    if test_mode(db):
        raise ValueError("Test mode is on: use 'Approve & send', which delivers to the test address only")

    if not outbound_enabled(db):
        raise ValueError("Outbound email is turned off. An admin can turn it on in Settings → Integrations")
    if out.delivery_method == "graph-reply" and out.mailbox_id:
        from app.models import IntegrationMailbox

        mb = db.get(IntegrationMailbox, out.mailbox_id)
        if mb is None:
            raise ValueError("The mailbox this reply belongs to was removed; send a new reply instead")
        _attempt_thread_reply(db, out, mb)
    else:
        s = get_settings()
        out.from_address = s.scheduling_mailbox if s.email_outbound_mode == "graph" else s.smtp_from
        _attempt(out)
    audit.record(db, "email", out.id, "email_resent", actor, send_status=out.send_status,
                 method=out.delivery_method, error=out.send_error)
    return out


def approve_test_send(db: Session, out: EmailMessage, *, actor: str) -> EmailMessage:
    """Test mode: deliver a waiting (or held) reply to the test address ONLY, as a new message (never in the
    patient's thread). The record keeps the real recipient; `redirect_to` shows where it actually went."""
    from app.services.delivery import deliver
    from app.services.runtime_settings import test_mode

    if out.direction != "outbound":
        raise ValueError("Only outbound messages can be approved")
    if out.send_status not in {"awaiting_approval", "held", "failed", "stored"}:
        raise ValueError(f"This message is {out.send_status}; nothing to approve")
    tm = test_mode(db)
    if not tm:
        raise ValueError("Test mode is off. Turn it on in Settings → Integrations, or use Retry sending")
    to = tm["recipient"]
    subject = f"[TEST, would go to {out.to_address}] {out.subject}"
    body = (f"*** DOCS HEALTH TEST MODE ***\nThis reply would have been sent to {out.to_address}.\n"
            f"It was approved by {actor} and delivered only to {to}.\n{'-' * 60}\n\n{out.body_text}")
    try:
        result = deliver(to, subject, body)
    except Exception as exc:  # deliver() never raises by contract
        log.exception("test-mode delivery crashed for email %s", out.id)
        result = None
        error = f"[unknown] {exc}"
    out.redirect_to = to
    if result is not None and result.ok and not result.refused:
        out.send_status, out.send_error = "test_sent", None
        out.delivered_at = datetime.now(timezone.utc)
        out.delivery_method = f"test:{get_settings().email_outbound_mode}"
    else:
        if result is not None:
            error = f"[{result.error_kind}] {result.error}" if not result.ok else f"[rejected] refused: {result.refused}"
        out.send_status = "awaiting_approval"
        out.send_error = (f"Approved, but could not be sent to {to}: {error}. "
                          "Set up a sender (Settings → Email delivery) and approve again.")[:1000]
    audit.record(db, "email", out.id, "test_send_approved", actor, delivered_to=to, real_recipient=out.to_address,
                 send_status=out.send_status, error=out.send_error)
    return out


def _attempt(out: EmailMessage) -> None:
    from app.services.delivery import deliver

    mode = get_settings().email_outbound_mode or "outbox"
    out.delivery_method = mode
    if mode == "outbox":
        try:
            _write_mail_drop(out)
        except OSError as exc:
            log.warning("mail drop write failed for %s: %s", out.id, exc)
        out.send_status, out.send_error = "stored", "Email delivery is not configured (outbox mode): stored, not sent"
        return
    try:
        result = deliver(out.to_address, out.subject, out.body_text, message_id=out.message_id,
                         in_reply_to=out.in_reply_to)
    except Exception as exc:  # deliver() never raises by contract; belt and braces
        log.exception("delivery crashed for email %s", out.id)
        out.send_status, out.send_error = "failed", f"[unknown] {exc}"[:1000]
        return
    if result.ok and not result.refused:
        out.send_status, out.send_error = "sent", None
        out.delivered_at = datetime.now(timezone.utc)
    else:
        detail = (f"[{result.error_kind}] {result.error}" if not result.ok else f"[rejected] refused: {result.refused}")
        if result.server_reply:
            detail += f" | server: {result.server_reply}"
        out.send_status, out.send_error = "failed", detail[:1000]
        log.warning("email %s to %s not delivered: %s", out.id, out.to_address, detail)


def _write_mail_drop(out: EmailMessage) -> None:
    """Outbox mode: also save the message as an .eml file so it can be opened locally
    (the only way to read e.g. a password-reset link when no real mailbox is configured)."""
    folder = Path(get_settings().mail_drop_dir)
    folder.mkdir(parents=True, exist_ok=True)
    mime = MimeMessage()
    mime["From"], mime["To"], mime["Subject"] = get_settings().scheduling_mailbox, out.to_address, out.subject
    mime["Message-ID"] = out.message_id
    mime["Date"] = formatdate(localtime=True)
    mime.set_content(out.body_text)
    name = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{out.id}.eml"
    (folder / name).write_bytes(bytes(mime))


def _graph_token() -> str:
    s = get_settings()
    if not (s.graph_tenant_id and s.graph_client_id and s.graph_client_secret):
        raise RuntimeError("GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET are not configured")
    resp = httpx.post(
        f"https://login.microsoftonline.com/{s.graph_tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": s.graph_client_id,
            "client_secret": s.graph_client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# --------------------------------------------------------------------------- mailbox polling

def poll_mailbox(db: Session, *, limit: int = 25) -> list[PortalRequest]:
    """Fetch unread mail from the scheduling mailbox and open portal requests.
    Connection/auth problems raise (callers report them); a single bad message never
    stops the rest of the batch."""
    mode = get_settings().email_inbound_mode
    if mode == "graph":
        return _poll_graph(db, limit)
    if mode == "imap":
        return _poll_imap(db, limit)
    return []


def _skip_reason(from_address: str, headers) -> str | None:
    """Avoid mail loops: our own messages, auto-replies, bounces and bulk mail."""
    s = get_settings()
    own = {a.lower() for a in (s.scheduling_mailbox, s.smtp_from, s.smtp_username, s.imap_username) if a}
    if from_address.lower() in own:
        return "sent by the scheduling mailbox itself"
    auto = (headers.get("Auto-Submitted") or "").lower()
    if auto and auto != "no":
        return f"auto-submitted ({auto})"
    if (headers.get("Precedence") or "").lower() in {"bulk", "junk", "list", "auto_reply"}:
        return "bulk/auto-reply precedence"
    if headers.get("X-Autoreply") or headers.get("X-Autorespond"):
        return "auto-reply"
    if from_address.lower().startswith(("mailer-daemon@", "postmaster@")):
        return "delivery status notification"
    return None


def _poll_graph(db: Session, limit: int) -> list[PortalRequest]:
    s = get_settings()
    headers = {"Authorization": f"Bearer {_graph_token()}", "Prefer": 'outlook.body-content-type="text"'}
    resp = httpx.get(
        f"{GRAPH}/users/{s.scheduling_mailbox}/mailFolders/inbox/messages",
        params={"$filter": "isRead eq false", "$top": limit, "$orderby": "receivedDateTime asc",
                "$select": "id,internetMessageId,subject,body,from,receivedDateTime,internetMessageHeaders"},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    created = []
    for m in resp.json().get("value", []):
        try:
            sender = (m.get("from") or {}).get("emailAddress") or {}
            address = sender.get("address", "unknown@unknown")
            hdrs = {h["name"]: h["value"] for h in (m.get("internetMessageHeaders") or [])}
            reason = _skip_reason(address, hdrs)
            if reason is None:
                req, is_new = ingest_email(
                    db, from_address=address, from_name=sender.get("name"), subject=m.get("subject") or "",
                    body=(m.get("body") or {}).get("content", ""), message_id=m.get("internetMessageId") or m["id"],
                    received_at=datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")), source="graph")
                db.commit()
                if is_new:
                    created.append(req)
            else:
                log.info("skipped inbound graph message from %s: %s", address, reason)
            httpx.patch(f"{GRAPH}/users/{s.scheduling_mailbox}/messages/{m['id']}", headers=headers,
                        json={"isRead": True}, timeout=30)
        except Exception:  # keep it unread so the next poll retries it
            db.rollback()
            log.exception("could not ingest graph message %s", m.get("id"))
    return created


def _poll_imap(db: Session, limit: int) -> list[PortalRequest]:
    import ssl

    s = get_settings()
    created = []
    with imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=ssl.create_default_context(), timeout=30) as imap:
        imap.login(s.imap_username, s.imap_password)
        status, _ = imap.select(s.imap_folder)
        if status != "OK":
            raise RuntimeError(f"IMAP folder {s.imap_folder!r} not found")
        _, data = imap.search(None, "UNSEEN")
        for num in data[0].split()[:limit]:
            try:
                # BODY.PEEK: don't mark as read until the request is safely stored
                _, parts = imap.fetch(num, "(BODY.PEEK[])")
                msg = email.message_from_bytes(parts[0][1])
                name, addr = parseaddr(msg.get("From", ""))
                reason = _skip_reason(addr or "", msg)
                if reason is None:
                    try:
                        received = parsedate_to_datetime(msg.get("Date"))
                    except (TypeError, ValueError):
                        received = None
                    req, is_new = ingest_email(
                        db, from_address=addr or "unknown@unknown", from_name=name or None,
                        subject=str(email.header.make_header(email.header.decode_header(msg.get("Subject", "")))),
                        body=_plain_body(msg), message_id=msg.get("Message-ID"), in_reply_to=msg.get("In-Reply-To"),
                        received_at=received, source="imap")
                    db.commit()
                    if is_new:
                        created.append(req)
                else:
                    log.info("skipped inbound imap message from %s: %s", addr, reason)
                imap.store(num, "+FLAGS", "\\Seen")
            except Exception:  # leave unseen for the next poll
                db.rollback()
                log.exception("could not ingest imap message %s", num)
    return created


def test_inbound(settings=None) -> dict:
    """Check the inbound mailbox can be opened (login + folder + unread count). Never raises."""
    import ssl

    s = settings or get_settings()
    mode = s.email_inbound_mode
    try:
        if mode == "imap":
            ctx = ssl.create_default_context()
            with imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=ctx, timeout=20) as imap:
                imap.login(s.imap_username, s.imap_password)
                status, _ = imap.select(s.imap_folder, readonly=True)
                if status != "OK":
                    return {"ok": False, "mode": mode, "error": f"Folder {s.imap_folder!r} not found"}
                _, data = imap.search(None, "UNSEEN")
                return {"ok": True, "mode": mode, "unread": len(data[0].split())}
        if mode == "graph":
            r = httpx.get(f"{GRAPH}/users/{s.scheduling_mailbox}/mailFolders/inbox",
                          headers={"Authorization": f"Bearer {_graph_token()}"}, timeout=20)
            if r.status_code != 200:
                return {"ok": False, "mode": mode, "error": f"Graph returned {r.status_code}: {r.text[:200]}"}
            return {"ok": True, "mode": mode, "unread": r.json().get("unreadItemCount")}
        return {"ok": False, "mode": mode, "error": "Inbound mail is off (EMAIL_INBOUND_MODE=none)"}
    except imaplib.IMAP4.error as exc:
        hint = (" Microsoft 365 no longer accepts password IMAP; use Graph." if "outlook" in (s.imap_host or "")
                else " Use an app password if the account has 2-step verification.")
        return {"ok": False, "mode": mode, "error": f"IMAP login/command failed: {exc}.{hint}"}
    except Exception as exc:  # DNS, TLS, timeout, token errors
        return {"ok": False, "mode": mode, "error": f"{type(exc).__name__}: {exc}"}


def _plain_body(msg) -> str:
    """Best text body: text/plain part, else HTML converted to readable text. Never raises."""

    def decode(part) -> str:
        payload = part.get_payload(decode=True) or b""
        try:
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
        except LookupError:  # unknown charset name
            return payload.decode("utf-8", "replace")

    def is_attachment(part) -> bool:
        return "attachment" in str(part.get("Content-Disposition") or "").lower()

    parts = list(msg.walk()) if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/plain" and not is_attachment(part):
            return decode(part)
    for part in parts:
        if part.get_content_type() == "text/html" and not is_attachment(part):
            return html_to_text(decode(part))
    return ""
