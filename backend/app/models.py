"""ORM models covering the PRD domain.

Patient chart (demographics, insurance, documents), scheduling (providers,
availability, appointments), eligibility, email intake, portal requests and
the audit trail.

Timestamps (`created_at`, etc.) are timezone-aware UTC. Appointment start/end
are naive datetimes in clinic local time (see CLINIC_TIMEZONE).
"""
from __future__ import annotations

import enum
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    JSON,
    Numeric,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RequestStatus(str, enum.Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    AUTO_RESPONDED = "auto_responded"
    NEEDS_REVIEW = "needs_review"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    CLOSED = "closed"


class RequestIntent(str, enum.Enum):
    NEW_APPOINTMENT = "new_appointment"
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"
    INSURANCE_UPDATE = "insurance_update"
    GENERAL_QUESTION = "general_question"
    OTHER = "other"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(30), default="scheduler")  # admin | scheduler
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Embedded in every session token; bumped on password change/reset so older sessions stop working.
    token_version: Mapped[int | None] = mapped_column(Integer, default=0)
    failed_logins: Mapped[int | None] = mapped_column(Integer, default=0)
    first_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordResetToken(Base):
    """Single-use identity-verification link for a password reset. Only a SHA-256 hash
    of the token is stored, so a database leak cannot be used to reset passwords."""

    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_ip: Mapped[str | None] = mapped_column(String(64))


class Payer(Base):
    __tablename__ = "payers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    payer_code: Mapped[str] = mapped_column(String(40), unique=True)
    aliases: Mapped[list] = mapped_column(JsonType, default=list)  # names seen in emails
    electronic_payer_id: Mapped[str | None] = mapped_column(String(20), index=True)  # e.g. 94036
    financial_category: Mapped[str | None] = mapped_column(String(40))  # Commercial | Medicare | Medicaid ...
    claims_addresses: Mapped[list | None] = mapped_column(JsonType, default=list)

    plans: Mapped[list[PayerPlan]] = relationship(back_populates="payer", cascade="all, delete-orphan")


