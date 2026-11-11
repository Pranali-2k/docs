"""Chart add-on tables: alerts, allergies, notes, reminders, flags, tasks, linked accounts, medications,
drug-interaction rules, non-visit orders, procedure logs, consents and internal mail (intramail).

Each patient-owned table cascades from `patients`. Integrity that must hold even when someone writes to the
database directly (symmetric links, completion timestamps, valid status moves) is also enforced by the
PostgreSQL triggers in migrations/0001_chart_addons.sql.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text as sa_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class _Stamped:
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow,
                                                 server_default=func.now())


def _patient_fk() -> Mapped[int]:
    return mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)


class PatientAlert(_Stamped, Base):
    __tablename__ = "patient_alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = _patient_fk()
    text: Mapped[str] = mapped_column(String(500))
    severity: Mapped[str] = mapped_column(String(10), default="info", server_default="info")  # info | warning | critical
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_text("true"))
    __table_args__ = (CheckConstraint("severity in ('info','warning','critical')", name="ck_alert_severity"),)


class PatientAllergy(_Stamped, Base):
    __tablename__ = "patient_allergies"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = _patient_fk()
    allergen: Mapped[str] = mapped_column(String(200))
    reaction: Mapped[str | None] = mapped_column(String(300))
    severity: Mapped[str | None] = mapped_column(String(10))  # mild | moderate | severe
    status: Mapped[str] = mapped_column(String(10), default="active", server_default="active")  # active | inactive
    noted_on: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (
        CheckConstraint("status in ('active','inactive')", name="ck_allergy_status"),
        CheckConstraint("severity is null or severity in ('mild','moderate','severe')", name="ck_allergy_severity"),
    )


class PatientNote(_Stamped, Base):
    """Sticky notes (shown on the chart header) and general or clinical notes."""

    __tablename__ = "patient_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = _patient_fk()
    kind: Mapped[str] = mapped_column(String(10), default="general", server_default="general")  # sticky | general | clinical
    body: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint("kind in ('sticky','general','clinical')", name="ck_note_kind"),)


class PatientReminder(_Stamped, Base):
    __tablename__ = "patient_reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = _patient_fk()
    message: Mapped[str] = mapped_column(String(500))
    due_on: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open")  # open | done | cancelled
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("status in ('open','done','cancelled')", name="ck_reminder_status"),)


class PatientFlag(_Stamped, Base):
    __tablename__ = "patient_flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = _patient_fk()
    flag_type: Mapped[str] = mapped_column(String(60))
    note: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_text("true"))


class Task(_Stamped, Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    priority: Mapped[str] = mapped_column(String(10), default="normal", server_default="normal")  # low | normal | high
    due_on: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(12), default="open", server_default="open")  # open | in_progress | done | cancelled
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status in ('open','in_progress','done','cancelled')", name="ck_task_status"),
        CheckConstraint("priority in ('low','normal','high')", name="ck_task_priority"),
    )


class PatientLink(Base):
    """Linked accounts (family members, same household). Stored as two rows, one per direction."""

    __tablename__ = "patient_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = _patient_fk()
    linked_patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    relationship_label: Mapped[str | None] = mapped_column(String(50))
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("patient_id", "linked_patient_id", name="uq_patient_link"),
        CheckConstraint("patient_id <> linked_patient_id", name="ck_link_not_self"),
    )


class Medication(_Stamped, Base):
    """Patient medication list (Patient Rx). A record of what was prescribed; it does not e-prescribe."""

    __tablename__ = "medications"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = _patient_fk()
    drug_name: Mapped[str] = mapped_column(String(200), index=True)
    strength: Mapped[str | None] = mapped_column(String(60))
    route: Mapped[str | None] = mapped_column(String(40))
    frequency: Mapped[str | None] = mapped_column(String(100))
    quantity: Mapped[int | None] = mapped_column(Integer)
    refills: Mapped[int | None] = mapped_column(Integer)
    prescriber_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), default="active", server_default="active")  # active | stopped | completed
    start_on: Mapped[date | None] = mapped_column(Date)
    stop_on: Mapped[date | None] = mapped_column(Date)
    instructions: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("status in ('active','stopped','completed')", name="ck_med_status"),
        CheckConstraint("quantity is null or quantity >= 0", name="ck_med_quantity"),
        CheckConstraint("refills is null or refills >= 0", name="ck_med_refills"),
    )


class DrugInteractionRule(_Stamped, Base):
    """A practice-maintained interaction rule. No clinical data ships with the app: an administrator enters
    the rules (or a licensed drug database feeds this table). Pairs are stored once, lower-cased, sorted."""

    __tablename__ = "drug_interaction_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    drug_a: Mapped[str] = mapped_column(String(200), index=True)
    drug_b: Mapped[str] = mapped_column(String(200), index=True)
    severity: Mapped[str] = mapped_column(String(10), default="moderate", server_default="moderate")  # minor | moderate | major
    message: Mapped[str] = mapped_column(String(500))
    source: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_text("true"))
    __table_args__ = (
        UniqueConstraint("drug_a", "drug_b", name="uq_drug_rule_pair"),
        CheckConstraint("severity in ('minor','moderate','major')", name="ck_rule_severity"),
    )


class ClinicalOrder(_Stamped, Base):
    """Non-visit orders (lab, imaging, referral, other)."""

    __tablename__ = "clinical_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = _patient_fk()
    order_type: Mapped[str] = mapped_column(String(10))  # lab | imaging | referral | other
    description: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(10), default="draft", server_default="draft")  # draft | ordered | completed | cancelled
    ordered_by_provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    ordered_on: Mapped[date | None] = mapped_column(Date)
    due_on: Mapped[date | None] = mapped_column(Date)
    completed_on: Mapped[date | None] = mapped_column(Date)
    result_note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("order_type in ('lab','imaging','referral','other')", name="ck_order_type"),
        CheckConstraint("status in ('draft','ordered','completed','cancelled')", name="ck_order_status"),
    )


class ProcedureLog(_Stamped, Base):
    """Procedures performed for a patient (CPT, optional ICD-10), optionally tied to an appointment."""

    __tablename__ = "procedure_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = _patient_fk()
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id", ondelete="SET NULL"))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    cpt_code: Mapped[str] = mapped_column(String(5))
    icd10_code: Mapped[str | None] = mapped_column(String(8))
    description: Mapped[str | None] = mapped_column(String(300))
    units: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    performed_on: Mapped[date] = mapped_column(Date)
    __table_args__ = (CheckConstraint("units >= 1", name="ck_proc_units"),)


class ConsentTemplate(_Stamped, Base):
    __tablename__ = "consent_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    body: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_text("true"))


class PatientConsent(_Stamped, Base):
    __tablename__ = "patient_consents"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = _patient_fk()
    template_id: Mapped[int] = mapped_column(ForeignKey("consent_templates.id", ondelete="RESTRICT"), index=True)
    template_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")  # pending | signed | revoked
    signed_on: Mapped[date | None] = mapped_column(Date)
    signed_by_name: Mapped[str | None] = mapped_column(String(200))
    expires_on: Mapped[date | None] = mapped_column(Date)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("status in ('pending','signed','revoked')", name="ck_consent_status"),)


class Intramail(Base):
    """Internal staff messages, optionally about a patient. Each side deletes its own copy."""

    __tablename__ = "intramail"
    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id", ondelete="SET NULL"), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_sender: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_text("false"))
    deleted_by_recipient: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_text("false"))
