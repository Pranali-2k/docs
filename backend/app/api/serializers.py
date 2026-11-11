"""Model -> JSON dict converters used by the routes."""
from __future__ import annotations

from app.models import (
    Appointment,
    AuditLog,
    EligibilityBenefit,
    EligibilityCheck,
    EmailMessage,
    Guarantor,
    InsurancePolicy,
    Patient,
    PatientDocument,
    Payer,
    PhiRelease,
    PortalRequest,
    Provider,
)
from app.services.normalize import format_phone


def iso(v):
    return v.isoformat() if v is not None else None


def num(v):
    return float(v) if v is not None else None


def patient_summary(p: Patient | None) -> dict | None:
    if p is None:
        return None
    pol = p.primary_policy
    return {
        "id": p.id,
        "mrn": p.mrn,
        "first_name": p.first_name,
        "last_name": p.last_name,
        "full_name": p.full_name,
        "date_of_birth": iso(p.date_of_birth),
        "phone": format_phone(p.phone),
        "email": p.email,
        "primary_payer": pol.payer.name if pol else None,
        "member_id": pol.member_id if pol else None,
    }


def demographics(p: Patient) -> dict:
    """Full Patient Data form. SSN / license are never returned in clear; only masked."""
    return {
        "prefix": p.prefix, "first_name": p.first_name, "middle_name": p.middle_name, "last_name": p.last_name,
        "suffix": p.suffix, "preferred_name": p.preferred_name, "previous_name": p.previous_name,
        "display_name": p.display_name, "is_test_patient": bool(p.is_test_patient), "marital_status": p.marital_status,
        "date_of_birth": iso(p.date_of_birth), "sex": p.sex,
        "ssn_masked": f"###-##-{p.ssn_last4}" if p.ssn_last4 else None,
        "drivers_license_masked": f"****{p.drivers_license_last4}" if p.drivers_license_last4 else None,
        "drivers_license_state": p.drivers_license_state,
        "phone": format_phone(p.phone), "home_phone": format_phone(p.home_phone),
        "work_phone": format_phone(p.work_phone), "preferred_contact_method": p.preferred_contact_method,
        "email": p.email, "address_line1": p.address_line1, "address_line2": p.address_line2, "city": p.city,
        "state": p.state, "postal_code": p.postal_code,
        "pcp_name": p.pcp_name, "pms_id": p.pms_id, "portal_status": p.portal_status or "inactive",
    }


def payer(py: Payer) -> dict:
    label = f"{py.name} ({py.electronic_payer_id})" if py.electronic_payer_id else py.name
    return {"id": py.id, "name": py.name, "label": label, "payer_code": py.payer_code,
            "electronic_payer_id": py.electronic_payer_id, "financial_category": py.financial_category,
            "claims_addresses": py.claims_addresses or [], "aliases": py.aliases or []}


def policy(pol: InsurancePolicy) -> dict:
    order = pol.coverage_order or ("primary" if pol.is_primary else "secondary")
    return {
        "id": pol.id,
        "payer_id": pol.payer_id,
        "payer_name": pol.payer.name,
        "payer_label": payer(pol.payer)["label"],
        "payer_code": pol.payer.payer_code,
        "financial_category": pol.payer.financial_category,
        "member_id": pol.member_id,
        "group_number": pol.group_number,
        "plan_name": pol.plan_name,
        "policy_type": pol.policy_type,
        "claims_address": pol.claims_address,
        "notes": pol.notes,
        "coverage_order": order,
        "subscriber_name": pol.subscriber_name,
        "relationship_to_subscriber": pol.relationship_to_subscriber,
        "holder_first_name": pol.holder_first_name,
        "holder_last_name": pol.holder_last_name,
        "holder_date_of_birth": iso(pol.holder_date_of_birth),
        "requires_referral": bool(pol.requires_referral),
        "requires_precert_inpatient": bool(pol.requires_precert_inpatient),
        "requires_preauth_outpatient": bool(pol.requires_preauth_outpatient),
        "eligibility_status": pol.eligibility_status,
        "copay_amount": num(pol.copay_amount),
        "coinsurance_pct": num(pol.coinsurance_pct),
        "deductible": num(pol.deductible),
        "remaining_deductible": num(pol.remaining_deductible),
        "out_of_pocket": num(pol.out_of_pocket),
        "remaining_out_of_pocket": num(pol.remaining_out_of_pocket),
        "effective_date": iso(pol.effective_date),
        "end_date": iso(pol.end_date),
        "last_eligibility_check_id": pol.last_eligibility_check_id,
        "is_primary": pol.is_primary,
        "is_active": pol.is_active,
        "archived_at": iso(pol.archived_at),
        "archived_by": pol.archived_by,
        "created_at": iso(pol.created_at),
    }


def guarantor(g: Guarantor | None) -> dict:
    if g is None:
        return {"same_as_patient": True, "relationship_to_patient": "self"}
    return {
        "same_as_patient": g.same_as_patient is not False, "relationship_to_patient": g.relationship_to_patient,
        "first_name": g.first_name, "last_name": g.last_name, "date_of_birth": iso(g.date_of_birth),
        "phone": format_phone(g.phone), "email": g.email, "address_line1": g.address_line1, "city": g.city,
        "state": g.state, "postal_code": g.postal_code, "updated_at": iso(g.updated_at),
    }