class PayerPlan(Base):
    """Named plans offered by a payer (drives the plan-name lookup on the insurance form)."""

    __tablename__ = "payer_plans"
    __table_args__ = (UniqueConstraint("payer_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    payer_id: Mapped[int] = mapped_column(ForeignKey("payers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    plan_type: Mapped[str] = mapped_column(String(20))  # PPO | HMO | EPO | POS | HDHP | Medicare | Medicaid

    payer: Mapped[Payer] = relationship(back_populates="plans")


class Provider(Base):
    __tablename__ = "providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    specialty: Mapped[str] = mapped_column(String(120))
    npi: Mapped[str] = mapped_column(String(20), unique=True)
    location: Mapped[str] = mapped_column(String(200), default="Main Clinic")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    networks: Mapped[list[ProviderNetwork]] = relationship(back_populates="provider", cascade="all, delete-orphan")
    availability: Mapped[list[ProviderAvailability]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )


class ProviderNetwork(Base):
    """Which payers a provider is in-network with."""

    __tablename__ = "provider_networks"
    __table_args__ = (UniqueConstraint("provider_id", "payer_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"))
    payer_id: Mapped[int] = mapped_column(ForeignKey("payers.id", ondelete="CASCADE"))
    in_network: Mapped[bool] = mapped_column(Boolean, default=True)

    provider: Mapped[Provider] = relationship(back_populates="networks")
    payer: Mapped[Payer] = relationship()


class ProviderAvailability(Base):
    """Weekly working-hours template; slots are generated from it."""

    __tablename__ = "provider_availability"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(Integer)  # 0=Mon .. 6=Sun
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=30)

    provider: Mapped[Provider] = relationship(back_populates="availability")


class ProviderBlock(Base):
    """Time a provider has kept for themselves (busy, in surgery, away): no appointment is offered or booked in it.

    Clinic-local naive times, like appointments, so a block and a slot compare directly.
    """

    __tablename__ = "provider_blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(String(120), default="staff")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    provider: Mapped[Provider] = relationship()


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True)
    mrn: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    prefix: Mapped[str | None] = mapped_column(String(20))
    first_name: Mapped[str] = mapped_column(String(100), index=True)
    middle_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100), index=True)
    suffix: Mapped[str | None] = mapped_column(String(20))
    preferred_name: Mapped[str | None] = mapped_column(String(100))
    previous_name: Mapped[str | None] = mapped_column(String(200))
    is_test_patient: Mapped[bool | None] = mapped_column(Boolean, default=False)
    marital_status: Mapped[str | None] = mapped_column(String(30))
    date_of_birth: Mapped[date | None] = mapped_column(Date, index=True)
    sex: Mapped[str | None] = mapped_column(String(20))
    # Sensitive identifiers: encrypted at rest (app.services.crypto); only the last 4 are kept in clear.
    ssn_encrypted: Mapped[str | None] = mapped_column(Text)
    ssn_last4: Mapped[str | None] = mapped_column(String(4))
    drivers_license_encrypted: Mapped[str | None] = mapped_column(Text)
    drivers_license_last4: Mapped[str | None] = mapped_column(String(4))
    drivers_license_state: Mapped[str | None] = mapped_column(String(2))
    phone: Mapped[str | None] = mapped_column(String(30), index=True)  # digits only (mobile / primary)
    home_phone: Mapped[str | None] = mapped_column(String(30))
    work_phone: Mapped[str | None] = mapped_column(String(30))
    preferred_contact_method: Mapped[str | None] = mapped_column(String(20))  # phone | email | text | mail
    email: Mapped[str | None] = mapped_column(String(255), index=True)  # lower-cased
    address_line1: Mapped[str | None] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(40))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    preferred_provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    policies: Mapped[list[InsurancePolicy]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    documents: Mapped[list[PatientDocument]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    appointments: Mapped[list[Appointment]] = relationship(back_populates="patient")
    guarantor: Mapped[Guarantor | None] = relationship(back_populates="patient", cascade="all, delete-orphan",
                                                       uselist=False)
    phi_releases: Mapped[list[PhiRelease]] = relationship(back_populates="patient", cascade="all, delete-orphan")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def display_name(self) -> str:
        parts = [self.first_name, f"({self.preferred_name})" if self.preferred_name else None,
                 self.middle_name, self.last_name, self.suffix]
        return " ".join(p for p in parts if p)

    @property
    def primary_policy(self) -> InsurancePolicy | None:
        active = [p for p in self.policies if p.is_active]
        for p in active:
            if p.is_primary:
                return p
        return active[0] if active else None


class InsurancePolicy(Base):
    __tablename__ = "insurance_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    payer_id: Mapped[int] = mapped_column(ForeignKey("payers.id"))
    member_id: Mapped[str] = mapped_column(String(60), index=True)
    group_number: Mapped[str | None] = mapped_column(String(60))
    subscriber_name: Mapped[str | None] = mapped_column(String(200))
    relationship_to_subscriber: Mapped[str] = mapped_column(String(30), default="self")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # coverage order: primary | secondary | tertiary  (is_primary is kept in sync for older code)
    coverage_order: Mapped[str | None] = mapped_column(String(10), default="primary")
    plan_name: Mapped[str | None] = mapped_column(String(200))
    policy_type: Mapped[str | None] = mapped_column(String(20))  # PPO | HMO | EPO | POS | HDHP | Medicare | Medicaid
    claims_address: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(String(500))
    # Authorization requirements configured for this plan
    requires_referral: Mapped[bool | None] = mapped_column(Boolean, default=False)
    requires_precert_inpatient: Mapped[bool | None] = mapped_column(Boolean, default=False)
    requires_preauth_outpatient: Mapped[bool | None] = mapped_column(Boolean, default=False)
    # Policy holder (when the patient is not the subscriber)
    holder_first_name: Mapped[str | None] = mapped_column(String(100))
    holder_last_name: Mapped[str | None] = mapped_column(String(100))
    holder_date_of_birth: Mapped[date | None] = mapped_column(Date)
    # Eligibility information on file (entered, or picked from an eligibility response)
    eligibility_status: Mapped[str | None] = mapped_column(String(20))  # active | inactive | unknown
    copay_amount: Mapped[float | None] = mapped_column(Numeric(10, 2))
    coinsurance_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    deductible: Mapped[float | None] = mapped_column(Numeric(10, 2))
    remaining_deductible: Mapped[float | None] = mapped_column(Numeric(10, 2))
    out_of_pocket: Mapped[float | None] = mapped_column(Numeric(10, 2))
    remaining_out_of_pocket: Mapped[float | None] = mapped_column(Numeric(10, 2))
    effective_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[str | None] = mapped_column(String(120))
    last_eligibility_check_id: Mapped[int | None] = mapped_column(Integer)

    patient: Mapped[Patient] = relationship(back_populates="policies")
    payer: Mapped[Payer] = relationship()


class Guarantor(Base):
    """Person financially responsible for the patient's account."""

    __tablename__ = "guarantors"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), unique=True)
    same_as_patient: Mapped[bool | None] = mapped_column(Boolean, default=True)
    relationship_to_patient: Mapped[str | None] = mapped_column(String(30), default="self")
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(255))
    address_line1: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(40))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="guarantor")


