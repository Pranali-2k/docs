"""Settings → Integrations: Microsoft Entra ID / Microsoft 365 mailboxes.

Admin-only, except the OAuth callback, which Microsoft calls via the browser redirect and
which is protected by the one-time state (and PKCE for sign-in).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.config import get_settings
from app.db import get_db
from app.models import IntegrationMailbox, User
from app.security import require_admin
from app.services import audit, crypto, mailbox_sync, msgraph, runtime_settings

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
log = logging.getLogger("docshealth.api.integrations")
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _view(db: Session) -> dict:
    conn = msgraph.get_connection(db)
    mailboxes = db.scalars(select(IntegrationMailbox).order_by(IntegrationMailbox.id)).all()
    return {
        "redirect_uri": msgraph.redirect_uri(),
        "delegated_scopes": msgraph.DELEGATED_SCOPES,
        "application_permissions": ["Mail.ReadWrite", "Mail.Send"],
        "connection": None if conn is None else {
            "auth_mode": conn.auth_mode, "tenant_id": conn.tenant_id, "client_id": conn.client_id,
            "client_secret_set": bool(conn.client_secret_encrypted), "client_secret_hint": conn.client_secret_hint,
            "status": conn.status, "connected_account": conn.connected_account, "connected_name": conn.connected_name,
            "scopes": conn.scopes, "token_expires_at": S.iso(conn.token_expires_at), "last_error": conn.last_error,
            "connected_by": conn.connected_by, "connected_at": S.iso(conn.connected_at),
            "poll_interval_seconds": conn.poll_interval_seconds,
        },
        "mailboxes": [_mailbox(m) for m in mailboxes],
        "outbound": runtime_settings.outbound_status(db),
        "test_mode": runtime_settings.test_mode(db),
    }


def _mailbox(m: IntegrationMailbox) -> dict:
    return {"id": m.id, "address": m.address, "display_name": m.display_name, "folder": m.folder, "enabled": m.enabled,
            "process_inbound": m.process_inbound, "reply_from_same_mailbox": m.reply_from_same_mailbox,
            "mark_as_read": m.mark_as_read, "is_default_sender": m.is_default_sender,
            "last_polled_at": S.iso(m.last_polled_at), "last_poll_count": m.last_poll_count, "last_error": m.last_error,
            "skip_sender_domains": mailbox_sync.parse_skip_list(m.skip_sender_domains),
            "skipped_internal_total": m.skipped_internal_total or 0, **mailbox_counts(m)}


def mailbox_counts(m: IntegrationMailbox) -> dict:
    read = (m.total_count - m.unread_count) if m.total_count is not None and m.unread_count is not None else None
    return {"unread_count": m.unread_count, "read_count": read, "total_count": m.total_count,
            "counts_at": S.iso(m.counts_at)}


@router.get("/microsoft")
def get_microsoft(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return _view(db)


class MicrosoftConfigIn(BaseModel):
    auth_mode: str = Field(pattern="^(delegated|application)$")
    tenant_id: str = Field(min_length=3, max_length=100)
    client_id: str = Field(min_length=36, max_length=36)
    client_secret: str | None = Field(default=None, max_length=512)  # blank = keep the saved one
    poll_interval_seconds: int = Field(default=120, ge=30, le=3600)


@router.put("/microsoft")
def put_microsoft(body: MicrosoftConfigIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    tenant = body.tenant_id.strip()
    if not (GUID.match(tenant) or re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", tenant.lower())):
        raise HTTPException(422, "Tenant ID must be the Directory (tenant) ID GUID or a domain like contoso.onmicrosoft.com")
    if not GUID.match(body.client_id.strip()):
        raise HTTPException(422, "Client ID must be the Application (client) ID GUID")
    conn = msgraph.get_connection(db, create=True)
    changed_identity = (conn.tenant_id, conn.client_id, conn.auth_mode) != (tenant, body.client_id.strip(), body.auth_mode)
    conn.tenant_id, conn.client_id, conn.auth_mode = tenant, body.client_id.strip(), body.auth_mode
    conn.poll_interval_seconds = body.poll_interval_seconds
    if body.client_secret:
        try:
            conn.client_secret_encrypted = crypto.encrypt(body.client_secret.strip())
        except crypto.CryptoError as exc:
            raise HTTPException(500, f"Could not secure the client secret: {exc}") from None
        conn.client_secret_hint = body.client_secret.strip()[-4:]
        changed_identity = True
    if not conn.client_secret_encrypted:
        raise HTTPException(422, "Client secret is required")
    if changed_identity:  # a different app/tenant/mode needs a fresh sign-in
        msgraph.disconnect(db, conn)
    elif conn.status == "not_configured":
        conn.status = "configured"
    audit.record(db, "integration", conn.id, "microsoft_configured", user.email, tenant_id=tenant,
                 client_id=conn.client_id, auth_mode=conn.auth_mode, secret_changed=bool(body.client_secret))
    db.commit()
    return _view(db)


@router.post("/microsoft/connect")
def connect(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Start 'Connect with Microsoft' (delegated) or 'Grant admin consent' (application)."""
    try:
        url = msgraph.start_authorization(db, actor=user.email)
        db.commit()
    except msgraph.GraphError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    return {"authorize_url": url}


