"""Field-level encryption for sensitive values (SSN, driver's license, integration secrets/tokens).

Fernet (AES-128-CBC + HMAC-SHA256) through MultiFernet:
  * FIELD_ENCRYPTION_KEY            current key; all new values are encrypted with it
  * FIELD_ENCRYPTION_KEYS_PREVIOUS  comma-separated older keys, still accepted for decryption,
                                    so a key rotation can never leave data unreadable
Rotate with `python -m scripts.rotate_field_key`. In development only, when no key is set,
a key is derived from JWT_SECRET (and production refuses to start without a real key).
"""
from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings

log = logging.getLogger("docshealth.crypto")


class CryptoError(RuntimeError):
    pass


def derived_dev_key(jwt_secret: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(("docshealth-field-key:" + jwt_secret).encode()).digest()).decode()


def _load(key: str) -> Fernet:
    try:
        return Fernet(key.strip().encode())
    except (ValueError, TypeError) as exc:
        raise CryptoError("an encryption key is not a valid Fernet key") from exc


@lru_cache
def _fernet() -> MultiFernet:
    s = get_settings()
    keys: list[Fernet] = []
    if s.field_encryption_key.strip():
        keys.append(_load(s.field_encryption_key))
    else:
        if s.environment.lower() not in {"dev", "test", "development"}:
            raise CryptoError("FIELD_ENCRYPTION_KEY must be set outside development")
        log.warning("FIELD_ENCRYPTION_KEY not set; deriving a development key from JWT_SECRET")
        keys.append(_load(derived_dev_key(s.jwt_secret)))
    for old in (k for k in s.field_encryption_keys_previous.split(",") if k.strip()):
        keys.append(_load(old))
    return MultiFernet(keys)


def reset_cache() -> None:
    _fernet.cache_clear()


def encrypt(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    try:
        return _fernet().encrypt(value.encode()).decode()
    except CryptoError:
        raise
    except Exception as exc:
        raise CryptoError(f"encryption failed: {exc}") from exc


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError("stored value cannot be decrypted with the current or previous keys") from exc


def rotate(token: str | None) -> str | None:
    """Re-encrypt a stored value with the current key (accepts any configured key)."""
    if not token:
        return token
    try:
        return _fernet().rotate(token.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError("value cannot be decrypted with any configured key") from exc


def last4(value: str | None) -> str | None:
    digits = "".join(ch for ch in (value or "") if ch.isalnum())
    return digits[-4:] if digits else None
