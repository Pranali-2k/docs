"""Staff authentication: PBKDF2 password hashes and JWT bearer tokens."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

_ITERATIONS = 390_000
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(candidate, digest)


def create_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "iat": int(now.timestamp()),
        "tv": user.token_version or 0,
        "exp": now + timedelta(minutes=s.jwt_expire_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def _gateway_user(request: Request, db: Session) -> User | None:
    """The staff account for a request the Healthcare Ecosystem gateway has already authenticated.

    The ecosystem signs the person in and checks their practice and subscription; it proves that
    with a shared secret. An ecosystem administrator acts as the DOCS admin, everyone else as the
    scheduler. Without a configured secret this does nothing.
    """
    s = get_settings()
    sent = request.headers.get("x-gateway-secret", "")
    if not s.gateway_secret or not sent or not hmac.compare_digest(sent, s.gateway_secret):
        return None
    if request.headers.get("x-gateway-tenant") != s.gateway_tenant:
        return None
    role = "admin" if request.headers.get("x-gateway-user-type") == "admin" else "scheduler"
    return db.scalar(select(User).where(User.role == role, User.is_active.is_(True)).order_by(User.id))


def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        gateway = _gateway_user(request, db)
        if gateway is not None:
            return gateway
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from None
    try:
        user = db.get(User, int(payload["sub"]))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User disabled")
    if payload.get("tv", 0) != (user.token_version or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session ended after a password change. Sign in again.")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user
