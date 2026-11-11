"""Audit trail: every automated or human action on a record is written here."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    db: Session,
    entity_type: str,
    entity_id: int | None,
    action: str,
    actor: str = "system",
    **details,
) -> AuditLog:
    entry = AuditLog(entity_type=entity_type, entity_id=entity_id, action=action, actor=actor, details=details)
    db.add(entry)
    return entry
