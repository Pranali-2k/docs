"""Slot search, booking and cancellation. Appointment times are clinic-local and naive."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Appointment, Patient, Provider, ProviderAvailability, ProviderBlock
from app.services import audit


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
    template = db.scalar(
        select(ProviderAvailability).where(
            ProviderAvailability.provider_id == provider.id, ProviderAvailability.weekday == start.weekday()
        )
    )
    minutes = duration_minutes or (template.slot_minutes if template else 30)
    end = start + timedelta(minutes=minutes)
    overlap = and_(Appointment.status == "booked", Appointment.start_at < end, Appointment.end_at > start)
    kept = db.scalar(select(ProviderBlock).where(
        ProviderBlock.provider_id == provider.id, ProviderBlock.start_at < end, ProviderBlock.end_at > start))
    if kept is not None:
        raise SchedulingError(f"{provider.full_name} is unavailable at {start:%Y-%m-%d %H:%M}"
                              + (f" ({kept.reason})" if kept.reason else ""))
    if db.scalar(select(Appointment.id).where(Appointment.provider_id == provider.id, overlap)):
        raise SchedulingError(f"{provider.full_name} is already booked at {start:%Y-%m-%d %H:%M}")
    if db.scalar(select(Appointment.id).where(Appointment.patient_id == patient.id, overlap)):
        raise SchedulingError("Patient already has an appointment at that time")
    appt = Appointment(
        patient_id=patient.id,
        provider_id=provider.id,
        start_at=start,
        end_at=end,
        visit_reason=reason,
        source_request_id=request_id,
        booked_by=actor,
    )
    db.add(appt)
    db.flush()
    audit.record(
        db, "appointment", appt.id, "appointment_booked", actor,
        patient_id=patient.id, provider_id=provider.id, start=start.isoformat(), request_id=request_id,
    )
    audit.record(db, "patient", patient.id, "chart_updated", actor, change="appointment_booked", appointment_id=appt.id)
    return appt


def cancel(db: Session, appt: Appointment, *, actor: str = "system", request_id: int | None = None) -> Appointment:
    if appt.status != "booked":
        raise SchedulingError(f"Appointment is {appt.status}, not booked")
    appt.status = "cancelled"
    appt.cancelled_at = datetime.now(timezone.utc)
    audit.record(
        db, "appointment", appt.id, "appointment_cancelled", actor,
        patient_id=appt.patient_id, start=appt.start_at.isoformat(), request_id=request_id,
    )
    audit.record(db, "patient", appt.patient_id, "chart_updated", actor, change="appointment_cancelled", appointment_id=appt.id)
    return appt


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
