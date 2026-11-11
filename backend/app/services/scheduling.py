"""Slot search, booking and cancellation. Appointment times are clinic-local and naive."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Appointment, Patient, Provider, ProviderAvailability, ProviderBlock
from app.services import db_procs


class SchedulingError(ValueError):
    pass


def clinic_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(get_settings().clinic_timezone)).replace(tzinfo=None)


def _booked(db: Session, provider_id: int, day: date) -> list[Appointment]:
    start = datetime.combine(day, datetime.min.time())
    return list(
        db.scalars(
            select(Appointment).where(
                Appointment.provider_id == provider_id,
                Appointment.status == "booked",
                Appointment.start_at >= start,
                Appointment.start_at < start + timedelta(days=1),
            )
        )
    )


def blocks_on(db: Session, provider_id: int, day: date) -> list[ProviderBlock]:
    """Time the provider has kept free of appointments on this clinic-local day, earliest first."""
    start = datetime.combine(day, datetime.min.time())
    return list(db.scalars(
        select(ProviderBlock).where(
            ProviderBlock.provider_id == provider_id,
            ProviderBlock.start_at < start + timedelta(days=1),
            ProviderBlock.end_at > start,
        ).order_by(ProviderBlock.start_at)
    ))


def available_slots(db: Session, provider_id: int, day: date, *, now: datetime | None = None) -> list[datetime]:
    now = now or clinic_now()
    templates = db.scalars(
        select(ProviderAvailability).where(
            ProviderAvailability.provider_id == provider_id, ProviderAvailability.weekday == day.weekday()
        )
    ).all()
    booked = _booked(db, provider_id, day)
    blocks = blocks_on(db, provider_id, day)
    slots: list[datetime] = []
    for t in templates:
        cursor = datetime.combine(day, t.start_time)
        end = datetime.combine(day, t.end_time)
        step = timedelta(minutes=t.slot_minutes)
        while cursor + step <= end:
            clash = any(a.start_at < cursor + step and cursor < a.end_at for a in booked + blocks)  # booked, or kept free
            if not clash and cursor > now + timedelta(hours=2):  # min lead time
                slots.append(cursor)
            cursor += step
    return sorted(slots)


def _fits(slot: datetime, time_of_day: str) -> bool:
    if time_of_day == "morning":
        return slot.hour < 12
    if time_of_day == "afternoon":
        return slot.hour >= 12
    return True


def find_slots(
    db: Session,
    provider_ids: list[int],
    *,
    preferred_dates: list[date] | None = None,
    time_of_day: str = "any",
    limit: int = 3,
    search_days: int = 21,
    now: datetime | None = None,
    exact_time: time | None = None,
) -> tuple[list[tuple[int, datetime]], bool]:
    """Return (slots, matched_preference). Tries preferred dates first, then the next weeks.
    With `exact_time` (e.g. "at 10 am"), a free slot starting exactly then on a preferred date comes first."""
    now = now or clinic_now()
    results: list[tuple[int, datetime]] = []
    if exact_time and preferred_dates:
        for d in sorted(preferred_dates):
            for pid in provider_ids:
                if any(s.time() == exact_time for s in available_slots(db, pid, d, now=now)):
                    results.append((pid, datetime.combine(d, exact_time)))
                    break
            if results:
                break

    def scan(days: list[date], respect_tod: bool) -> None:
        for d in days:
            for pid in provider_ids:
                for s in available_slots(db, pid, d, now=now):
                    if (not respect_tod or _fits(s, time_of_day)) and (pid, s) not in results:
                        results.append((pid, s))
                        if len(results) >= limit:
                            return

    exact_first = results[:1]
    if preferred_dates:
        scan(sorted(preferred_dates), True)
        if results:
            rest = sorted((r for r in results if r not in exact_first), key=lambda r: r[1])
            return (exact_first + rest)[:limit], True
    # Nothing free on the days asked for: the nearest openings after them (else after today).
    first_day = max(now.date(), min(preferred_dates)) if preferred_dates else now.date()
    horizon = [first_day + timedelta(days=i) for i in range(search_days)]
    scan(horizon, True)
    if not results and first_day != now.date():
        horizon = [now.date() + timedelta(days=i) for i in range(search_days)]
        scan(horizon, True)
    if not results:
        scan(horizon, False)
    return sorted(results, key=lambda r: r[1])[:limit], False


def book(
    db: Session,
    patient: Patient,
    provider: Provider,
    start: datetime,
    *,
    reason: str | None = None,
    request_id: int | None = None,
    actor: str = "system",
    duration_minutes: int | None = None,
) -> Appointment:
    """Book a visit. The rules (provider blocks, double booking, slot length) and the audit rows live in the
    book_appointment() procedure, so they hold for every caller, including direct SQL."""
    try:
        appt_id = db_procs.scalar(
            db, "SELECT book_appointment(:patient, :provider, :start, :reason, :request, :actor, :minutes)",
            {"patient": patient.id, "provider": provider.id, "start": start, "reason": reason, "request": request_id,
             "actor": actor, "minutes": duration_minutes})
    except db_procs.ProcedureError as exc:
        raise SchedulingError(str(exc)) from None
    return db.get(Appointment, appt_id)


def cancel(db: Session, appt: Appointment, *, actor: str = "system", request_id: int | None = None) -> Appointment:
    try:
        db_procs.scalar(db, "SELECT cancel_appointment(:appt, :actor, :request)",
                        {"appt": appt.id, "actor": actor, "request": request_id})
    except db_procs.ProcedureError as exc:
        raise SchedulingError(str(exc)) from None
    db.refresh(appt)
    return appt


def reschedule(
    db: Session,
    old: Appointment,
    provider: Provider,
    start: datetime,
    *,
    reason: str | None = None,
    request_id: int | None = None,
    actor: str = "system",
) -> Appointment:
    """Move a visit in one transaction: if the new time cannot be booked, the original visit is left untouched."""
    try:
        appt_id = db_procs.scalar(
            db, "SELECT reschedule_appointment(:old, :provider, :start, :reason, :request, :actor)",
            {"old": old.id, "provider": provider.id, "start": start, "reason": reason, "request": request_id,
             "actor": actor})
    except db_procs.ProcedureError as exc:
        raise SchedulingError(str(exc)) from None
    db.refresh(old)
    return db.get(Appointment, appt_id)


def upcoming_for_patient(db: Session, patient_id: int, *, now: datetime | None = None) -> list[Appointment]:
    now = now or clinic_now()
    return list(
        db.scalars(
            select(Appointment)
            .where(Appointment.patient_id == patient_id, Appointment.status == "booked", Appointment.start_at >= now)
            .order_by(Appointment.start_at)
        )
    )


def fmt_slot(dt: datetime) -> str:
    return dt.strftime("%A, %B %d, %Y at %I:%M %p").replace(" 0", " ")
