"""Install a new FIELD_ENCRYPTION_KEY and re-encrypt every encrypted column with it.

    python -m scripts.rotate_field_key            # generate a new key and rotate
    python -m scripts.rotate_field_key --check    # only verify every stored value decrypts

Crash-safe order:
  1. write the new key to backend/.env as FIELD_ENCRYPTION_KEY and keep the old key(s) in
     FIELD_ENCRYPTION_KEYS_PREVIOUS (backup at .env.bak), so both keys decrypt from now on;
  2. re-encrypt all values in ONE database transaction and verify each one;
  3. only then is the old key optional (it is kept in PREVIOUS; remove it later if you like).
If step 2 fails, nothing in the database changed and both keys still work.
Restart the API afterwards. Keys are never printed.
"""
from __future__ import annotations

import argparse
import sys

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import IntegrationConnection, OAuthState, Patient
from app.services import crypto
from app.services.envfile import update_env

COLUMNS = [
    (Patient, ["ssn_encrypted", "drivers_license_encrypted"]),
    (IntegrationConnection, ["client_secret_encrypted", "refresh_token_encrypted"]),
    (OAuthState, ["code_verifier_encrypted"]),
]


def check() -> tuple[int, list[str]]:
    total, bad = 0, []
    with SessionLocal() as db:
        for model, cols in COLUMNS:
            for row in db.scalars(select(model)):
                for col in cols:
                    value = getattr(row, col)
                    if value:
                        total += 1
                        try:
                            crypto.decrypt(value)
                        except crypto.CryptoError:
                            bad.append(f"{model.__tablename__}.{col} (row {getattr(row, 'id', '?')})")
    return total, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    total, bad = check()
    print(f"Encrypted values found: {total}; unreadable: {len(bad)}")
    if bad:
        print("Cannot proceed; these values don't decrypt with the configured keys:\n  " + "\n  ".join(bad))
        return 2
    if args.check:
        return 0

    s = get_settings()
    current = s.field_encryption_key.strip() or crypto.derived_dev_key(s.jwt_secret)
    previous = [k for k in [current, *s.field_encryption_keys_previous.split(",")] if k.strip()]
    new_key = Fernet.generate_key().decode()
    try:
        update_env({"FIELD_ENCRYPTION_KEY": new_key, "FIELD_ENCRYPTION_KEYS_PREVIOUS": ",".join(dict.fromkeys(previous))})
    except (OSError, ValueError) as exc:
        print(f"Could not write backend/.env, nothing changed: {exc}")
        return 3
    get_settings.cache_clear()
    crypto.reset_cache()
    print("New key saved to backend/.env (old key kept in FIELD_ENCRYPTION_KEYS_PREVIOUS; backup .env.bak)")

    rotated = 0
    with SessionLocal() as db:
        try:
            for model, cols in COLUMNS:
                for row in db.scalars(select(model)):
                    for col in cols:
                        value = getattr(row, col)
                        if value:
                            plain = crypto.decrypt(value)
                            new_value = crypto.rotate(value)
                            assert Fernet(new_key.encode()).decrypt(new_value.encode()).decode() == plain
                            setattr(row, col, new_value)
                            rotated += 1
            db.commit()
        except Exception as exc:  # roll back; both keys remain valid, so nothing is lost
            db.rollback()
            print(f"Re-encryption failed and was rolled back (both keys still work): {exc}")
            return 1
    print(f"Re-encrypted {rotated} value(s) with the new key. Restart the API.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
