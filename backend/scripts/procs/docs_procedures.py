"""Python functions for the Docs Health insert procedures (generated; do not edit by hand).

Regenerate with:  python -m scripts.procs.generate

One function per procedure in scripts/procs/procedures.sql. Each takes an open psycopg
connection and the procedure's arguments (without the p_ prefix), calls the procedure,
and turns the procedure's RAISE EXCEPTION into a ProcedureError carrying its message:

    import psycopg
    from scripts.procs import docs_procedures as db

    with psycopg.connect(url) as conn:
        db.insert_payer(conn, name="Aetna", payer_code="AETNA", aliases=["Aetna Inc."])
        try:
            db.insert_payer(conn, name="Aetna", payer_code="AETNA2", aliases=[])
        except db.ProcedureError as error:
            print(error)          # Payer name already exists: Aetna

Parameters are bound by the driver and cast to the procedure's own parameter types, so
nothing is formatted into SQL text. JSONB arguments take Python dicts and lists.
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg import Connection, errors
from psycopg.types.json import Jsonb

#: procedure -> (table it writes, [(parameter, SQL type), ...])
PROCEDURES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "insert_user": ('users', [('p_email', 'VARCHAR(255)'), ('p_full_name', 'VARCHAR(200)'), ('p_role', 'VARCHAR(30)'), ('p_password_hash', 'VARCHAR(255)'), ('p_is_active', 'BOOLEAN')]),
    "insert_summary": ('summaries', [('p_summary_id', 'UUID'), ('p_session_id', 'UUID'), ('p_patient_id', 'BIGINT'), ('p_chief_complaint', 'TEXT'), ('p_current_symptoms', 'TEXT'), ('p_allergies', 'TEXT'), ('p_past_history', 'TEXT'), ('p_additional_notes', 'TEXT')]),
    "insert_session_summary": ('session_summaries', [('p_session_id', 'VARCHAR(64)'), ('p_patient_id', 'VARCHAR(128)'), ('p_patient_name', 'TEXT'), ('p_json_filename', 'TEXT'), ('p_language', 'TEXT'), ('p_message_count', 'INTEGER'), ('p_generated_at', 'TIMESTAMPTZ'), ('p_chief_complaint', 'TEXT'), ('p_history_of_present_illness', 'TEXT'), ('p_current_symptoms', 'TEXT'), ('p_allergies', 'TEXT'), ('p_current_medications', 'TEXT'), ('p_past_medical_history', 'TEXT'), ('p_family_history', 'TEXT'), ('p_social_history', 'TEXT'), ('p_other_notes', 'TEXT'), ('p_summary_text', 'TEXT'), ('p_llm_provider', 'TEXT'), ('p_llm_model', 'TEXT'), ('p_icd_10', 'TEXT'), ('p_icd_10_details', 'JSONB'), ('p_icd_10_status', 'TEXT'), ('p_cpt', 'TEXT'), ('p_cpt_details', 'JSONB'), ('p_cpt_status', 'TEXT')]),
    "insert_session_registry": ('session_registry', [('p_session_id', 'VARCHAR(64)'), ('p_patient_id', 'VARCHAR(128)'), ('p_patient_name', 'TEXT'), ('p_timestamp', 'TIMESTAMPTZ'), ('p_conversation_table', 'TEXT')]),
    "insert_provider": ('providers', [('p_full_name', 'VARCHAR(200)'), ('p_specialty', 'VARCHAR(120)'), ('p_npi', 'VARCHAR(20)'), ('p_location', 'VARCHAR(200)'), ('p_is_active', 'BOOLEAN')]),
    "insert_provider_network": ('provider_networks', [('p_provider_id', 'INTEGER'), ('p_payer_id', 'INTEGER'), ('p_in_network', 'BOOLEAN')]),
    "insert_provider_availability": ('provider_availability', [('p_provider_id', 'INTEGER'), ('p_weekday', 'INTEGER'), ('p_start_time', 'TIME'), ('p_end_time', 'TIME'), ('p_slot_minutes', 'INTEGER')]),
    "insert_portal_request": ('portal_requests', [('p_reference', 'VARCHAR(20)'), ('p_status', 'VARCHAR(20)'), ('p_intent', 'VARCHAR(30)'), ('p_source_email_id', 'INTEGER'), ('p_extracted', 'JSONB'), ('p_patient_id', 'INTEGER'), ('p_match_confidence', 'DOUBLE PRECISION'), ('p_match_details', 'JSONB'), ('p_provider_id', 'INTEGER'), ('p_ai_confidence', 'DOUBLE PRECISION'), ('p_ai_summary', 'TEXT'), ('p_decision', 'VARCHAR(20)'), ('p_review_reasons', 'JSONB'), ('p_draft_response', 'TEXT'), ('p_appointment_id', 'INTEGER'), ('p_assigned_to', 'VARCHAR(120)'), ('p_priority', 'VARCHAR(10)'), ('p_llm_provider', 'VARCHAR(40)'), ('p_resolution_note', 'TEXT'), ('p_resolved_by', 'VARCHAR(120)')]),
    "insert_phi_release": ('phi_releases', [('p_patient_id', 'INTEGER'), ('p_recipient_name', 'VARCHAR(200)'), ('p_relationship_to_patient', 'VARCHAR(50)'), ('p_phone', 'VARCHAR(30)'), ('p_may_discuss_appointments', 'BOOLEAN'), ('p_may_discuss_billing', 'BOOLEAN'), ('p_may_discuss_clinical', 'BOOLEAN'), ('p_signed_on', 'DATE'), ('p_expires_on', 'DATE'), ('p_revoked_at', 'TIMESTAMPTZ'), ('p_created_by', 'VARCHAR(120)')]),
    "insert_payer": ('payers', [('p_name', 'VARCHAR(200)'), ('p_payer_code', 'VARCHAR(40)'), ('p_aliases', 'JSONB'), ('p_electronic_payer_id', 'VARCHAR(20)'), ('p_financial_category', 'VARCHAR(40)'), ('p_claims_addresses', 'JSONB')]),
    "insert_payer_plan": ('payer_plans', [('p_payer_id', 'INTEGER'), ('p_name', 'VARCHAR(200)'), ('p_plan_type', 'VARCHAR(20)')]),
    "insert_patient": ('patients', [('p_mrn', 'VARCHAR(30)'), ('p_prefix', 'VARCHAR(20)'), ('p_first_name', 'VARCHAR(100)'), ('p_middle_name', 'VARCHAR(100)'), ('p_last_name', 'VARCHAR(100)'), ('p_suffix', 'VARCHAR(20)'), ('p_preferred_name', 'VARCHAR(100)'), ('p_previous_name', 'VARCHAR(200)'), ('p_is_test_patient', 'BOOLEAN'), ('p_marital_status', 'VARCHAR(30)'), ('p_date_of_birth', 'DATE'), ('p_sex', 'VARCHAR(20)'), ('p_ssn_encrypted', 'TEXT'), ('p_ssn_last4', 'VARCHAR(4)'), ('p_drivers_license_encrypted', 'TEXT'), ('p_drivers_license_last4', 'VARCHAR(4)'), ('p_drivers_license_state', 'VARCHAR(2)'), ('p_phone', 'VARCHAR(30)'), ('p_home_phone', 'VARCHAR(30)'), ('p_work_phone', 'VARCHAR(30)'), ('p_preferred_contact_method', 'VARCHAR(20)'), ('p_email', 'VARCHAR(255)'), ('p_address_line1', 'VARCHAR(200)'), ('p_address_line2', 'VARCHAR(200)'), ('p_city', 'VARCHAR(100)'), ('p_state', 'VARCHAR(40)'), ('p_postal_code', 'VARCHAR(20)'), ('p_preferred_provider_id', 'INTEGER'), ('p_notes', 'TEXT')]),
    "insert_patient_document": ('patient_documents', [('p_patient_id', 'INTEGER'), ('p_kind', 'VARCHAR(40)'), ('p_filename', 'VARCHAR(255)'), ('p_content_type', 'VARCHAR(100)'), ('p_storage_path', 'VARCHAR(500)'), ('p_size_bytes', 'INTEGER'), ('p_uploaded_by', 'VARCHAR(120)')]),
    "insert_password_reset_token": ('password_reset_tokens', [('p_user_id', 'INTEGER'), ('p_token_hash', 'VARCHAR(64)'), ('p_expires_at', 'TIMESTAMPTZ'), ('p_requested_ip', 'VARCHAR(64)')]),
    "insert_oauth_state": ('oauth_states', [('p_state_hash', 'VARCHAR(64)'), ('p_provider', 'VARCHAR(30)'), ('p_flow', 'VARCHAR(20)'), ('p_code_verifier_encrypted', 'TEXT'), ('p_created_by', 'VARCHAR(120)'), ('p_expires_at', 'TIMESTAMPTZ')]),
    "insert_integration_mailbox": ('integration_mailboxes', [('p_connection_id', 'INTEGER'), ('p_address', 'VARCHAR(255)'), ('p_display_name', 'VARCHAR(200)'), ('p_folder', 'VARCHAR(200)'), ('p_enabled', 'BOOLEAN'), ('p_process_inbound', 'BOOLEAN'), ('p_reply_from_same_mailbox', 'BOOLEAN'), ('p_mark_as_read', 'BOOLEAN'), ('p_is_default_sender', 'BOOLEAN'), ('p_created_by', 'VARCHAR(120)'), ('p_skip_sender_domains', 'TEXT')]),
    "insert_integration_connection": ('integration_connections', [('p_provider', 'VARCHAR(30)'), ('p_auth_mode', 'VARCHAR(20)'), ('p_tenant_id', 'VARCHAR(100)'), ('p_client_id', 'VARCHAR(100)'), ('p_client_secret_encrypted', 'TEXT'), ('p_client_secret_hint', 'VARCHAR(10)'), ('p_status', 'VARCHAR(30)'), ('p_connected_account', 'VARCHAR(255)'), ('p_connected_name', 'VARCHAR(255)'), ('p_scopes', 'TEXT'), ('p_refresh_token_encrypted', 'TEXT'), ('p_token_expires_at', 'TIMESTAMPTZ'), ('p_poll_interval_seconds', 'INTEGER'), ('p_connected_by', 'VARCHAR(120)')]),
    "insert_insurance_policy": ('insurance_policies', [('p_patient_id', 'INTEGER'), ('p_payer_id', 'INTEGER'), ('p_member_id', 'VARCHAR(60)'), ('p_group_number', 'VARCHAR(60)'), ('p_subscriber_name', 'VARCHAR(200)'), ('p_relationship_to_subscriber', 'VARCHAR(30)'), ('p_is_primary', 'BOOLEAN'), ('p_is_active', 'BOOLEAN'), ('p_coverage_order', 'VARCHAR(10)'), ('p_plan_name', 'VARCHAR(200)'), ('p_policy_type', 'VARCHAR(20)'), ('p_claims_address', 'VARCHAR(300)'), ('p_notes', 'VARCHAR(500)'), ('p_requires_referral', 'BOOLEAN'), ('p_requires_precert_inpatient', 'BOOLEAN'), ('p_requires_preauth_outpatient', 'BOOLEAN'), ('p_holder_first_name', 'VARCHAR(100)'), ('p_holder_last_name', 'VARCHAR(100)'), ('p_holder_date_of_birth', 'DATE'), ('p_eligibility_status', 'VARCHAR(20)'), ('p_copay_amount', 'NUMERIC(10,2)'), ('p_coinsurance_pct', 'NUMERIC(5,2)'), ('p_deductible', 'NUMERIC(10,2)'), ('p_remaining_deductible', 'NUMERIC(10,2)'), ('p_out_of_pocket', 'NUMERIC(10,2)'), ('p_remaining_out_of_pocket', 'NUMERIC(10,2)'), ('p_effective_date', 'DATE'), ('p_end_date', 'DATE')]),
    "insert_guarantor": ('guarantors', [('p_patient_id', 'INTEGER'), ('p_same_as_patient', 'BOOLEAN'), ('p_relationship_to_patient', 'VARCHAR(30)'), ('p_first_name', 'VARCHAR(100)'), ('p_last_name', 'VARCHAR(100)'), ('p_date_of_birth', 'DATE'), ('p_phone', 'VARCHAR(30)'), ('p_email', 'VARCHAR(255)'), ('p_address_line1', 'VARCHAR(200)'), ('p_city', 'VARCHAR(100)'), ('p_state', 'VARCHAR(40)'), ('p_postal_code', 'VARCHAR(20)')]),
    "insert_emr_export": ('emr_exports', [('p_export_id', 'VARCHAR(64)'), ('p_session_id', 'VARCHAR(64)'), ('p_patient_id', 'VARCHAR(128)'), ('p_patient_name', 'TEXT'), ('p_exported_at', 'TIMESTAMPTZ'), ('p_export_file', 'TEXT'), ('p_payload', 'JSONB')]),
    "insert_email_message": ('email_messages', [('p_message_id', 'VARCHAR(500)'), ('p_direction', 'VARCHAR(10)'), ('p_source', 'VARCHAR(20)'), ('p_from_address', 'VARCHAR(255)'), ('p_from_name', 'VARCHAR(200)'), ('p_to_address', 'VARCHAR(255)'), ('p_subject', 'VARCHAR(500)'), ('p_body_text', 'TEXT'), ('p_in_reply_to', 'VARCHAR(500)'), ('p_request_id', 'INTEGER'), ('p_send_status', 'VARCHAR(20)'), ('p_send_error', 'TEXT'), ('p_sent_by', 'VARCHAR(120)'), ('p_received_at', 'TIMESTAMPTZ'), ('p_delivery_method', 'VARCHAR(20)'), ('p_delivered_at', 'TIMESTAMPTZ'), ('p_mailbox_id', 'INTEGER'), ('p_provider_message_id', 'VARCHAR(300)'), ('p_conversation_id', 'VARCHAR(300)'), ('p_body_raw', 'TEXT')]),
    "insert_eligibility_check": ('eligibility_checks', [('p_patient_id', 'INTEGER'), ('p_policy_id', 'INTEGER'), ('p_provider_id', 'INTEGER'), ('p_request_id', 'INTEGER'), ('p_coverage_status', 'VARCHAR(30)'), ('p_in_network', 'BOOLEAN'), ('p_plan_name', 'VARCHAR(200)'), ('p_copay', 'VARCHAR(40)'), ('p_message', 'TEXT'), ('p_raw_response', 'JSONB'), ('p_checked_by', 'VARCHAR(120)'), ('p_checked_at', 'TIMESTAMPTZ'), ('p_date_of_service', 'DATE'), ('p_npi_type', 'VARCHAR(12)'), ('p_npi', 'VARCHAR(20)'), ('p_payer_member_name', 'VARCHAR(200)'), ('p_payer_member_dob', 'DATE'), ('p_payer_member_id', 'VARCHAR(60)'), ('p_plan_type', 'VARCHAR(20)'), ('p_plan_start', 'DATE'), ('p_plan_end', 'DATE'), ('p_eligibility_start', 'DATE'), ('p_eligibility_end', 'DATE'), ('p_raw_x12', 'TEXT'), ('p_discrepancies', 'JSONB'), ('p_ai_summary', 'TEXT')]),
    "insert_eligibility_benefit": ('eligibility_benefits', [('p_check_id', 'INTEGER'), ('p_network', 'VARCHAR(4)'), ('p_category', 'VARCHAR(40)'), ('p_coverage_level', 'VARCHAR(12)'), ('p_service_type_code', 'VARCHAR(4)'), ('p_service_type', 'VARCHAR(120)'), ('p_amount', 'NUMERIC(10,2)'), ('p_percent', 'NUMERIC(5,2)'), ('p_time_period', 'VARCHAR(30)'), ('p_auth_required', 'BOOLEAN'), ('p_message', 'VARCHAR(300)')]),
    "insert_appointment": ('appointments', [('p_appointment_id', 'VARCHAR(64)'), ('p_session_id', 'VARCHAR(64)'), ('p_patient_id', 'VARCHAR(128)'), ('p_icd_10', 'TEXT'), ('p_patient_name', 'TEXT'), ('p_scheduled_for', 'TIMESTAMPTZ'), ('p_duration_minutes', 'INTEGER'), ('p_clinician', 'TEXT'), ('p_reason', 'TEXT'), ('p_status', 'TEXT'), ('p_booked_via', 'TEXT'), ('p_cpt', 'TEXT'), ('p_patient_email', 'TEXT'), ('p_email_status', 'TEXT')]),
    "insert_app_setting": ('app_settings', [('p_key', 'VARCHAR(100)'), ('p_value', 'JSONB'), ('p_updated_by', 'VARCHAR(120)')]),
}


class ProcedureError(Exception):
    """The procedure rejected the call (its RAISE EXCEPTION message is the text)."""


def call_sql(name: str) -> str:
    """The CALL statement for a procedure, with named placeholders and explicit casts."""
    _table, params = PROCEDURES[name]
    placeholders = ", ".join(f"%({p})s::{t}" for p, t in params)
    return f"CALL public.{name}({placeholders})"


def _call(conn: Connection, name: str, values: dict, *, commit: bool) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(call_sql(name), values)
    except errors.RaiseException as error:
        conn.rollback()
        raise ProcedureError(error.diag.message_primary or str(error)) from None
    except psycopg.Error:
        conn.rollback()
        raise
    if commit:
        conn.commit()


def insert_user(
    conn: Connection,
    *,
    email: str | None = None,
    full_name: str | None = None,
    role: str | None = None,
    password_hash: str | None = None,
    is_active: bool | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_user(...): insert one row into `users`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        email: VARCHAR(255)
        full_name: VARCHAR(200)
        role: VARCHAR(30)
        password_hash: VARCHAR(255)
        is_active: BOOLEAN
    """
    _call(
        conn,
        "insert_user",
        {
        "p_email": email,
        "p_full_name": full_name,
        "p_role": role,
        "p_password_hash": password_hash,
        "p_is_active": is_active,
        },
        commit=commit,
    )


