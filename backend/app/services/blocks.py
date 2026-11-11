"""Keeping a provider's time free of appointments: add, give back, and what it disturbs."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appointment, Provider, ProviderBlock
from app.services import audit
from app.services.scheduling import SchedulingError, clinic_now


def booked_inside(db: Session, provider_id: int, start: datetime, end: datetime) -> list[Appointment]:
    """Appointments already booked that overlap [start, end): the patients a new block would disturb."""
    return list(db.scalars(
        select(Appointment).where(
            Appointment.provider_id == provider_id, Appointment.status == "booked",
            Appointment.start_at < end, Appointment.end_at > start,
        ).order_by(Appointment.start_at)
    ))


def add_block(db: Session, provider: Provider, start: datetime, end: datetime, *, reason: str | None = None,
              actor: str = "staff") -> ProviderBlock:
    """Keep [start, end) free. Overlapping or touching blocks are merged into one."""
    if end <= start:
        raise SchedulingError("The end must be after the start.")
    if end <= clinic_now():
        raise SchedulingError("That time has already passed.")
    touching = list(db.scalars(
        select(ProviderBlock).where(
            ProviderBlock.provider_id == provider.id, ProviderBlock.start_at <= end, ProviderBlock.end_at >= start
        ).order_by(ProviderBlock.start_at)
    ))
    for old in touching:
        start, end = min(start, old.start_at), max(end, old.end_at)
        reason = reason or old.reason
        db.delete(old)
    block = ProviderBlock(provider_id=provider.id, start_at=start, end_at=end, reason=(reason or None), created_by=actor)
    db.add(block)
    db.flush()
    audit.record(db, "provider", provider.id, "time_blocked", actor, start=start.isoformat(), end=end.isoformat(),
                 reason=reason, merged=len(touching))
    return block


def remove_range(db: Session, provider: Provider, start: datetime, end: datetime, *, actor: str = "staff") -> int:
    """Give [start, end) back: blocks inside it go, blocks that stick out are trimmed or split. Returns how many changed."""
    changed = 0
    for block in list(db.scalars(
        select(ProviderBlock).where(
            ProviderBlock.provider_id == provider.id, ProviderBlock.start_at < end, ProviderBlock.end_at > start
        ).order_by(ProviderBlock.start_at)
    )):
        before, after = block.start_at < start, block.end_at > end
        if before and after:  # the freed time is in the middle: split in two
            db.add(ProviderBlock(provider_id=provider.id, start_at=end, end_at=block.end_at, reason=block.reason,
                                 created_by=block.created_by))
            block.end_at = start
        elif before:
            block.end_at = start
        elif after:
            block.start_at = end
        else:
            db.delete(block)
        changed += 1
    if changed:
        db.flush()
        audit.record(db, "provider", provider.id, "time_unblocked", actor, start=start.isoformat(), end=end.isoformat())
    return changed


def blocks_between(db: Session, start: datetime, end: datetime, provider_id: int | None = None) -> list[ProviderBlock]:
    stmt = select(ProviderBlock).where(ProviderBlock.start_at < end, ProviderBlock.end_at > start)
    if provider_id:
        stmt = stmt.where(ProviderBlock.provider_id == provider_id)
    return list(db.scalars(stmt.order_by(ProviderBlock.start_at)))