@router.get("/microsoft/callback", include_in_schema=False)
def callback(request: Request, db: Session = Depends(get_db)):
    """Microsoft redirects the browser here. Always ends on the Integrations page with a status."""
    p = request.query_params
    base = f"{get_settings().portal_base_url.rstrip('/')}/settings/integrations"
    try:
        conn = msgraph.complete_authorization(db, state=p.get("state"), code=p.get("code"),
                                              admin_consent=p.get("admin_consent"), error=p.get("error"),
                                              error_description=p.get("error_description"))
        audit.record(db, "integration", conn.id, "microsoft_connected", conn.connected_by or "admin",
                     account=conn.connected_account, auth_mode=conn.auth_mode)
        db.commit()
        return RedirectResponse(f"{base}?" + urlencode({"ms": "connected"}), status_code=303)
    except msgraph.GraphError as exc:
        db.commit()  # keep state 'used' and the connection's last_error
        log.warning("Microsoft connect failed: %s", exc)
        return RedirectResponse(f"{base}?" + urlencode({"ms": "error", "reason": str(exc)[:300]}), status_code=303)
    except Exception:
        db.rollback()
        log.exception("Microsoft callback crashed")
        return RedirectResponse(f"{base}?" + urlencode({"ms": "error", "reason": "Unexpected error; see server log"}),
                                status_code=303)


