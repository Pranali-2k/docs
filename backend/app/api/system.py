"""Auth, email intake, dashboard reporting, audit trail, outbox, demo lab and system info."""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.api.requests import detail
from app.config import get_settings
from app.db import get_db
from app.demo_samples import SAMPLES, render
from app.llm.providers import get_llm
from app.models import Appointment, AuditLog, EmailMessage, Patient, PortalRequest, User
from app.security import create_token, current_user, require_admin, verify_password
from app.services import audit_text, email_gateway, password_reset, pipeline, scheduling

router = APIRouter(prefix="/api", tags=["system"])
log = logging.getLogger("docshealth.api")


# --------------------------------------------------------------------------- auth

class LoginIn(BaseModel):
    email: str
    password: str


def _user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    from app.services import audit, login_guard

    ip = request.client.host if request.client else None
    generic = HTTPException(401, "Invalid email or password")
    if login_guard.ip_blocked(ip):
        raise HTTPException(429, "Too many failed sign-in attempts. Try again in 15 minutes.")
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not user.is_active:
        verify_password(body.password, "pbkdf2_sha256$390000$x$0")  # equalize timing for unknown accounts
        login_guard.record_ip_failure(ip)
        raise generic
    if login_guard.account_locked(user):
        raise HTTPException(423, "This account is temporarily locked after repeated failed sign-ins. "
                                 "Try again in 15 minutes or reset your password.")
    if not verify_password(body.password, user.password_hash):
        locked = login_guard.record_failure(user)
        login_guard.record_ip_failure(ip)
        audit.record(db, "user", user.id, "login_locked" if locked else "login_failed", "anonymous", ip=ip)
        db.commit()
        raise generic
    login_guard.record_success(user)
    audit.record(db, "user", user.id, "login_succeeded", user.email, ip=ip)
    db.commit()
    return {"access_token": create_token(user), "token_type": "bearer", "user": _user(user)}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return _user(user)


class ForgotIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


GENERIC_RESET_REPLY = {
    "message": "If an account exists for that email, we've sent a link to verify your identity and reset "
    "the password. Check your inbox."
}


@router.post("/auth/forgot-password", status_code=202)
def forgot_password(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    """Always answers the same way, whether or not the email has an account."""
    try:
        password_reset.request_reset(db, body.email, ip=request.client.host if request.client else None)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        log.exception("password reset request failed")
        raise HTTPException(503, "Password reset is temporarily unavailable. Try again shortly.") from None
    return GENERIC_RESET_REPLY


@router.get("/auth/reset-password/verify")
def verify_reset_link(token: str, db: Session = Depends(get_db)):
    try:
        user = password_reset.verify(db, token)
    except password_reset.ResetError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"valid": True, "email": password_reset.mask_email(user.email),
            "policy": f"At least {password_reset.MIN_LENGTH} characters with upper- and lower-case letters, "
                      "a number and a symbol."}


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=1, max_length=256)