class PhiRelease(Base):
    """HIPAA release of PHI: who staff may discuss the patient's information with."""

    __tablename__ = "phi_releases"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    recipient_name: Mapped[str] = mapped_column(String(200))
    relationship_to_patient: Mapped[str | None] = mapped_column(String(50))
    phone: Mapped[str | None] = mapped_column(String(30))
    may_discuss_appointments: Mapped[bool | None] = mapped_column(Boolean, default=True)
    may_discuss_billing: Mapped[bool | None] = mapped_column(Boolean, default=False)
    may_discuss_clinical: Mapped[bool | None] = mapped_column(Boolean, default=False)
    signed_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="phi_releases")


class EligibilityCheck(Base):
    __tablename__ = "eligibility_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("insurance_policies.id", ondelete="SET NULL"))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    request_id: Mapped[int | None] = mapped_column(ForeignKey("portal_requests.id", ondelete="SET NULL"))
    coverage_status: Mapped[str] = mapped_column(String(30))  # active | inactive | not_found | error
    in_network: Mapped[bool | None] = mapped_column(Boolean)
    plan_name: Mapped[str | None] = mapped_column(String(200))
    copay: Mapped[str | None] = mapped_column(String(40))
    message: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict] = mapped_column(JsonType, default=dict)
    checked_by: Mapped[str] = mapped_column(String(120), default="system")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # 270 request context
    date_of_service: Mapped[date | None] = mapped_column(Date)
    npi_type: Mapped[str | None] = mapped_column(String(12))  # individual | group
    npi: Mapped[str | None] = mapped_column(String(20))
    # 271 response context
    payer_member_name: Mapped[str | None] = mapped_column(String(200))
    payer_member_dob: Mapped[date | None] = mapped_column(Date)
    payer_member_id: Mapped[str | None] = mapped_column(String(60))
    plan_type: Mapped[str | None] = mapped_column(String(20))
    plan_start: Mapped[date | None] = mapped_column(Date)
    plan_end: Mapped[date | None] = mapped_column(Date)
    eligibility_start: Mapped[date | None] = mapped_column(Date)
    eligibility_end: Mapped[date | None] = mapped_column(Date)
    raw_x12: Mapped[str | None] = mapped_column(Text)
    discrepancies: Mapped[list | None] = mapped_column(JsonType, default=list)
    ai_summary: Mapped[str | None] = mapped_column(Text)

    benefits: Mapped[list[EligibilityBenefit]] = relationship(back_populates="check", cascade="all, delete-orphan",
                                                              order_by="EligibilityBenefit.id")


