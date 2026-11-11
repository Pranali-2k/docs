"""Self-service password reset with email identity verification.

Flow:
  1. request_reset(email): if an active account exists, issue a single-use random
     token (only its SHA-256 hash is stored) and email a link to the address on file.
     The caller always gets the same generic answer, so the endpoint can't be used
     to find out which emails have accounts.
  2. verify(token): the portal checks the link before showing the form.
  3. complete_reset(token, new_password): enforce the password policy, set the new
     hash, burn the token (and every other open token for the user), and bump
     the user's token_version so all previously issued sessions stop working.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PasswordResetToken, User
from app.security import hash_password, verify_password
from app.services import audit, email_gateway

log = logging.getLogger("docshealth.auth")

MIN_LENGTH = 10
WINDOW = timedelta(minutes=15)


class ResetError(ValueError):
    """Invalid, expired or already-used link, or a password that fails policy."""


@dataclass
class ResetLink:
    user: User
    token: PasswordResetToken


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo


def mask_email(address: str) -> str:
    name, _, domain = address.partition("@")
    return f"{name[:1]}{'*' * max(1, len(name) - 2)}{name[-1:] if len(name) > 1 else ''}@{domain}"


def password_problems(password: str, user: User | None = None) -> list[str]:
    problems = []
    if len(password) < MIN_LENGTH:
        problems.append(f"at least {MIN_LENGTH} characters")
    if not re.search(r"[a-z]", password) or not re.search(r"[A-Z]", password):
        problems.append("upper- and lower-case letters")
    if not re.search(r"\d", password):
        problems.append("a number")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("a symbol")
    if user is not None and user.email.split("@")[0].lower() in password.lower():
        problems.append("not containing your email name")
    return problems


def request_reset(db: Session, email: str, *, ip: str | None = None) -> None:
    """Issue and email a reset link if the account exists. Silent otherwise."""
    s = get_settings()
    user = db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    if user is None or not user.is_active:
        log.info("password reset requested for unknown/inactive account")
        audit.record(db, "user", None, "password_reset_requested_unknown", "anonymous", ip=ip)
        return

    now = datetime.now(timezone.utc)
    recent = db.scalar(
        select(func.count()).select_from(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.created_at >= now - WINDOW)
    ) or 0
    if recent >= s.password_reset_max_per_window:
        audit.record(db, "user", user.id, "password_reset_throttled", "anonymous", ip=ip)
        return

    # Only the newest link works.
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id, token_hash=_hash(token), expires_at=now + timedelta(minutes=s.password_reset_ttl_minutes),
        requested_ip=ip,
    ))
    link = f"{s.portal_base_url.rstrip('/')}/reset-password?token={token}"
    body = (
        f"Hello {user.full_name},\n\n"
        f"We received a request to reset the password for your {s.clinic_name} scheduling portal account "
        f"({user.email}).\n\n"
        f"To verify it's you and choose a new password, open this link within {s.password_reset_ttl_minutes} minutes:\n\n"
        f"{link}\n\n"
        "The link works once. If you didn't ask for this, you can ignore this email and your password stays the same.\n\n"
        f"{s.clinic_name} Portal Security"
    )
    msg = email_gateway.send_email(
        db, to_address=user.email, subject=f"Reset your {s.clinic_name} portal password", body=body,
        actor="system", source="security",
    )
    audit.record(db, "user", user.id, "password_reset_requested", "anonymous", ip=ip,
                 email_id=msg.id, send_status=msg.send_status)


def _load(db: Session, token: str) -> ResetLink:
    if not token or len(token) > 200:
        raise ResetError("This reset link is invalid.")
    row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash(token)))
    if row is None:
        raise ResetError("This reset link is invalid.")
    if row.used_at is not None:
        raise ResetError("This reset link has already been used. Request a new one.")
    if _aware(row.expires_at) < datetime.now(timezone.utc):
        raise ResetError("This reset link has expired. Request a new one.")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise ResetError("This account is not active.")
    return ResetLink(user, row)


def verify(db: Session, token: str) -> User:
    return _load(db, token).user


def complete_reset(db: Session, token: str, new_password: str, *, ip: str | None = None) -> User:
    link = _load(db, token)
    user = link.user
    problems = password_problems(new_password, user)
    if problems:
        raise ResetError("Password needs " + ", ".join(problems) + ".")
    if verify_password(new_password, user.password_hash):
        raise ResetError("Choose a password different from your current one.")
    now = datetime.now(timezone.utc)
    user.password_hash = hash_password(new_password)
    user.password_changed_at = now
    user.token_version = (user.token_version or 0) + 1  # revokes every existing session
    user.failed_logins, user.first_failed_at, user.locked_until = 0, None, None  # unlock
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    audit.record(db, "user", user.id, "password_reset_completed", user.email, ip=ip)
    try:
        s = get_settings()
        email_gateway.send_email(
            db, to_address=user.email, subject=f"Your {s.clinic_name} portal password was changed",
            body=(f"Hello {user.full_name},\n\nThe password for {user.email} was just changed. If this wasn't you, "
                  f"contact your {s.clinic_name} administrator immediately.\n\n{s.clinic_name} Portal Security"),
            actor="system", source="security",
        )
    except Exception:  # the reset itself succeeded; a failed notice must not undo it
        log.exception("password-changed notice failed for user %s", user.id)
    return user
