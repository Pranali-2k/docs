"""Schedule module: providers, payers, slots and appointments."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.db import get_db
from app.models import Appointment, Patient, Payer, Provider, User
from app.security import current_user
from app.services import scheduling

router = APIRouter(prefix="/api", tags=["scheduling"])


@router.get("/providers")
def providers(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [S.provider(p) for p in db.scalars(select(Provider).order_by(Provider.full_name))]


@router.get("/payers")
def payers(q: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Payer list / 'Payer Look-up': matches name, alias or electronic payer ID."""
    rows = db.scalars(select(Payer).order_by(Payer.name)).all()
    if q:
        needle = q.strip().lower()
        rows = [p for p in rows if needle in p.name.lower() or needle in (p.electronic_payer_id or "").lower()
                or any(needle in a.lower() for a in (p.aliases or []))]
    return [S.payer(p) for p in rows]


@router.get("/payers/{payer_id}/plans")
def payer_plans(payer_id: int, q: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    payer = db.get(Payer, payer_id)
    if payer is None:
        raise HTTPException(404, "Payer not found")
    plans = sorted(payer.plans, key=lambda p: p.name)
    if q:
        plans = [p for p in plans if q.strip().lower() in p.name.lower()]
    return [{"id": p.id, "name": p.name, "plan_type": p.plan_type} for p in plans]


@router.get("/providers/{provider_id}/slots")
def slots(provider_id: int, day: date, db: Session = Depends(get_db), _: User = Depends(current_user)):
    if db.get(Provider, provider_id) is None:
        raise HTTPException(404, "Provider not found")
    return [{"start_at": s.isoformat(), "label": s.strftime("%I:%M %p").lstrip("0")}
            for s in scheduling.available_slots(db, provider_id, day)]


@router.get("/appointments")
def list_appointments(
    start: date | None = None,
    days: int = 1,
    provider_id: int | None = None,
    include_cancelled: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    start = start or scheduling.clinic_now().date()
    lo = datetime.combine(start, datetime.min.time())
    stmt = select(Appointment).where(Appointment.start_at >= lo, Appointment.start_at < lo + timedelta(days=max(1, min(days, 31))))
    if provider_id:
        stmt = stmt.where(Appointment.provider_id == provider_id)
    if not include_cancelled:
        stmt = stmt.where(Appointment.status != "cancelled")
    return [S.appointment(a) for a in db.scalars(stmt.order_by(Appointment.start_at))]


class AppointmentIn(BaseModel):
    patient_id: int
    provider_id: int
    start_at: datetime
    visit_reason: str | None = None


@router.post("/appointments", status_code=201)
def create_appointment(body: AppointmentIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    patient, provider = db.get(Patient, body.patient_id), db.get(Provider, body.provider_id)
    if patient is None or provider is None:
        raise HTTPException(404, "Patient or provider not found")
    try:
        appt = scheduling.book(db, patient, provider, body.start_at.replace(tzinfo=None), reason=body.visit_reason,
                               actor=user.email)
    except scheduling.SchedulingError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return S.appointment(appt)


@router.post("/appointments/{appointment_id}/cancel")
def cancel_appointment(appointment_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise HTTPException(404, "Appointment not found")
    try:
        scheduling.cancel(db, appt, actor=user.email)
    except scheduling.SchedulingError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return S.appointment(appt)