@router.post("/auth/reset-password")
def reset_password(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    try:
        user = password_reset.complete_reset(db, body.token, body.new_password,
                                             ip=request.client.host if request.client else None)
        db.commit()
    except password_reset.ResetError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from None
    return {"message": "Your password has been changed. Sign in with your new password.", "email": user.email}


# --------------------------------------------------------------------------- intake

class InboundEmail(BaseModel):
    from_address: EmailStr
    from_name: str | None = None
    subject: str = ""
    body: str
    message_id: str | None = None
    in_reply_to: str | None = None
    received_at: datetime | None = None
    process: bool = True


def _intake(db: Session, body: InboundEmail, source: str, actor: str) -> dict:
    req, created = email_gateway.ingest_email(
        db, from_address=str(body.from_address), from_name=body.from_name, subject=body.subject, body=body.body,
        message_id=body.message_id, in_reply_to=body.in_reply_to, received_at=body.received_at, source=source,
        actor=actor,
    )
    db.commit()
    if created and body.process:
        try:
            pipeline.process_request(db, req, actor="ai-assistant")
            db.commit()
        except Exception as exc:  # keep the request; staff can reprocess
            db.rollback()
            log.exception("processing request %s failed", req.id)
            req = db.get(PortalRequest, req.id)
            req.status = "needs_review"
            req.review_reasons = [{"code": "AI_ENGINE_ERROR", "message": f"Processing failed: {exc}"[:500]}]
            db.commit()
    return {"created": created, "request": detail(db, req)}


@router.post("/intake/email", status_code=201)
def intake_email(body: InboundEmail, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Attach an email to the AI application (manual forward, Power Automate / Graph webhook relay)."""
    return _intake(db, body, "manual", user.email)


@router.post("/intake/poll")
def poll(db: Session = Depends(get_db), user: User = Depends(current_user)):
    try:
        created = email_gateway.poll_mailbox(db)
    except Exception as exc:
        raise HTTPException(502, f"Mailbox poll failed: {exc}") from None
    processed, failed = [], []
    for req in created:
        try:
            pipeline.process_request(db, req, actor="ai-assistant")
            db.commit()
            processed.append(S.request_row(req))
        except Exception:  # leave it in the inbox for staff; continue with the next email
            db.rollback()
            log.exception("processing polled request %s failed", req.id)
            failed.append(req.id)
    return {"mode": get_settings().email_inbound_mode, "processed": processed, "failed": failed}


# --------------------------------------------------------------------------- dashboard

@router.get("/dashboard/summary")
def dashboard(days: int = 30, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Request statistics for the last `days` days. The numbers come from the dashboard_summary() database function;
    the API adds the clinic-local date and the mailbox statistics."""
    days = min(max(days, 1), 3650)
    data = db.execute(text("SELECT dashboard_summary(:days, :today)"),
                      {"days": days, "today": scheduling.clinic_now().date()}).scalar()
    data = data if isinstance(data, dict) else json.loads(data)
    # jsonb keeps no key order: list the review reasons most frequent first
    data["review_reasons"] = dict(sorted(data["review_reasons"].items(), key=lambda kv: (-kv[1], kv[0])))
    data["mailboxes"] = _mailbox_stats(db)
    return data


def _mailbox_stats(db: Session) -> list[dict]:
    """Connected Microsoft 365 mailboxes: folder counts (cached on each check) + what the portal imported."""
    try:
        from app.models import IntegrationMailbox

        from app.services.mailbox_sync import skip_counts

        imported = dict(db.execute(select(EmailMessage.mailbox_id, func.count()).where(
            EmailMessage.direction == "inbound", EmailMessage.mailbox_id.is_not(None)).group_by(EmailMessage.mailbox_id)).all())
        out = []
        for m in db.scalars(select(IntegrationMailbox).order_by(IntegrationMailbox.id)):
            known = m.total_count is not None and m.unread_count is not None
            skips = skip_counts(db, m.id)
            n_imported, n_skipped = imported.get(m.id, 0), sum(skips.values())
            out.append({"id": m.id, "address": m.address, "folder": m.folder, "enabled": m.enabled,
                        "unread": m.unread_count, "read": (m.total_count - m.unread_count) if known else None,
                        "total": m.total_count, "counts_at": m.counts_at.isoformat() if m.counts_at else None,
                        "imported": n_imported,
                        "skipped_internal": skips.get("internal", 0),
                        "skipped_other": skips.get("automated", 0) + skips.get("own_mailbox", 0),
                        # what is in the folder but not yet handled (new mail waiting for the next check, or older
                        # mail the cursor started after); never negative even if mail was deleted from the folder
                        "pending": max(0, m.total_count - n_imported - n_skipped) if m.total_count is not None else None,
                        "last_polled_at": m.last_polled_at.isoformat() if m.last_polled_at else None})
        return out
    except Exception:  # the dashboard must load even if mailbox stats can't be read
        log.exception("could not load mailbox stats for the dashboard")
        return []


# --------------------------------------------------------------------------- audit & outbox

@router.get("/audit")
def audit_log(entity_type: str | None = None, entity_id: int | None = None, actor: str | None = None,
              limit: int = 200, db: Session = Depends(get_db), _: User = Depends(current_user)):
    stmt = select(AuditLog)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    return audit_text.annotate(db, list(db.scalars(stmt.order_by(AuditLog.id.desc()).limit(min(limit, 1000)))))


@router.get("/emails")
def emails(direction: str | None = None, limit: int = 100, db: Session = Depends(get_db),
           _: User = Depends(current_user)):
    stmt = select(EmailMessage)
    if direction:
        stmt = stmt.where(EmailMessage.direction == direction)
    return [S.email(m) for m in db.scalars(stmt.order_by(EmailMessage.received_at.desc()).limit(min(limit, 500)))]


# --------------------------------------------------------------------------- demo lab

def _demo_guard() -> None:
    if not get_settings().demo_on:
        raise HTTPException(404, "Demo tools are disabled in this environment")


@router.get("/demo/samples")
def demo_samples(db: Session = Depends(get_db), _: User = Depends(current_user)):
    _demo_guard()
    return [render(db, k) for k in SAMPLES]


@router.post("/demo/samples/{key}", status_code=201)
def demo_inject(key: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _demo_guard()
    if key not in SAMPLES:
        raise HTTPException(404, "Unknown sample")
    s = render(db, key)
    body = InboundEmail(from_address=s["from_address"], from_name=s["from_name"], subject=s["subject"], body=s["body"])
    return _intake(db, body, "demo", user.email)


# --------------------------------------------------------------------------- system

@router.get("/health")
def health(request: Request, db: Session = Depends(get_db)):
    dialect = db.get_bind().dialect.name
    try:
        db.execute(select(1))
    except SQLAlchemyError as exc:
        log.error("health check: database unreachable: %s", exc)
        return JSONResponse(status_code=503, content={"status": "degraded", "database": dialect,
                                                      "detail": "database unreachable"})
    return {"status": "ok", "database": dialect}


def _test_mode() -> dict | None:
    from app.db import SessionLocal
    from app.services.runtime_settings import test_mode

    try:
        with SessionLocal() as db:
            return test_mode(db)
    except Exception:
        return None


def _outbound_enabled() -> bool:
    from app.db import SessionLocal
    from app.services.runtime_settings import outbound_enabled

    try:
        with SessionLocal() as db:
            return outbound_enabled(db)
    except Exception:  # report as off rather than fail the info endpoint
        return False


@router.get("/system/info")
def info(_: User = Depends(current_user)):
    s = get_settings()
    llm = get_llm()
    return {
        "llm": llm.describe(),
        "llm_options": ["mock", "openai", "azure_openai", "gemini", "huggingface"],
        "email_inbound_mode": s.email_inbound_mode,
        "email_outbound_mode": s.email_outbound_mode,
        "scheduling_mailbox": s.scheduling_mailbox,
        "eligibility_adapter": s.eligibility_adapter,
        "ai_confidence_threshold": s.ai_confidence_threshold,
        "match_confidence_threshold": s.match_confidence_threshold,
        "auto_book_appointments": s.auto_book_appointments,
        "auto_send_responses": s.auto_send_responses,
        "clinic_name": s.clinic_name,
        "outbound_email_enabled": _outbound_enabled(),
        "outbound_test_mode": _test_mode(),
        "practice_name": s.practice_name,
        "practice_group_npi": s.practice_group_npi,
        "clinic_timezone": s.clinic_timezone,
        "environment": s.environment,
        "demo_enabled": s.demo_on,
    }


SECRET_FIELDS = {"smtp_password", "graph_client_secret", "sendgrid_api_key", "mailgun_api_key", "imap_password"}
METHOD_FIELDS = {
    "smtp": ["smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password", "smtp_from", "smtp_from_name"],
    "graph": ["graph_tenant_id", "graph_client_id", "graph_client_secret", "scheduling_mailbox", "smtp_from_name"],
    "sendgrid": ["sendgrid_api_key", "smtp_from", "smtp_from_name"],
    "mailgun": ["mailgun_api_key", "mailgun_domain", "mailgun_region", "smtp_from", "smtp_from_name"],
}


class EmailSettingsIn(BaseModel):
    method: str = Field(default="smtp", pattern="^(smtp|graph|sendgrid|mailgun)$")
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: str | None = Field(default=None, pattern="^(starttls|ssl|none)$")  # none: localhost only (enforced by mailer)
    smtp_username: str | None = Field(default=None, max_length=255)
    smtp_password: str | None = Field(default=None, max_length=512)  # blank = keep the saved one
    smtp_from: EmailStr | None = None
    smtp_from_name: str | None = Field(default=None, max_length=120)
    graph_tenant_id: str | None = Field(default=None, max_length=100)
    graph_client_id: str | None = Field(default=None, max_length=100)
    graph_client_secret: str | None = Field(default=None, max_length=512)
    scheduling_mailbox: EmailStr | None = None
    sendgrid_api_key: str | None = Field(default=None, max_length=512)
    mailgun_api_key: str | None = Field(default=None, max_length=512)
    mailgun_domain: str | None = Field(default=None, max_length=255)
    mailgun_region: str | None = Field(default=None, pattern="^(us|eu)$")
    to: EmailStr
    save_on_success: bool = True
    enable_outbound: bool = True


class InboundSettingsIn(BaseModel):
    method: str = Field(pattern="^(imap|graph|none)$")
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=993, ge=1, le=65535)
    imap_username: str | None = Field(default=None, max_length=255)
    imap_password: str | None = Field(default=None, max_length=512)
    imap_folder: str | None = Field(default="INBOX", max_length=100)
    save_on_success: bool = True


def _email_settings_view() -> dict:
    s = get_settings()
    view = {"email_outbound_mode": s.email_outbound_mode, "email_inbound_mode": s.email_inbound_mode,
            "smtp_host": s.smtp_host, "smtp_port": s.smtp_port, "smtp_security": s.smtp_security,
            "smtp_username": s.smtp_username, "smtp_from": s.smtp_from, "smtp_from_name": s.smtp_from_name,
            "graph_tenant_id": s.graph_tenant_id, "graph_client_id": s.graph_client_id,
            "scheduling_mailbox": s.scheduling_mailbox, "mailgun_domain": s.mailgun_domain,
            "mailgun_region": s.mailgun_region, "imap_host": s.imap_host, "imap_port": s.imap_port,
            "imap_username": s.imap_username, "imap_folder": s.imap_folder}
    # secrets: only whether they are set, never the values
    view.update({f"{k}_set": bool(getattr(s, k)) for k in SECRET_FIELDS})
    view["password_set"] = view["smtp_password_set"]
    return view


def _merged(current, body: BaseModel, fields: list[str]):
    """Candidate settings: form values, falling back to saved values for blank fields (incl. secrets)."""
    update = {}
    for k in fields:
        v = getattr(body, k, None)
        if v in (None, ""):
            continue
        update[k] = str(v).strip() if isinstance(v, str) or k in {"smtp_from", "scheduling_mailbox"} else v
    return current.model_copy(update=update)


@router.get("/system/email-settings")
def get_email_settings(_: User = Depends(require_admin)):
    return _email_settings_view()


@router.get("/system/email-providers/detect")
def detect_provider(email: str, _: User = Depends(require_admin)):
    """Suggested send/receive settings for a mailbox (Gmail, Microsoft 365 company domains, ...)."""
    from app.services.mail_providers import detect

    return detect(email).as_dict()


@router.post("/system/email-settings/test")
def test_email_settings(body: EmailSettingsIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Send a real test email with the chosen method/settings; save them only if it is accepted.
    Secrets are never returned or logged."""
    from app.services import audit
    from app.services.delivery import deliver
    from app.services.envfile import update_env

    fields = METHOD_FIELDS[body.method]
    candidate = _merged(get_settings(), body, fields).model_copy(update={"smtp_max_attempts": 2})
    if body.method == "graph" and body.scheduling_mailbox:
        candidate = candidate.model_copy(update={"smtp_from": str(body.scheduling_mailbox)})
    sender = candidate.scheduling_mailbox if body.method == "graph" else candidate.smtp_from
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = f"Docs Health test email ({stamp})"
    text_body = (f"This is a test email sent from the Docs Health scheduling portal by {user.email}.\n\n"
                 f"Method: {body.method}\nFrom: {sender}\nTime: {stamp}\n\nIf you received this, email delivery works.")
    result = deliver(str(body.to), subject, text_body, method=body.method, settings=candidate)

    try:  # record the attempt in the email log and audit trail, whatever happened
        db.add(EmailMessage(
            message_id=result.message_id or f"<test-{datetime.now().timestamp()}@docshealth.local>",
            direction="outbound", source="test", from_address=sender or "-", to_address=str(body.to),
            subject=subject, body_text=text_body, sent_by=user.email, delivery_method=body.method,
            send_status="sent" if result.ok else "failed",
            delivered_at=datetime.now(timezone.utc) if result.ok else None,
            send_error=None if result.ok else
            f"[{result.error_kind}] {result.error} {result.server_reply or ''}".strip()[:1000]))
        audit.record(db, "system", None, "email_test_sent", user.email, to=str(body.to), ok=result.ok,
                     method=body.method, error_kind=result.error_kind)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        log.exception("could not record test email")

    saved = False
    if result.ok and body.save_on_success:
        values = {k.upper(): str(getattr(candidate, k)) for k in fields}
        if body.method == "graph":
            values["SCHEDULING_MAILBOX"] = candidate.scheduling_mailbox
        elif candidate.smtp_from:
            values["SCHEDULING_MAILBOX"] = candidate.smtp_from
        if body.enable_outbound:
            values["EMAIL_OUTBOUND_MODE"] = body.method
        try:
            update_env(values)
            get_settings.cache_clear()  # apply immediately, no restart needed
            saved = True
            audit.record(db, "system", None, "email_settings_saved", user.email, method=body.method,
                         outbound_enabled=body.enable_outbound)
            db.commit()
        except (OSError, ValueError) as exc:
            log.error("could not save email settings: %s", exc)
            raise HTTPException(500, f"Test email was accepted, but saving settings failed: {exc}") from None
    return {
        "ok": result.ok, "method": body.method, "accepted": result.accepted, "refused": result.refused,
        "attempts": result.attempts, "error_kind": result.error_kind, "error": result.error,
        "server_reply": result.server_reply, "message_id": result.message_id, "elapsed_ms": result.elapsed_ms,
        "saved": saved, "settings": _email_settings_view(),
    }


@router.post("/system/email-settings/test-inbound")
def test_inbound_settings(body: InboundSettingsIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Open the inbound mailbox (IMAP or Graph) read-only; save and enable on success."""
    from app.services import audit
    from app.services.email_gateway import test_inbound
    from app.services.envfile import update_env

    fields = ["imap_host", "imap_port", "imap_username", "imap_password", "imap_folder"]
    candidate = _merged(get_settings(), body, fields).model_copy(update={"email_inbound_mode": body.method})
    result = test_inbound(candidate)
    audit.record(db, "system", None, "inbound_mail_test", user.email, method=body.method, ok=result.get("ok"))
    db.commit()
    result["saved"] = False
    if result.get("ok") and body.save_on_success:
        values = {"EMAIL_INBOUND_MODE": body.method}
        if body.method == "imap":
            values.update({k.upper(): str(getattr(candidate, k)) for k in fields})
        try:
            update_env(values)
            get_settings.cache_clear()
            result["saved"] = True
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"Mailbox opened, but saving settings failed: {exc}") from None
    result["settings"] = _email_settings_view()
    return result


@router.post("/emails/{email_id}/resend")
def resend_email(email_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Retry a stored (outbox) or failed outbound email with the current delivery settings."""
    msg = db.get(EmailMessage, email_id)
    if msg is None:
        raise HTTPException(404, "Email not found")
    try:
        email_gateway.redeliver(db, msg, actor=user.email)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    return S.email(msg)


@router.post("/emails/{email_id}/approve-test")
def approve_test_email(email_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Test mode: send a waiting reply to the test address only (never to the patient)."""
    msg = db.get(EmailMessage, email_id)
    if msg is None:
        raise HTTPException(404, "Email not found")
    try:
        email_gateway.approve_test_send(db, msg, actor=user.email)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    return S.email(msg)


@router.get("/users")
def users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [_user(u) for u in db.scalars(select(User).order_by(User.email))]
