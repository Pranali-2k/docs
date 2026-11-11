"""A doctor's own time: keep it free of appointments, give it back, or say so in plain English."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Provider, ProviderAvailability, ProviderBlock, User
from app.security import current_user
from app.services import availability_chat, blocks
from app.services import scheduling
from app.services.scheduling import SchedulingError, clinic_now, fmt_slot

router = APIRouter(prefix="/api", tags=["availability"])


def _out(b: ProviderBlock) -> dict:
    return {"id": b.id, "provider_id": b.provider_id, "provider_name": b.provider.full_name if b.provider else None,
            "start_at": b.start_at.isoformat(), "end_at": b.end_at.isoformat(), "reason": b.reason,
            "created_by": b.created_by}


def _provider(db: Session, provider_id: int) -> Provider:
    p = db.get(Provider, provider_id)
    if p is None:
        raise HTTPException(404, "Provider not found")
    return p


@router.get("/provider-blocks")
def list_blocks(start: date | None = None, days: int = 7, provider_id: int | None = None,
                db: Session = Depends(get_db), _: User = Depends(current_user)):
    first = datetime.combine(start or clinic_now().date(), time.min)
    return [_out(b) for b in blocks.blocks_between(db, first, first + timedelta(days=min(max(days, 1), 92)), provider_id)]


@router.get("/providers/{provider_id}/day-grid")
def day_grid(provider_id: int, day: date, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Every slot of the doctor's day with what it is: open, busy (kept free by the doctor), booked, or past.
    The doctor's own picker: click an open time to be busy then, click a busy time to open it again."""
    provider = _provider(db, provider_id)
    now = clinic_now()
    templates = db.scalars(select(ProviderAvailability).where(
        ProviderAvailability.provider_id == provider.id, ProviderAvailability.weekday == day.weekday()
    ).order_by(ProviderAvailability.start_time)).all()
    booked = scheduling._booked(db, provider.id, day)
    kept = scheduling.blocks_on(db, provider.id, day)
    rows = []
    for t in templates:
        cursor, end, step = datetime.combine(day, t.start_time), datetime.combine(day, t.end_time), timedelta(minutes=t.slot_minutes)
        while cursor + step <= end:
            stop = cursor + step
            appt = next((a for a in booked if a.start_at < stop and cursor < a.end_at), None)
            block = next((b for b in kept if b.start_at < stop and cursor < b.end_at), None)
            state = "booked" if appt else "busy" if block else "past" if cursor <= now else "open"
            rows.append({"start_at": cursor.isoformat(), "end_at": stop.isoformat(), "state": state,
                         "label": cursor.strftime("%I:%M %p").lstrip("0"),
                         "patient": appt.patient.full_name if appt and appt.patient else None,
                         "reason": block.reason if block else None})
            cursor = stop
    return rows


class RangeIn(BaseModel):
    provider_id: int
    start_at: datetime
    end_at: datetime