def insert_summary(
    conn: Connection,
    *,
    summary_id: UUID | str | None = None,
    session_id: UUID | str | None = None,
    patient_id: int | None = None,
    chief_complaint: str | None = None,
    current_symptoms: str | None = None,
    allergies: str | None = None,
    past_history: str | None = None,
    additional_notes: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_summary(...): insert one row into `summaries`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        summary_id: UUID
        session_id: UUID
        patient_id: BIGINT
        chief_complaint: TEXT
        current_symptoms: TEXT
        allergies: TEXT
        past_history: TEXT
        additional_notes: TEXT
    """
    _call(
        conn,
        "insert_summary",
        {
        "p_summary_id": summary_id,
        "p_session_id": session_id,
        "p_patient_id": patient_id,
        "p_chief_complaint": chief_complaint,
        "p_current_symptoms": current_symptoms,
        "p_allergies": allergies,
        "p_past_history": past_history,
        "p_additional_notes": additional_notes,
        },
        commit=commit,
    )


def insert_session_summary(
    conn: Connection,
    *,
    session_id: str | None = None,
    patient_id: str | None = None,
    patient_name: str | None = None,
    json_filename: str | None = None,
    language: str | None = None,
    message_count: int | None = None,
    generated_at: datetime | None = None,
    chief_complaint: str | None = None,
    history_of_present_illness: str | None = None,
    current_symptoms: str | None = None,
    allergies: str | None = None,
    current_medications: str | None = None,
    past_medical_history: str | None = None,
    family_history: str | None = None,
    social_history: str | None = None,
    other_notes: str | None = None,
    summary_text: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    icd_10: str | None = None,
    icd_10_details: dict | list | None = None,
    icd_10_status: str | None = None,
    cpt: str | None = None,
    cpt_details: dict | list | None = None,
    cpt_status: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_session_summary(...): insert one row into `session_summaries`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        session_id: VARCHAR(64)
        patient_id: VARCHAR(128)
        patient_name: TEXT
        json_filename: TEXT
        language: TEXT
        message_count: INTEGER
        generated_at: TIMESTAMPTZ
        chief_complaint: TEXT
        history_of_present_illness: TEXT
        current_symptoms: TEXT
        allergies: TEXT
        current_medications: TEXT
        past_medical_history: TEXT
        family_history: TEXT
        social_history: TEXT
        other_notes: TEXT
        summary_text: TEXT
        llm_provider: TEXT
        llm_model: TEXT
        icd_10: TEXT
        icd_10_details: JSONB
        icd_10_status: TEXT
        cpt: TEXT
        cpt_details: JSONB
        cpt_status: TEXT
    """
    _call(
        conn,
        "insert_session_summary",
        {
        "p_session_id": session_id,
        "p_patient_id": patient_id,
        "p_patient_name": patient_name,
        "p_json_filename": json_filename,
        "p_language": language,
        "p_message_count": message_count,
        "p_generated_at": generated_at,
        "p_chief_complaint": chief_complaint,
        "p_history_of_present_illness": history_of_present_illness,
        "p_current_symptoms": current_symptoms,
        "p_allergies": allergies,
        "p_current_medications": current_medications,
        "p_past_medical_history": past_medical_history,
        "p_family_history": family_history,
        "p_social_history": social_history,
        "p_other_notes": other_notes,
        "p_summary_text": summary_text,
        "p_llm_provider": llm_provider,
        "p_llm_model": llm_model,
        "p_icd_10": icd_10,
        "p_icd_10_details": Jsonb(icd_10_details) if icd_10_details is not None else None,
        "p_icd_10_status": icd_10_status,
        "p_cpt": cpt,
        "p_cpt_details": Jsonb(cpt_details) if cpt_details is not None else None,
        "p_cpt_status": cpt_status,
        },
        commit=commit,
    )


def insert_session_registry(
    conn: Connection,
    *,
    session_id: str | None = None,
    patient_id: str | None = None,
    patient_name: str | None = None,
    timestamp: datetime | None = None,
    conversation_table: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_session_registry(...): insert one row into `session_registry`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        session_id: VARCHAR(64)
        patient_id: VARCHAR(128)
        patient_name: TEXT
        timestamp: TIMESTAMPTZ
        conversation_table: TEXT
    """
    _call(
        conn,
        "insert_session_registry",
        {
        "p_session_id": session_id,
        "p_patient_id": patient_id,
        "p_patient_name": patient_name,
        "p_timestamp": timestamp,
        "p_conversation_table": conversation_table,
        },
        commit=commit,
    )


def insert_provider(
    conn: Connection,
    *,
    full_name: str | None = None,
    specialty: str | None = None,
    npi: str | None = None,
    location: str | None = None,
    is_active: bool | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_provider(...): insert one row into `providers`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        full_name: VARCHAR(200)
        specialty: VARCHAR(120)
        npi: VARCHAR(20)
        location: VARCHAR(200)
        is_active: BOOLEAN
    """
    _call(
        conn,
        "insert_provider",
        {
        "p_full_name": full_name,
        "p_specialty": specialty,
        "p_npi": npi,
        "p_location": location,
        "p_is_active": is_active,
        },
        commit=commit,
    )


def insert_provider_network(
    conn: Connection,
    *,
    provider_id: int | None = None,
    payer_id: int | None = None,
    in_network: bool | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_provider_network(...): insert one row into `provider_networks`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        provider_id: INTEGER
        payer_id: INTEGER
        in_network: BOOLEAN
    """
    _call(
        conn,
        "insert_provider_network",
        {
        "p_provider_id": provider_id,
        "p_payer_id": payer_id,
        "p_in_network": in_network,
        },
        commit=commit,
    )


def insert_provider_availability(
    conn: Connection,
    *,
    provider_id: int | None = None,
    weekday: int | None = None,
    start_time: time | None = None,
    end_time: time | None = None,
    slot_minutes: int | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_provider_availability(...): insert one row into `provider_availability`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        provider_id: INTEGER
        weekday: INTEGER
        start_time: TIME
        end_time: TIME
        slot_minutes: INTEGER
    """
    _call(
        conn,
        "insert_provider_availability",
        {
        "p_provider_id": provider_id,
        "p_weekday": weekday,
        "p_start_time": start_time,
        "p_end_time": end_time,
        "p_slot_minutes": slot_minutes,
        },
        commit=commit,
    )


def insert_portal_request(
    conn: Connection,
    *,
    reference: str | None = None,
    status: str | None = None,
    intent: str | None = None,
    source_email_id: int | None = None,
    extracted: dict | list | None = None,
    patient_id: int | None = None,
    match_confidence: float | None = None,
    match_details: dict | list | None = None,
    provider_id: int | None = None,
    ai_confidence: float | None = None,
    ai_summary: str | None = None,
    decision: str | None = None,
    review_reasons: dict | list | None = None,
    draft_response: str | None = None,
    appointment_id: int | None = None,
    assigned_to: str | None = None,
    priority: str | None = None,
    llm_provider: str | None = None,
    resolution_note: str | None = None,
    resolved_by: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_portal_request(...): insert one row into `portal_requests`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        reference: VARCHAR(20)
        status: VARCHAR(20)
        intent: VARCHAR(30)
        source_email_id: INTEGER
        extracted: JSONB
        patient_id: INTEGER
        match_confidence: DOUBLE PRECISION
        match_details: JSONB
        provider_id: INTEGER
        ai_confidence: DOUBLE PRECISION
        ai_summary: TEXT
        decision: VARCHAR(20)
        review_reasons: JSONB
        draft_response: TEXT
        appointment_id: INTEGER
        assigned_to: VARCHAR(120)
        priority: VARCHAR(10)
        llm_provider: VARCHAR(40)
        resolution_note: TEXT
        resolved_by: VARCHAR(120)
    """
    _call(
        conn,
        "insert_portal_request",
        {
        "p_reference": reference,
        "p_status": status,
        "p_intent": intent,
        "p_source_email_id": source_email_id,
        "p_extracted": Jsonb(extracted) if extracted is not None else None,
        "p_patient_id": patient_id,
        "p_match_confidence": match_confidence,
        "p_match_details": Jsonb(match_details) if match_details is not None else None,
        "p_provider_id": provider_id,
        "p_ai_confidence": ai_confidence,
        "p_ai_summary": ai_summary,
        "p_decision": decision,
        "p_review_reasons": Jsonb(review_reasons) if review_reasons is not None else None,
        "p_draft_response": draft_response,
        "p_appointment_id": appointment_id,
        "p_assigned_to": assigned_to,
        "p_priority": priority,
        "p_llm_provider": llm_provider,
        "p_resolution_note": resolution_note,
        "p_resolved_by": resolved_by,
        },
        commit=commit,
    )


def insert_phi_release(
    conn: Connection,
    *,
    patient_id: int | None = None,
    recipient_name: str | None = None,
    relationship_to_patient: str | None = None,
    phone: str | None = None,
    may_discuss_appointments: bool | None = None,
    may_discuss_billing: bool | None = None,
    may_discuss_clinical: bool | None = None,
    signed_on: date | None = None,
    expires_on: date | None = None,
    revoked_at: datetime | None = None,
    created_by: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_phi_release(...): insert one row into `phi_releases`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        patient_id: INTEGER
        recipient_name: VARCHAR(200)
        relationship_to_patient: VARCHAR(50)
        phone: VARCHAR(30)
        may_discuss_appointments: BOOLEAN
        may_discuss_billing: BOOLEAN
        may_discuss_clinical: BOOLEAN
        signed_on: DATE
        expires_on: DATE
        revoked_at: TIMESTAMPTZ
        created_by: VARCHAR(120)
    """
    _call(
        conn,
        "insert_phi_release",
        {
        "p_patient_id": patient_id,
        "p_recipient_name": recipient_name,
        "p_relationship_to_patient": relationship_to_patient,
        "p_phone": phone,
        "p_may_discuss_appointments": may_discuss_appointments,
        "p_may_discuss_billing": may_discuss_billing,
        "p_may_discuss_clinical": may_discuss_clinical,
        "p_signed_on": signed_on,
        "p_expires_on": expires_on,
        "p_revoked_at": revoked_at,
        "p_created_by": created_by,
        },
        commit=commit,
    )


def insert_payer(
    conn: Connection,
    *,
    name: str | None = None,
    payer_code: str | None = None,
    aliases: dict | list | None = None,
    electronic_payer_id: str | None = None,
    financial_category: str | None = None,
    claims_addresses: dict | list | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_payer(...): insert one row into `payers`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        name: VARCHAR(200)
        payer_code: VARCHAR(40)
        aliases: JSONB
        electronic_payer_id: VARCHAR(20)
        financial_category: VARCHAR(40)
        claims_addresses: JSONB
    """
    _call(
        conn,
        "insert_payer",
        {
        "p_name": name,
        "p_payer_code": payer_code,
        "p_aliases": Jsonb(aliases) if aliases is not None else None,
        "p_electronic_payer_id": electronic_payer_id,
        "p_financial_category": financial_category,
        "p_claims_addresses": Jsonb(claims_addresses) if claims_addresses is not None else None,
        },
        commit=commit,
    )


def insert_payer_plan(
    conn: Connection,
    *,
    payer_id: int | None = None,
    name: str | None = None,
    plan_type: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_payer_plan(...): insert one row into `payer_plans`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        payer_id: INTEGER
        name: VARCHAR(200)
        plan_type: VARCHAR(20)
    """
    _call(
        conn,
        "insert_payer_plan",
        {
        "p_payer_id": payer_id,
        "p_name": name,
        "p_plan_type": plan_type,
        },
        commit=commit,
    )


def insert_patient(
    conn: Connection,
    *,
    mrn: str | None = None,
    prefix: str | None = None,
    first_name: str | None = None,
    middle_name: str | None = None,
    last_name: str | None = None,
    suffix: str | None = None,
    preferred_name: str | None = None,
    previous_name: str | None = None,
    is_test_patient: bool | None = None,
    marital_status: str | None = None,
    date_of_birth: date | None = None,
    sex: str | None = None,
    ssn_encrypted: str | None = None,
    ssn_last4: str | None = None,
    drivers_license_encrypted: str | None = None,
    drivers_license_last4: str | None = None,
    drivers_license_state: str | None = None,
    phone: str | None = None,
    home_phone: str | None = None,
    work_phone: str | None = None,
    preferred_contact_method: str | None = None,
    email: str | None = None,
    address_line1: str | None = None,
    address_line2: str | None = None,
    city: str | None = None,
    state: str | None = None,
    postal_code: str | None = None,
    preferred_provider_id: int | None = None,
    notes: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_patient(...): insert one row into `patients`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        mrn: VARCHAR(30)
        prefix: VARCHAR(20)
        first_name: VARCHAR(100)
        middle_name: VARCHAR(100)
        last_name: VARCHAR(100)
        suffix: VARCHAR(20)
        preferred_name: VARCHAR(100)
        previous_name: VARCHAR(200)
        is_test_patient: BOOLEAN
        marital_status: VARCHAR(30)
        date_of_birth: DATE
        sex: VARCHAR(20)
        ssn_encrypted: TEXT
        ssn_last4: VARCHAR(4)
        drivers_license_encrypted: TEXT
        drivers_license_last4: VARCHAR(4)
        drivers_license_state: VARCHAR(2)
        phone: VARCHAR(30)
        home_phone: VARCHAR(30)
        work_phone: VARCHAR(30)
        preferred_contact_method: VARCHAR(20)
        email: VARCHAR(255)
        address_line1: VARCHAR(200)
        address_line2: VARCHAR(200)
        city: VARCHAR(100)
        state: VARCHAR(40)
        postal_code: VARCHAR(20)
        preferred_provider_id: INTEGER
        notes: TEXT
    """
    _call(
        conn,
        "insert_patient",
        {
        "p_mrn": mrn,
        "p_prefix": prefix,
        "p_first_name": first_name,
        "p_middle_name": middle_name,
        "p_last_name": last_name,
        "p_suffix": suffix,
        "p_preferred_name": preferred_name,
        "p_previous_name": previous_name,
        "p_is_test_patient": is_test_patient,
        "p_marital_status": marital_status,
        "p_date_of_birth": date_of_birth,
        "p_sex": sex,
        "p_ssn_encrypted": ssn_encrypted,
        "p_ssn_last4": ssn_last4,
        "p_drivers_license_encrypted": drivers_license_encrypted,
        "p_drivers_license_last4": drivers_license_last4,
        "p_drivers_license_state": drivers_license_state,
        "p_phone": phone,
        "p_home_phone": home_phone,
        "p_work_phone": work_phone,
        "p_preferred_contact_method": preferred_contact_method,
        "p_email": email,
        "p_address_line1": address_line1,
        "p_address_line2": address_line2,
        "p_city": city,
        "p_state": state,
        "p_postal_code": postal_code,
        "p_preferred_provider_id": preferred_provider_id,
        "p_notes": notes,
        },
        commit=commit,
    )


def insert_patient_document(
    conn: Connection,
    *,
    patient_id: int | None = None,
    kind: str | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    storage_path: str | None = None,
    size_bytes: int | None = None,
    uploaded_by: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_patient_document(...): insert one row into `patient_documents`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        patient_id: INTEGER
        kind: VARCHAR(40)
        filename: VARCHAR(255)
        content_type: VARCHAR(100)
        storage_path: VARCHAR(500)
        size_bytes: INTEGER
        uploaded_by: VARCHAR(120)
    """
    _call(
        conn,
        "insert_patient_document",
        {
        "p_patient_id": patient_id,
        "p_kind": kind,
        "p_filename": filename,
        "p_content_type": content_type,
        "p_storage_path": storage_path,
        "p_size_bytes": size_bytes,
        "p_uploaded_by": uploaded_by,
        },
        commit=commit,
    )


def insert_password_reset_token(
    conn: Connection,
    *,
    user_id: int | None = None,
    token_hash: str | None = None,
    expires_at: datetime | None = None,
    requested_ip: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_password_reset_token(...): insert one row into `password_reset_tokens`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        user_id: INTEGER
        token_hash: VARCHAR(64)
        expires_at: TIMESTAMPTZ
        requested_ip: VARCHAR(64)
    """
    _call(
        conn,
        "insert_password_reset_token",
        {
        "p_user_id": user_id,
        "p_token_hash": token_hash,
        "p_expires_at": expires_at,
        "p_requested_ip": requested_ip,
        },
        commit=commit,
    )


def insert_oauth_state(
    conn: Connection,
    *,
    state_hash: str | None = None,
    provider: str | None = None,
    flow: str | None = None,
    code_verifier_encrypted: str | None = None,
    created_by: str | None = None,
    expires_at: datetime | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_oauth_state(...): insert one row into `oauth_states`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        state_hash: VARCHAR(64)
        provider: VARCHAR(30)
        flow: VARCHAR(20)
        code_verifier_encrypted: TEXT
        created_by: VARCHAR(120)
        expires_at: TIMESTAMPTZ
    """
    _call(
        conn,
        "insert_oauth_state",
        {
        "p_state_hash": state_hash,
        "p_provider": provider,
        "p_flow": flow,
        "p_code_verifier_encrypted": code_verifier_encrypted,
        "p_created_by": created_by,
        "p_expires_at": expires_at,
        },
        commit=commit,
    )


def insert_integration_mailbox(
    conn: Connection,
    *,
    connection_id: int | None = None,
    address: str | None = None,
    display_name: str | None = None,
    folder: str | None = None,
    enabled: bool | None = None,
    process_inbound: bool | None = None,
    reply_from_same_mailbox: bool | None = None,
    mark_as_read: bool | None = None,
    is_default_sender: bool | None = None,
    created_by: str | None = None,
    skip_sender_domains: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_integration_mailbox(...): insert one row into `integration_mailboxes`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        connection_id: INTEGER
        address: VARCHAR(255)
        display_name: VARCHAR(200)
        folder: VARCHAR(200)
        enabled: BOOLEAN
        process_inbound: BOOLEAN
        reply_from_same_mailbox: BOOLEAN
        mark_as_read: BOOLEAN
        is_default_sender: BOOLEAN
        created_by: VARCHAR(120)
        skip_sender_domains: TEXT
    """
    _call(
        conn,
        "insert_integration_mailbox",
        {
        "p_connection_id": connection_id,
        "p_address": address,
        "p_display_name": display_name,
        "p_folder": folder,
        "p_enabled": enabled,
        "p_process_inbound": process_inbound,
        "p_reply_from_same_mailbox": reply_from_same_mailbox,
        "p_mark_as_read": mark_as_read,
        "p_is_default_sender": is_default_sender,
        "p_created_by": created_by,
        "p_skip_sender_domains": skip_sender_domains,
        },
        commit=commit,
    )


def insert_integration_connection(
    conn: Connection,
    *,
    provider: str | None = None,
    auth_mode: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
    client_secret_encrypted: str | None = None,
    client_secret_hint: str | None = None,
    status: str | None = None,
    connected_account: str | None = None,
    connected_name: str | None = None,
    scopes: str | None = None,
    refresh_token_encrypted: str | None = None,
    token_expires_at: datetime | None = None,
    poll_interval_seconds: int | None = None,
    connected_by: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_integration_connection(...): insert one row into `integration_connections`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        provider: VARCHAR(30)
        auth_mode: VARCHAR(20)
        tenant_id: VARCHAR(100)
        client_id: VARCHAR(100)
        client_secret_encrypted: TEXT
        client_secret_hint: VARCHAR(10)
        status: VARCHAR(30)
        connected_account: VARCHAR(255)
        connected_name: VARCHAR(255)
        scopes: TEXT
        refresh_token_encrypted: TEXT
        token_expires_at: TIMESTAMPTZ
        poll_interval_seconds: INTEGER
        connected_by: VARCHAR(120)
    """
    _call(
        conn,
        "insert_integration_connection",
        {
        "p_provider": provider,
        "p_auth_mode": auth_mode,
        "p_tenant_id": tenant_id,
        "p_client_id": client_id,
        "p_client_secret_encrypted": client_secret_encrypted,
        "p_client_secret_hint": client_secret_hint,
        "p_status": status,
        "p_connected_account": connected_account,
        "p_connected_name": connected_name,
        "p_scopes": scopes,
        "p_refresh_token_encrypted": refresh_token_encrypted,
        "p_token_expires_at": token_expires_at,
        "p_poll_interval_seconds": poll_interval_seconds,
        "p_connected_by": connected_by,
        },
        commit=commit,
    )


def insert_insurance_policy(
    conn: Connection,
    *,
    patient_id: int | None = None,
    payer_id: int | None = None,
    member_id: str | None = None,
    group_number: str | None = None,
    subscriber_name: str | None = None,
    relationship_to_subscriber: str | None = None,
    is_primary: bool | None = None,
    is_active: bool | None = None,
    coverage_order: str | None = None,
    plan_name: str | None = None,
    policy_type: str | None = None,
    claims_address: str | None = None,
    notes: str | None = None,
    requires_referral: bool | None = None,
    requires_precert_inpatient: bool | None = None,
    requires_preauth_outpatient: bool | None = None,
    holder_first_name: str | None = None,
    holder_last_name: str | None = None,
    holder_date_of_birth: date | None = None,
    eligibility_status: str | None = None,
    copay_amount: Decimal | float | None = None,
    coinsurance_pct: Decimal | float | None = None,
    deductible: Decimal | float | None = None,
    remaining_deductible: Decimal | float | None = None,
    out_of_pocket: Decimal | float | None = None,
    remaining_out_of_pocket: Decimal | float | None = None,
    effective_date: date | None = None,
    end_date: date | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_insurance_policy(...): insert one row into `insurance_policies`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        patient_id: INTEGER
        payer_id: INTEGER
        member_id: VARCHAR(60)
        group_number: VARCHAR(60)
        subscriber_name: VARCHAR(200)
        relationship_to_subscriber: VARCHAR(30)
        is_primary: BOOLEAN
        is_active: BOOLEAN
        coverage_order: VARCHAR(10)
        plan_name: VARCHAR(200)
        policy_type: VARCHAR(20)
        claims_address: VARCHAR(300)
        notes: VARCHAR(500)
        requires_referral: BOOLEAN
        requires_precert_inpatient: BOOLEAN
        requires_preauth_outpatient: BOOLEAN
        holder_first_name: VARCHAR(100)
        holder_last_name: VARCHAR(100)
        holder_date_of_birth: DATE
        eligibility_status: VARCHAR(20)
        copay_amount: NUMERIC(10,2)
        coinsurance_pct: NUMERIC(5,2)
        deductible: NUMERIC(10,2)
        remaining_deductible: NUMERIC(10,2)
        out_of_pocket: NUMERIC(10,2)
        remaining_out_of_pocket: NUMERIC(10,2)
        effective_date: DATE
        end_date: DATE
    """
    _call(
        conn,
        "insert_insurance_policy",
        {
        "p_patient_id": patient_id,
        "p_payer_id": payer_id,
        "p_member_id": member_id,
        "p_group_number": group_number,
        "p_subscriber_name": subscriber_name,
        "p_relationship_to_subscriber": relationship_to_subscriber,
        "p_is_primary": is_primary,
        "p_is_active": is_active,
        "p_coverage_order": coverage_order,
        "p_plan_name": plan_name,
        "p_policy_type": policy_type,
        "p_claims_address": claims_address,
        "p_notes": notes,
        "p_requires_referral": requires_referral,
        "p_requires_precert_inpatient": requires_precert_inpatient,
        "p_requires_preauth_outpatient": requires_preauth_outpatient,
        "p_holder_first_name": holder_first_name,
        "p_holder_last_name": holder_last_name,
        "p_holder_date_of_birth": holder_date_of_birth,
        "p_eligibility_status": eligibility_status,
        "p_copay_amount": copay_amount,
        "p_coinsurance_pct": coinsurance_pct,
        "p_deductible": deductible,
        "p_remaining_deductible": remaining_deductible,
        "p_out_of_pocket": out_of_pocket,
        "p_remaining_out_of_pocket": remaining_out_of_pocket,
        "p_effective_date": effective_date,
        "p_end_date": end_date,
        },
        commit=commit,
    )


def insert_guarantor(
    conn: Connection,
    *,
    patient_id: int | None = None,
    same_as_patient: bool | None = None,
    relationship_to_patient: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    date_of_birth: date | None = None,
    phone: str | None = None,
    email: str | None = None,
    address_line1: str | None = None,
    city: str | None = None,
    state: str | None = None,
    postal_code: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_guarantor(...): insert one row into `guarantors`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        patient_id: INTEGER
        same_as_patient: BOOLEAN
        relationship_to_patient: VARCHAR(30)
        first_name: VARCHAR(100)
        last_name: VARCHAR(100)
        date_of_birth: DATE
        phone: VARCHAR(30)
        email: VARCHAR(255)
        address_line1: VARCHAR(200)
        city: VARCHAR(100)
        state: VARCHAR(40)
        postal_code: VARCHAR(20)
    """
    _call(
        conn,
        "insert_guarantor",
        {
        "p_patient_id": patient_id,
        "p_same_as_patient": same_as_patient,
        "p_relationship_to_patient": relationship_to_patient,
        "p_first_name": first_name,
        "p_last_name": last_name,
        "p_date_of_birth": date_of_birth,
        "p_phone": phone,
        "p_email": email,
        "p_address_line1": address_line1,
        "p_city": city,
        "p_state": state,
        "p_postal_code": postal_code,
        },
        commit=commit,
    )


def insert_emr_export(
    conn: Connection,
    *,
    export_id: str | None = None,
    session_id: str | None = None,
    patient_id: str | None = None,
    patient_name: str | None = None,
    exported_at: datetime | None = None,
    export_file: str | None = None,
    payload: dict | list | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_emr_export(...): insert one row into `emr_exports`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        export_id: VARCHAR(64)
        session_id: VARCHAR(64)
        patient_id: VARCHAR(128)
        patient_name: TEXT
        exported_at: TIMESTAMPTZ
        export_file: TEXT
        payload: JSONB
    """
    _call(
        conn,
        "insert_emr_export",
        {
        "p_export_id": export_id,
        "p_session_id": session_id,
        "p_patient_id": patient_id,
        "p_patient_name": patient_name,
        "p_exported_at": exported_at,
        "p_export_file": export_file,
        "p_payload": Jsonb(payload) if payload is not None else None,
        },
        commit=commit,
    )


def insert_email_message(
    conn: Connection,
    *,
    message_id: str | None = None,
    direction: str | None = None,
    source: str | None = None,
    from_address: str | None = None,
    from_name: str | None = None,
    to_address: str | None = None,
    subject: str | None = None,
    body_text: str | None = None,
    in_reply_to: str | None = None,
    request_id: int | None = None,
    send_status: str | None = None,
    send_error: str | None = None,
    sent_by: str | None = None,
    received_at: datetime | None = None,
    delivery_method: str | None = None,
    delivered_at: datetime | None = None,
    mailbox_id: int | None = None,
    provider_message_id: str | None = None,
    conversation_id: str | None = None,
    body_raw: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_email_message(...): insert one row into `email_messages`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        message_id: VARCHAR(500)
        direction: VARCHAR(10)
        source: VARCHAR(20)
        from_address: VARCHAR(255)
        from_name: VARCHAR(200)
        to_address: VARCHAR(255)
        subject: VARCHAR(500)
        body_text: TEXT
        in_reply_to: VARCHAR(500)
        request_id: INTEGER
        send_status: VARCHAR(20)
        send_error: TEXT
        sent_by: VARCHAR(120)
        received_at: TIMESTAMPTZ
        delivery_method: VARCHAR(20)
        delivered_at: TIMESTAMPTZ
        mailbox_id: INTEGER
        provider_message_id: VARCHAR(300)
        conversation_id: VARCHAR(300)
        body_raw: TEXT
    """
    _call(
        conn,
        "insert_email_message",
        {
        "p_message_id": message_id,
        "p_direction": direction,
        "p_source": source,
        "p_from_address": from_address,
        "p_from_name": from_name,
        "p_to_address": to_address,
        "p_subject": subject,
        "p_body_text": body_text,
        "p_in_reply_to": in_reply_to,
        "p_request_id": request_id,
        "p_send_status": send_status,
        "p_send_error": send_error,
        "p_sent_by": sent_by,
        "p_received_at": received_at,
        "p_delivery_method": delivery_method,
        "p_delivered_at": delivered_at,
        "p_mailbox_id": mailbox_id,
        "p_provider_message_id": provider_message_id,
        "p_conversation_id": conversation_id,
        "p_body_raw": body_raw,
        },
        commit=commit,
    )


def insert_eligibility_check(
    conn: Connection,
    *,
    patient_id: int | None = None,
    policy_id: int | None = None,
    provider_id: int | None = None,
    request_id: int | None = None,
    coverage_status: str | None = None,
    in_network: bool | None = None,
    plan_name: str | None = None,
    copay: str | None = None,
    message: str | None = None,
    raw_response: dict | list | None = None,
    checked_by: str | None = None,
    checked_at: datetime | None = None,
    date_of_service: date | None = None,
    npi_type: str | None = None,
    npi: str | None = None,
    payer_member_name: str | None = None,
    payer_member_dob: date | None = None,
    payer_member_id: str | None = None,
    plan_type: str | None = None,
    plan_start: date | None = None,
    plan_end: date | None = None,
    eligibility_start: date | None = None,
    eligibility_end: date | None = None,
    raw_x12: str | None = None,
    discrepancies: dict | list | None = None,
    ai_summary: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_eligibility_check(...): insert one row into `eligibility_checks`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        patient_id: INTEGER
        policy_id: INTEGER
        provider_id: INTEGER
        request_id: INTEGER
        coverage_status: VARCHAR(30)
        in_network: BOOLEAN
        plan_name: VARCHAR(200)
        copay: VARCHAR(40)
        message: TEXT
        raw_response: JSONB
        checked_by: VARCHAR(120)
        checked_at: TIMESTAMPTZ
        date_of_service: DATE
        npi_type: VARCHAR(12)
        npi: VARCHAR(20)
        payer_member_name: VARCHAR(200)
        payer_member_dob: DATE
        payer_member_id: VARCHAR(60)
        plan_type: VARCHAR(20)
        plan_start: DATE
        plan_end: DATE
        eligibility_start: DATE
        eligibility_end: DATE
        raw_x12: TEXT
        discrepancies: JSONB
        ai_summary: TEXT
    """
    _call(
        conn,
        "insert_eligibility_check",
        {
        "p_patient_id": patient_id,
        "p_policy_id": policy_id,
        "p_provider_id": provider_id,
        "p_request_id": request_id,
        "p_coverage_status": coverage_status,
        "p_in_network": in_network,
        "p_plan_name": plan_name,
        "p_copay": copay,
        "p_message": message,
        "p_raw_response": Jsonb(raw_response) if raw_response is not None else None,
        "p_checked_by": checked_by,
        "p_checked_at": checked_at,
        "p_date_of_service": date_of_service,
        "p_npi_type": npi_type,
        "p_npi": npi,
        "p_payer_member_name": payer_member_name,
        "p_payer_member_dob": payer_member_dob,
        "p_payer_member_id": payer_member_id,
        "p_plan_type": plan_type,
        "p_plan_start": plan_start,
        "p_plan_end": plan_end,
        "p_eligibility_start": eligibility_start,
        "p_eligibility_end": eligibility_end,
        "p_raw_x12": raw_x12,
        "p_discrepancies": Jsonb(discrepancies) if discrepancies is not None else None,
        "p_ai_summary": ai_summary,
        },
        commit=commit,
    )


def insert_eligibility_benefit(
    conn: Connection,
    *,
    check_id: int | None = None,
    network: str | None = None,
    category: str | None = None,
    coverage_level: str | None = None,
    service_type_code: str | None = None,
    service_type: str | None = None,
    amount: Decimal | float | None = None,
    percent: Decimal | float | None = None,
    time_period: str | None = None,
    auth_required: bool | None = None,
    message: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_eligibility_benefit(...): insert one row into `eligibility_benefits`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        check_id: INTEGER
        network: VARCHAR(4)
        category: VARCHAR(40)
        coverage_level: VARCHAR(12)
        service_type_code: VARCHAR(4)
        service_type: VARCHAR(120)
        amount: NUMERIC(10,2)
        percent: NUMERIC(5,2)
        time_period: VARCHAR(30)
        auth_required: BOOLEAN
        message: VARCHAR(300)
    """
    _call(
        conn,
        "insert_eligibility_benefit",
        {
        "p_check_id": check_id,
        "p_network": network,
        "p_category": category,
        "p_coverage_level": coverage_level,
        "p_service_type_code": service_type_code,
        "p_service_type": service_type,
        "p_amount": amount,
        "p_percent": percent,
        "p_time_period": time_period,
        "p_auth_required": auth_required,
        "p_message": message,
        },
        commit=commit,
    )


def insert_appointment(
    conn: Connection,
    *,
    appointment_id: str | None = None,
    session_id: str | None = None,
    patient_id: str | None = None,
    icd_10: str | None = None,
    patient_name: str | None = None,
    scheduled_for: datetime | None = None,
    duration_minutes: int | None = None,
    clinician: str | None = None,
    reason: str | None = None,
    status: str | None = None,
    booked_via: str | None = None,
    cpt: str | None = None,
    patient_email: str | None = None,
    email_status: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_appointment(...): insert one row into `appointments`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        appointment_id: VARCHAR(64)
        session_id: VARCHAR(64)
        patient_id: VARCHAR(128)
        icd_10: TEXT
        patient_name: TEXT
        scheduled_for: TIMESTAMPTZ
        duration_minutes: INTEGER
        clinician: TEXT
        reason: TEXT
        status: TEXT
        booked_via: TEXT
        cpt: TEXT
        patient_email: TEXT
        email_status: TEXT
    """
    _call(
        conn,
        "insert_appointment",
        {
        "p_appointment_id": appointment_id,
        "p_session_id": session_id,
        "p_patient_id": patient_id,
        "p_icd_10": icd_10,
        "p_patient_name": patient_name,
        "p_scheduled_for": scheduled_for,
        "p_duration_minutes": duration_minutes,
        "p_clinician": clinician,
        "p_reason": reason,
        "p_status": status,
        "p_booked_via": booked_via,
        "p_cpt": cpt,
        "p_patient_email": patient_email,
        "p_email_status": email_status,
        },
        commit=commit,
    )


def insert_app_setting(
    conn: Connection,
    *,
    key: str | None = None,
    value: dict | list | None = None,
    updated_by: str | None = None,
    commit: bool = True,
) -> None:
    """CALL public.insert_app_setting(...): insert one row into `app_settings`.

    Every argument is optional here; the procedure decides which are required and
    raises ProcedureError with its own message when one is missing or invalid.

        key: VARCHAR(100)
        value: JSONB
        updated_by: VARCHAR(120)
    """
    _call(
        conn,
        "insert_app_setting",
        {
        "p_key": key,
        "p_value": Jsonb(value) if value is not None else None,
        "p_updated_by": updated_by,
        },
        commit=commit,
    )