@router.post("/microsoft/test")
def test_connection(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    conn = msgraph.get_connection(db)
    if conn is None:
        raise HTTPException(422, "Configure the Microsoft integration first")
    result: dict = {"ok": False}
    try:
        if conn.auth_mode == "delegated":
            me = msgraph.graph(db, "GET", "/me?$select=displayName,mail,userPrincipalName", conn=conn)
            result = {"ok": True, "account": me.get("mail") or me.get("userPrincipalName"), "name": me.get("displayName")}
        else:
            msgraph.access_token(db, conn)
            result = {"ok": True, "account": f"app:{conn.client_id}", "name": "Application token issued"}
        if conn.status != "connected":
            conn.status = "connected"
        conn.last_error = None
    except msgraph.GraphError as exc:
        result = {"ok": False, "error_kind": exc.kind, "error": str(exc)}
        conn.last_error = str(exc)
    audit.record(db, "integration", conn.id, "microsoft_tested", user.email, ok=result["ok"])
    db.commit()
    return {**result, "integration": _view(db)}


@router.post("/microsoft/disconnect")
def disconnect(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    conn = msgraph.get_connection(db)
    if conn is None:
        raise HTTPException(404, "Not configured")
    msgraph.disconnect(db, conn)
    audit.record(db, "integration", conn.id, "microsoft_disconnected", user.email)
    db.commit()
    return _view(db)


# --------------------------------------------------------------------------- mailboxes

class MailboxIn(BaseModel):
    address: EmailStr
    display_name: str | None = Field(default=None, max_length=200)
    folder: str = Field(default="Inbox", min_length=1, max_length=200)
    enabled: bool = True
    process_inbound: bool = True
    reply_from_same_mailbox: bool = True
    mark_as_read: bool = True
    is_default_sender: bool = False
    # None = default to the mailbox's own domain (internal mail is not a patient request)
    skip_sender_domains: list[str] | None = Field(default=None, max_length=50)


class MailboxPatch(BaseModel):
    display_name: str | None = None
    folder: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    process_inbound: bool | None = None
    reply_from_same_mailbox: bool | None = None
    mark_as_read: bool | None = None
    is_default_sender: bool | None = None
    skip_sender_domains: list[str] | None = Field(default=None, max_length=50)


_SKIP_ITEM = re.compile(r"^(?:[a-z0-9._%+'-]+@)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _clean_skip_list(items: list[str]) -> str:
    cleaned = mailbox_sync.parse_skip_list(",".join(items))
    bad = [i for i in cleaned if not _SKIP_ITEM.match(i)]
    if bad:
        raise HTTPException(422, f"Not a valid domain or email address: {', '.join(bad)}")
    return ", ".join(cleaned)


def _get_mailbox(db: Session, mailbox_id: int) -> IntegrationMailbox:
    mb = db.get(IntegrationMailbox, mailbox_id)
    if mb is None:
        raise HTTPException(404, "Mailbox not found")
    return mb


def _one_default(db: Session, mb: IntegrationMailbox) -> None:
    if mb.is_default_sender:
        for other in db.scalars(select(IntegrationMailbox).where(IntegrationMailbox.id != mb.id)):
            other.is_default_sender = False


class OutboundIn(BaseModel):
    enabled: bool
    reason: str | None = Field(default=None, max_length=300)


class TestModeIn(BaseModel):
    enabled: bool
    recipient: EmailStr | None = None


@router.put("/outbound/test-mode")
def set_test_mode(body: TestModeIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Test mode: every reply waits for approval and is delivered only to `recipient`."""
    if body.enabled and not body.recipient:
        raise HTTPException(422, "Enter the address that should receive test replies")
    try:
        runtime_settings.set_test_mode(db, body.enabled, str(body.recipient) if body.recipient else None, actor=user.email)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("could not change test mode")
        raise HTTPException(500, "Could not change test mode; nothing was changed") from None
    return _view(db)


@router.put("/outbound")
def set_outbound(body: OutboundIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Global outbound-email switch. Off = every reply is held in the portal and nothing is sent."""
    try:
        runtime_settings.set_outbound(db, body.enabled, actor=user.email, reason=body.reason)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("could not change outbound switch")
        raise HTTPException(500, "Could not change the outbound email setting; nothing was changed") from None
    return _view(db)


@router.post("/mailboxes", status_code=201)
def add_mailbox(body: MailboxIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    # A mailbox can be set up before Microsoft is connected; it is read once the connection is live.
    conn = msgraph.get_connection(db, create=True)
    address = str(body.address).lower()
    skip = body.skip_sender_domains if body.skip_sender_domains is not None else [address.rsplit("@", 1)[-1]]
    mb = IntegrationMailbox(connection_id=conn.id, address=address, created_by=user.email,
                            skip_sender_domains=_clean_skip_list(skip),
                            **body.model_dump(exclude={"address", "skip_sender_domains"}))
    db.add(mb)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That mailbox is already configured") from None
    _one_default(db, mb)
    audit.record(db, "mailbox", mb.id, "mailbox_added", user.email, address=mb.address, folder=mb.folder)
    db.commit()
    return _view(db)


@router.patch("/mailboxes/{mailbox_id}")
def update_mailbox(mailbox_id: int, body: MailboxPatch, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    mb = _get_mailbox(db, mailbox_id)
    changes = body.model_dump(exclude_unset=True)
    if "skip_sender_domains" in changes:
        changes["skip_sender_domains"] = _clean_skip_list(changes["skip_sender_domains"] or [])
    for k, v in changes.items():
        setattr(mb, k, v)
    _one_default(db, mb)
    audit.record(db, "mailbox", mb.id, "mailbox_updated", user.email, changes=changes)
    db.commit()
    return _view(db)


@router.delete("/mailboxes/{mailbox_id}")
def remove_mailbox(mailbox_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    mb = _get_mailbox(db, mailbox_id)
    audit.record(db, "mailbox", mb.id, "mailbox_removed", user.email, address=mb.address)
    db.delete(mb)
    db.commit()
    return _view(db)


@router.post("/mailboxes/{mailbox_id}/test")
def test_mailbox(mailbox_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    mb = _get_mailbox(db, mailbox_id)
    try:
        info = msgraph.folder_info(db, mb.address, mb.folder)
        mb.unread_count, mb.total_count = info.get("unreadItemCount"), info.get("totalItemCount")
        mb.counts_at = datetime.now(timezone.utc)
        mb.last_error = None
        result = {"ok": True, "folder": info.get("displayName"), "unread": info.get("unreadItemCount"),
                  "total": info.get("totalItemCount")}
    except msgraph.GraphError as exc:
        mb.last_error = str(exc)
        result = {"ok": False, "error_kind": exc.kind, "error": str(exc)}
    db.commit()
    return {**result, "integration": _view(db)}


@router.post("/mailboxes/{mailbox_id}/poll")
def poll_now(mailbox_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    mb = _get_mailbox(db, mailbox_id)
    try:
        result = mailbox_sync.poll_mailbox(db, mb, actor=user.email)
        return {"ok": True, **result, "integration": _view(db)}
    except msgraph.GraphError as exc:
        return {"ok": False, "error_kind": exc.kind, "error": str(exc), "integration": _view(db)}