def phi_release(r: PhiRelease) -> dict:
    return {
        "id": r.id, "recipient_name": r.recipient_name, "relationship_to_patient": r.relationship_to_patient,
        "phone": format_phone(r.phone), "may_discuss_appointments": bool(r.may_discuss_appointments),
        "may_discuss_billing": bool(r.may_discuss_billing), "may_discuss_clinical": bool(r.may_discuss_clinical),
        "signed_on": iso(r.signed_on), "expires_on": iso(r.expires_on), "revoked_at": iso(r.revoked_at),
        "created_by": r.created_by, "created_at": iso(r.created_at),
    }


def document(d: PatientDocument) -> dict:
    return {
        "id": d.id, "kind": d.kind, "filename": d.filename, "content_type": d.content_type,
        "size_bytes": d.size_bytes, "uploaded_by": d.uploaded_by, "uploaded_at": iso(d.uploaded_at),
    }


def eligibility(e: EligibilityCheck) -> dict:
    return {
        "id": e.id, "policy_id": e.policy_id, "provider_id": e.provider_id, "request_id": e.request_id,
        "coverage_status": e.coverage_status, "in_network": e.in_network, "plan_name": e.plan_name,
        "plan_type": e.plan_type, "copay": e.copay, "message": e.message, "raw_response": e.raw_response,
        "checked_by": e.checked_by, "checked_at": iso(e.checked_at), "date_of_service": iso(e.date_of_service),
        "npi_type": e.npi_type, "npi": e.npi, "discrepancy_count": len(e.discrepancies or []),
        "ai_summary": e.ai_summary,
    }


def benefit(b: EligibilityBenefit) -> dict:
    return {
        "id": b.id, "network": b.network, "category": b.category, "coverage_level": b.coverage_level,
        "service_type_code": b.service_type_code, "service_type": b.service_type, "amount": num(b.amount),
        "percent": num(b.percent), "time_period": b.time_period, "auth_required": b.auth_required,
        "message": b.message,
    }


def eligibility_detail(e: EligibilityCheck, patient: Patient, provider_obj: Provider | None,
                       policy_obj: InsurancePolicy | None) -> dict:
    return {
        **eligibility(e),
        "patient_name": f"{patient.last_name}, {patient.first_name}".upper(),
        "patient_dob": iso(patient.date_of_birth),
        "provider_name": provider_obj.full_name if provider_obj else None,
        "payer_name": policy_obj.payer.name if policy_obj else None,
        "policy_number": policy_obj.member_id if policy_obj else e.payer_member_id,
        "payer_member_name": e.payer_member_name,
        "payer_member_dob": iso(e.payer_member_dob),
        "payer_member_id": e.payer_member_id,
        "plan_start": iso(e.plan_start), "plan_end": iso(e.plan_end),
        "eligibility_start": iso(e.eligibility_start), "eligibility_end": iso(e.eligibility_end),
        "discrepancies": e.discrepancies or [],
        "raw_x12": e.raw_x12,
        "benefits": [benefit(b) for b in e.benefits],
    }


def provider(p: Provider) -> dict:
    return {
        "id": p.id, "full_name": p.full_name, "specialty": p.specialty, "npi": p.npi, "location": p.location,
        "is_active": p.is_active,
        "in_network_payers": [n.payer.name for n in p.networks if n.in_network],
    }


def appointment(a: Appointment) -> dict:
    return {
        "id": a.id,
        "patient_id": a.patient_id,
        "patient_name": a.patient.full_name if a.patient else None,
        "patient_mrn": a.patient.mrn if a.patient else None,
        "provider_id": a.provider_id,
        "provider_name": a.provider.full_name if a.provider else None,
        "start_at": iso(a.start_at),
        "end_at": iso(a.end_at),
        "status": a.status,
        "visit_reason": a.visit_reason,
        "source_request_id": a.source_request_id,
        "booked_by": a.booked_by,
        "created_at": iso(a.created_at),
    }


def email(m: EmailMessage) -> dict:
    return {
        "id": m.id, "message_id": m.message_id, "direction": m.direction, "source": m.source,
        "from_address": m.from_address, "from_name": m.from_name, "to_address": m.to_address,
        "subject": m.subject, "body_text": m.body_text, "request_id": m.request_id,
        "send_status": m.send_status, "send_error": m.send_error, "sent_by": m.sent_by,
        "delivery_method": m.delivery_method, "delivered_at": iso(m.delivered_at), "redirect_to": m.redirect_to,
        "received_at": iso(m.received_at),
    }


def request_row(r: PortalRequest) -> dict:
    e = r.source_email
    ext = r.extracted or {}
    pat = ext.get("patient") or {}
    return {
        "id": r.id,
        "reference": r.reference,
        "status": r.status,
        "intent": r.intent,
        "priority": r.priority,
        "decision": r.decision,
        "from_address": e.from_address if e else None,
        "subject": e.subject if e else None,
        "received_at": iso(e.received_at) if e else iso(r.created_at),
        "patient": patient_summary(r.patient),
        "requested_name": " ".join(filter(None, [pat.get("first_name"), pat.get("last_name")])) or None,
        "ai_confidence": r.ai_confidence,
        "match_confidence": r.match_confidence,
        "review_reasons": r.review_reasons or [],
        "assigned_to": r.assigned_to,
        "summary": r.ai_summary,
        "created_at": iso(r.created_at),
        "updated_at": iso(r.updated_at),
        "resolved_at": iso(r.resolved_at),
    }


def audit_entry(a: AuditLog) -> dict:
    return {
        "id": a.id, "entity_type": a.entity_type, "entity_id": a.entity_id, "action": a.action,
        "actor": a.actor, "details": a.details, "created_at": iso(a.created_at),
    }
