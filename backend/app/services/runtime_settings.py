"""Runtime switches stored in the database (Settings → Integrations), e.g. the outbound-email kill switch.

Reads never raise: if the table is unavailable the safe default is returned (outbound OFF),
so a database hiccup can never cause email to be sent when it was meant to be held.
"""
from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import AppSetting
from app.services import audit

log = logging.getLogger("docshealth.runtime_settings")

OUTBOUND_KEY = "outbound_email"
TEST_MODE_KEY = "outbound_test_mode"


def outbound_enabled(db: Session) -> bool:
    try:
        row = db.get(AppSetting, OUTBOUND_KEY)
    except SQLAlchemyError:
        log.exception("could not read outbound switch; treating outbound email as OFF")
        return False
    return bool(row and row.value.get("enabled"))  # no row yet = off until an admin turns it on


def outbound_status(db: Session) -> dict:
    row = db.get(AppSetting, OUTBOUND_KEY)
    return {"enabled": bool(row and row.value.get("enabled")), "updated_by": row.updated_by if row else None,
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
            "reason": row.value.get("reason") if row else None}


def set_outbound(db: Session, enabled: bool, *, actor: str, reason: str | None = None) -> dict:
    row = db.get(AppSetting, OUTBOUND_KEY) or AppSetting(key=OUTBOUND_KEY, value={})
    row.value = {"enabled": bool(enabled), "reason": reason}
    row.updated_by = actor
    db.add(row)
    audit.record(db, "system", None, "outbound_email_enabled" if enabled else "outbound_email_disabled", actor,
                 reason=reason)
    db.flush()
    return outbound_status(db)


def test_mode(db: Session) -> dict | None:
    """Outbound test mode: every reply waits for approval and is delivered ONLY to `recipient`.
    Returns None when off. If the setting can't be read we report it as off; outbound then falls back
    to the kill switch, whose own safe default is OFF (held), so nothing reaches a patient either way."""
    try:
        row = db.get(AppSetting, TEST_MODE_KEY)
    except SQLAlchemyError:
        log.exception("could not read outbound test mode")
        return None
    if not row or not row.value.get("enabled") or not row.value.get("recipient"):
        return None
    return {"enabled": True, "recipient": row.value["recipient"], "updated_by": row.updated_by,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def set_test_mode(db: Session, enabled: bool, recipient: str | None, *, actor: str) -> dict | None:
    row = db.get(AppSetting, TEST_MODE_KEY) or AppSetting(key=TEST_MODE_KEY, value={})
    row.value = {"enabled": bool(enabled), "recipient": (recipient or "").strip().lower() or None}
    row.updated_by = actor
    db.add(row)
    audit.record(db, "system", None, "outbound_test_mode_on" if enabled else "outbound_test_mode_off", actor,
                 recipient=row.value["recipient"])
    db.flush()
    return test_mode(db)