class EligibilityBenefit(Base):
    """One benefit line from a 271 response (an EB segment), normalized."""

    __tablename__ = "eligibility_benefits"
    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey("eligibility_checks.id", ondelete="CASCADE"), index=True)
    network: Mapped[str] = mapped_column(String(4))  # in | out
    # co_payment | co_insurance | deductible | remaining_deductible | out_of_pocket | remaining_out_of_pocket
    category: Mapped[str] = mapped_column(String(40))
    coverage_level: Mapped[str] = mapped_column(String(12), default="individual")  # individual | family
    service_type_code: Mapped[str] = mapped_column(String(4))  # X12 service type code, e.g. 98
    service_type: Mapped[str] = mapped_column(String(120))
    amount: Mapped[float | None] = mapped_column(Numeric(10, 2))
    percent: Mapped[float | None] = mapped_column(Numeric(5, 2))
    time_period: Mapped[str | None] = mapped_column(String(30))  # visit | calendar_year | remaining
    auth_required: Mapped[bool | None] = mapped_column(Boolean)
    message: Mapped[str | None] = mapped_column(String(300))

    check: Mapped[EligibilityCheck] = relationship(back_populates="benefits")


class PatientDocument(Base):
    """Registration documents/images: photo ID, insurance card front/back, etc."""

    __tablename__ = "patient_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # photo_id | insurance_card_front | insurance_card_back | other
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    storage_path: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[str] = mapped_column(String(120), default="system")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="documents")


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="booked")  # booked | cancelled | completed | no_show
    visit_reason: Mapped[str | None] = mapped_column(String(300))
    source_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("portal_requests.id", ondelete="SET NULL", use_alter=True, name="fk_appt_request")
    )
    booked_by: Mapped[str] = mapped_column(String(120), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    patient: Mapped[Patient] = relationship(back_populates="appointments")
    provider: Mapped[Provider] = relationship()


class EmailMessage(Base):
    __tablename__ = "email_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound
    source: Mapped[str] = mapped_column(String(20), default="manual")  # graph | imap | webhook | manual | demo | portal
    from_address: Mapped[str] = mapped_column(String(255), index=True)
    from_name: Mapped[str | None] = mapped_column(String(200))
    to_address: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(500), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    # original HTML as received (kept for provenance); body_text is the readable version shown and analysed
    body_raw: Mapped[str | None] = mapped_column(Text)
    # test mode: the address this message was actually delivered to instead of to_address
    redirect_to: Mapped[str | None] = mapped_column(String(255))
    in_reply_to: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("portal_requests.id", ondelete="SET NULL", use_alter=True, name="fk_email_request"), index=True
    )
    send_status: Mapped[str | None] = mapped_column(String(20))  # queued | sent | failed | stored (outbox)
    send_error: Mapped[str | None] = mapped_column(Text)
    delivery_method: Mapped[str | None] = mapped_column(String(20))  # smtp | graph | graph-reply | sendgrid | mailgun | outbox
    mailbox_id: Mapped[int | None] = mapped_column(Integer, index=True)  # integration_mailboxes.id it arrived in / was sent from
    provider_message_id: Mapped[str | None] = mapped_column(String(300))  # Microsoft Graph message id
    conversation_id: Mapped[str | None] = mapped_column(String(300), index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_by: Mapped[str | None] = mapped_column(String(120))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PortalRequest(Base):
    """A scheduling request in the portal, usually created from an inbound email."""

    __tablename__ = "portal_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default=RequestStatus.RECEIVED.value, index=True)
    intent: Mapped[str | None] = mapped_column(String(30))
    source_email_id: Mapped[int | None] = mapped_column(ForeignKey("email_messages.id", ondelete="SET NULL"))
    extracted: Mapped[dict] = mapped_column(JsonType, default=dict)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id", ondelete="SET NULL"), index=True)
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_details: Mapped[dict] = mapped_column(JsonType, default=dict)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(String(20))  # auto_respond | manual_review
    review_reasons: Mapped[list] = mapped_column(JsonType, default=list)
    draft_response: Mapped[str | None] = mapped_column(Text)
    # AI-proposed chart + slot for an unmatched new patient; nothing is created until staff approve it
    draft_chart: Mapped[dict | None] = mapped_column(JsonType)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id", ondelete="SET NULL"))
    assigned_to: Mapped[str | None] = mapped_column(String(120))
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    llm_provider: Mapped[str | None] = mapped_column(String(40))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source_email: Mapped[EmailMessage | None] = relationship(foreign_keys=[source_email_id])
    patient: Mapped[Patient | None] = relationship()
    provider: Mapped[Provider | None] = relationship()
    appointment: Mapped[Appointment | None] = relationship(foreign_keys=[appointment_id])


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="system")
    details: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class IntegrationConnection(Base):
    """An identity-provider connection (Microsoft Entra ID) used to read and send mail.
    Secrets and tokens are encrypted with app.services.crypto."""

    __tablename__ = "integration_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), unique=True)  # microsoft
    auth_mode: Mapped[str] = mapped_column(String(20), default="delegated")  # delegated | application
    tenant_id: Mapped[str | None] = mapped_column(String(100))
    client_id: Mapped[str | None] = mapped_column(String(100))
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    client_secret_hint: Mapped[str | None] = mapped_column(String(10))  # last 4 chars, for display
    status: Mapped[str] = mapped_column(String(30), default="not_configured")
    # not_configured | configured | connected | reauth_required | error
    connected_account: Mapped[str | None] = mapped_column(String(255))
    connected_name: Mapped[str | None] = mapped_column(String(255))
    scopes: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    poll_interval_seconds: Mapped[int | None] = mapped_column(Integer, default=120)
    last_error: Mapped[str | None] = mapped_column(Text)
    connected_by: Mapped[str | None] = mapped_column(String(120))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class IntegrationMailbox(Base):
    """A mailbox whose incoming mail becomes portal requests, and which replies in-thread."""

    __tablename__ = "integration_mailboxes"
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    folder: Mapped[str] = mapped_column(String(200), default="Inbox")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    process_inbound: Mapped[bool] = mapped_column(Boolean, default=True)
    reply_from_same_mailbox: Mapped[bool] = mapped_column(Boolean, default=True)
    mark_as_read: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default_sender: Mapped[bool] = mapped_column(Boolean, default=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_count: Mapped[int | None] = mapped_column(Integer)
    # cursor: newest receivedDateTime already processed (lets polling progress without Mail.ReadWrite)
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # senders to ignore: comma-separated domains (subdomains included) or full addresses, e.g. "docshealth.com"
    skip_sender_domains: Mapped[str | None] = mapped_column(Text)
    skipped_internal_total: Mapped[int | None] = mapped_column(Integer)
    # folder counts from Graph (Mail.Read is enough), refreshed on every check
    unread_count: Mapped[int | None] = mapped_column(Integer)
    total_count: Mapped[int | None] = mapped_column(Integer)
    counts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)


class MailboxSkippedMessage(Base):
    """One row per mailbox message that was deliberately not imported (internal sender, own mailbox, auto-reply).
    Unique per message, so dashboard counts are true distinct counts however often the mailbox is checked."""

    __tablename__ = "mailbox_skipped_messages"
    __table_args__ = (UniqueConstraint("mailbox_id", "provider_message_id", name="uq_mailbox_skipped_msg"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("integration_mailboxes.id", ondelete="CASCADE"), index=True)
    provider_message_id: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(20), index=True)  # internal | own_mailbox | automated
    reason: Mapped[str] = mapped_column(String(300))
    from_address: Mapped[str | None] = mapped_column(String(255))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)


class OAuthState(Base):
    """Short-lived OAuth state + PKCE verifier for the Entra ID sign-in redirect."""

    __tablename__ = "oauth_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(30))
    flow: Mapped[str] = mapped_column(String(20))  # authorize | admin_consent
    code_verifier_encrypted: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    """Runtime settings changed from the portal (no restart, no .env edit)."""

    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JsonType, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