@router.post("/provider-blocks/free")
def free_range(body: RangeIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Give one stretch of time back (a click on a busy slot), splitting a longer block if needed."""
    provider = _provider(db, body.provider_id)
    changed = blocks.remove_range(db, provider, body.start_at.replace(tzinfo=None), body.end_at.replace(tzinfo=None),
                                  actor=user.email)
    db.commit()
    return {"changed": changed}


class BlockIn(BaseModel):
    provider_id: int
    start_at: datetime
    end_at: datetime
    reason: str | None = Field(default=None, max_length=200)


@router.post("/provider-blocks", status_code=201)
def create_block(body: BlockIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    provider = _provider(db, body.provider_id)
    try:
        block = blocks.add_block(db, provider, body.start_at.replace(tzinfo=None), body.end_at.replace(tzinfo=None),
                                 reason=body.reason, actor=user.email)
    except SchedulingError as exc:
        raise HTTPException(422, str(exc)) from None
    disturbed = blocks.booked_inside(db, provider.id, block.start_at, block.end_at)
    db.commit()
    return {**_out(block), "already_booked": [{"id": a.id, "patient": a.patient.full_name, "start_at": a.start_at.isoformat()}
                                              for a in disturbed]}


@router.delete("/provider-blocks/{block_id}")
def delete_block(block_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    block = db.get(ProviderBlock, block_id)
    if block is None:
        raise HTTPException(404, "Block not found")
    blocks.remove_range(db, block.provider, block.start_at, block.end_at, actor=user.email)
    db.commit()
    return {"removed": block_id}


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=600)
    provider_id: int | None = None


def _span(start: datetime, end: datetime) -> str:
    same_day = start.date() == end.date()
    return (f"{start:%a %b %d}, {start:%I:%M %p} to {end:%I:%M %p}" if same_day
            else f"{start:%a %b %d %I:%M %p} to {end:%a %b %d %I:%M %p}").replace(" 0", " ")


@router.post("/availability/chat")
def availability_chat_message(body: ChatIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """The doctor says when they are busy or free; the schedule changes and the reply says exactly what changed."""
    providers = [(p.id, p.full_name) for p in db.scalars(select(Provider).where(Provider.is_active))]
    today = clinic_now().date()
    cmd = availability_chat.parse(body.message, providers, body.provider_id, today)
    if cmd.problems or cmd.action == "unknown":
        return {"reply": " ".join(cmd.problems) or "I did not understand that.", "changed": False, "blocks": [],
                "examples": ["Dr. Rao is busy tomorrow 2-4 pm", "I'm in surgery Monday all day",
                             "Free up Dr. Torres on Friday afternoon", "Show what is blocked this week"]}
    provider = _provider(db, cmd.provider_id)

    if cmd.action == "list":
        first = datetime.combine(today, time.min)
        rows = blocks.blocks_between(db, first, first + timedelta(days=30), provider.id)
        if cmd.ranges:
            rows = [b for b in rows if any(b.start_at < e and b.end_at > s for s, e in cmd.ranges)]
        reply = (f"{provider.full_name} has these times kept free:\n" + "\n".join(f"• {_span(b.start_at, b.end_at)}"
                 + (f" ({b.reason})" if b.reason else "") for b in rows)) if rows else \
            f"{provider.full_name} has no time kept free in the next 30 days."
        return {"reply": reply, "changed": False, "provider_id": provider.id, "blocks": [_out(b) for b in rows]}

    lines, disturbed_all = [], []
    try:
        for start, end in cmd.ranges:
            if cmd.action == "block":
                block = blocks.add_block(db, provider, start, end, reason=cmd.reason, actor=user.email)
                lines.append(f"• Kept free: {_span(block.start_at, block.end_at)}" + (f" ({block.reason})" if block.reason else ""))
                disturbed_all += blocks.booked_inside(db, provider.id, block.start_at, block.end_at)
            else:
                n = blocks.remove_range(db, provider, start, end, actor=user.email)
                lines.append(f"• {'Given back' if n else 'Nothing was blocked'}: {_span(start, end)}")
    except SchedulingError as exc:
        db.rollback()
        return {"reply": f"I could not do that: {exc}", "changed": False, "blocks": []}
    db.commit()
    if cmd.action == "block":
        reply = f"Done. No appointments will be booked or offered for {provider.full_name} in:\n" + "\n".join(lines)
    else:
        reply = f"Done for {provider.full_name}:\n" + "\n".join(lines)
    if disturbed_all:
        reply += (f"\n\nHeads up: {len(disturbed_all)} patient(s) are already booked in that time, so staff may need to move them: "
                  + "; ".join(f"{a.patient.full_name} ({fmt_slot(a.start_at)})" for a in disturbed_all[:5]) + ".")
    now_blocks = blocks.blocks_between(db, datetime.combine(today, time.min), datetime.combine(today, time.min) + timedelta(days=30), provider.id)
    return {"reply": reply, "changed": True, "provider_id": provider.id, "blocks": [_out(b) for b in now_blocks],
            "already_booked": [{"id": a.id, "patient": a.patient.full_name, "start_at": a.start_at.isoformat()} for a in disturbed_all]}
