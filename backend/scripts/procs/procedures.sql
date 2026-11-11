--1
CREATE OR REPLACE PROCEDURE public.insert_user(
    IN p_email VARCHAR(255),
    IN p_full_name VARCHAR(200),
    IN p_role VARCHAR(30),
    IN p_password_hash VARCHAR(255),
    IN p_is_active BOOLEAN
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_email IS NULL OR TRIM(p_email) = '' THEN
        RAISE EXCEPTION 'Email is required';
    END IF;

    IF p_full_name IS NULL OR TRIM(p_full_name) = '' THEN
        RAISE EXCEPTION 'Full name is required';
    END IF;

    IF p_role IS NULL OR TRIM(p_role) = '' THEN
        RAISE EXCEPTION 'Role is required';
    END IF;

    IF p_password_hash IS NULL OR TRIM(p_password_hash) = '' THEN
        RAISE EXCEPTION 'Password hash is required';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.users
        WHERE LOWER(email) = LOWER(TRIM(p_email))
    ) THEN
        RAISE EXCEPTION 'User with email % already exists',
            TRIM(p_email);
    END IF;

    INSERT INTO public.users (
        email,
        full_name,
        "role",
        password_hash,
        is_active,
        created_at,
        password_changed_at,
        token_version,
        failed_logins
    )
    VALUES (
        LOWER(TRIM(p_email)),
        TRIM(p_full_name),
        TRIM(p_role),
        p_password_hash,
        COALESCE(p_is_active, TRUE),
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP,
        0,
        0
    );
END;
$$;
-------------------------------------------------------------------------
--2 
CREATE OR REPLACE PROCEDURE public.insert_summary(
    IN p_summary_id UUID,
    IN p_session_id UUID,
    IN p_patient_id BIGINT,
    IN p_chief_complaint TEXT,
    IN p_current_symptoms TEXT,
    IN p_allergies TEXT,
    IN p_past_history TEXT,
    IN p_additional_notes TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_summary_id IS NULL THEN
        RAISE EXCEPTION 'Summary ID is required';
    END IF;

    IF p_session_id IS NULL THEN
        RAISE EXCEPTION 'Session ID is required';
    END IF;

    IF p_patient_id IS NULL THEN
        RAISE EXCEPTION 'Patient ID is required';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.summaries
        WHERE summary_id = p_summary_id
    ) THEN
        RAISE EXCEPTION 'Summary ID % already exists',
            p_summary_id;
    END IF;

    INSERT INTO public.summaries (
        summary_id,
        session_id,
        patient_id,
        chief_complaint,
        current_symptoms,
        allergies,
        past_history,
        additional_notes,
        created_at
    )
    VALUES (
        p_summary_id,
        p_session_id,
        p_patient_id,
        p_chief_complaint,
        p_current_symptoms,
        p_allergies,
        p_past_history,
        p_additional_notes,
        CURRENT_TIMESTAMP
    );
END;
$$;
------------------------------------------------------------------------------------------------
--3
CREATE OR REPLACE PROCEDURE public.insert_session_summary(
    IN p_session_id VARCHAR(64),
    IN p_patient_id VARCHAR(128),
    IN p_patient_name TEXT,
    IN p_json_filename TEXT,
    IN p_language TEXT,
    IN p_message_count INTEGER,
    IN p_generated_at TIMESTAMPTZ,
    IN p_chief_complaint TEXT,
    IN p_history_of_present_illness TEXT,
    IN p_current_symptoms TEXT,
    IN p_allergies TEXT,
    IN p_current_medications TEXT,
    IN p_past_medical_history TEXT,
    IN p_family_history TEXT,
    IN p_social_history TEXT,
    IN p_other_notes TEXT,
    IN p_summary_text TEXT,
    IN p_llm_provider TEXT,
    IN p_llm_model TEXT,
    IN p_icd_10 TEXT,
    IN p_icd_10_details JSONB,
    IN p_icd_10_status TEXT,
    IN p_cpt TEXT,
    IN p_cpt_details JSONB,
    IN p_cpt_status TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_session_id IS NULL OR TRIM(p_session_id) = '' THEN
        RAISE EXCEPTION 'Session ID is required';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.session_summaries
        WHERE session_id = TRIM(p_session_id)
    ) THEN
        RAISE EXCEPTION 'Session ID % already exists',
            TRIM(p_session_id);
    END IF;

    INSERT INTO public.session_summaries (
        session_id,
        patient_id,
        patient_name,
        json_filename,
        language,
        message_count,
        generated_at,
        chief_complaint,
        history_of_present_illness,
        current_symptoms,
        allergies,
        current_medications,
        past_medical_history,
        family_history,
        social_history,
        other_notes,
        summary_text,
        created_at,
        updated_at,
        llm_provider,
        llm_model,
        icd_10,
        icd_10_details,
        icd_10_status,
        cpt,
        cpt_details,
        cpt_status
    )
    VALUES (
        TRIM(p_session_id),
        p_patient_id,
        p_patient_name,
        p_json_filename,
        p_language,
        p_message_count,
        p_generated_at,
        p_chief_complaint,
        p_history_of_present_illness,
        p_current_symptoms,
        p_allergies,
        p_current_medications,
        p_past_medical_history,
        p_family_history,
        p_social_history,
        p_other_notes,
        p_summary_text,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP,
        p_llm_provider,
        p_llm_model,
        p_icd_10,
        p_icd_10_details,
        p_icd_10_status,
        p_cpt,
        p_cpt_details,
        p_cpt_status
    );
END;
$$;
---------------------------------------------------------------------------
--4
CREATE OR REPLACE PROCEDURE public.insert_session_registry(
    IN p_session_id VARCHAR(64),
    IN p_patient_id VARCHAR(128),
    IN p_patient_name TEXT,
    IN p_timestamp TIMESTAMPTZ,
    IN p_conversation_table TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_session_id IS NULL OR TRIM(p_session_id) = '' THEN
        RAISE EXCEPTION 'Session ID is required';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.session_registry
        WHERE session_id = TRIM(p_session_id)
    ) THEN
        RAISE EXCEPTION 'Session ID % already exists',
            TRIM(p_session_id);
    END IF;

    INSERT INTO public.session_registry (
        session_id,
        patient_id,
        patient_name,
        "timestamp",
        conversation_table
    )
    VALUES (
        TRIM(p_session_id),
        p_patient_id,
        p_patient_name,
        COALESCE(p_timestamp, CURRENT_TIMESTAMP),
        p_conversation_table
    );
END;
$$;
-------------------------------------------------------------------------------------------------
--5
CREATE OR REPLACE PROCEDURE public.insert_provider(
    IN p_full_name VARCHAR(200),
    IN p_specialty VARCHAR(120),
    IN p_npi VARCHAR(20),
    IN p_location VARCHAR(200),
    IN p_is_active BOOLEAN
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_full_name IS NULL OR TRIM(p_full_name) = '' THEN
        RAISE EXCEPTION 'Provider full name is required';
    END IF;

    IF p_specialty IS NULL OR TRIM(p_specialty) = '' THEN
        RAISE EXCEPTION 'Provider specialty is required';
    END IF;

    IF p_npi IS NULL OR TRIM(p_npi) = '' THEN
        RAISE EXCEPTION 'Provider NPI is required';
    END IF;

    IF p_location IS NULL OR TRIM(p_location) = '' THEN
        RAISE EXCEPTION 'Provider location is required';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.providers
        WHERE npi = TRIM(p_npi)
    ) THEN
        RAISE EXCEPTION 'Provider with NPI % already exists',
            TRIM(p_npi);
    END IF;

    INSERT INTO public.providers (
        full_name,
        specialty,
        npi,
        location,
        is_active
    )
    VALUES (
        TRIM(p_full_name),
        TRIM(p_specialty),
        TRIM(p_npi),
        TRIM(p_location),
        COALESCE(p_is_active, TRUE)
    );
END;
$$;
---------------------------------------------------------------------------------------------
--6
CREATE OR REPLACE PROCEDURE public.insert_provider_network(
    IN p_provider_id INTEGER,
    IN p_payer_id INTEGER,
    IN p_in_network BOOLEAN
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_provider_id IS NULL THEN
        RAISE EXCEPTION 'Provider ID is required';
    END IF;

    IF p_payer_id IS NULL THEN
        RAISE EXCEPTION 'Payer ID is required';
    END IF;

    IF p_in_network IS NULL THEN
        RAISE EXCEPTION 'In-network status is required';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.provider_networks
        WHERE provider_id = p_provider_id
          AND payer_id = p_payer_id
    ) THEN
        RAISE EXCEPTION
            'Provider ID % and Payer ID % combination already exists',
            p_provider_id,
            p_payer_id;
    END IF;

    INSERT INTO public.provider_networks (
        provider_id,
        payer_id,
        in_network
    )
    VALUES (
        p_provider_id,
        p_payer_id,
        p_in_network
    );
END;
$$;
------------------------------------------------------------------------------------------
--7
CREATE OR REPLACE PROCEDURE public.insert_provider_availability(
    IN p_provider_id INTEGER,
    IN p_weekday INTEGER,
    IN p_start_time TIME,
    IN p_end_time TIME,
    IN p_slot_minutes INTEGER
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_provider_id IS NULL THEN
        RAISE EXCEPTION 'Provider ID is required';
    END IF;

    IF p_weekday IS NULL OR p_weekday NOT BETWEEN 0 AND 6 THEN
        RAISE EXCEPTION 'Weekday must be between 0 and 6';
    END IF;

    IF p_start_time IS NULL OR p_end_time IS NULL THEN
        RAISE EXCEPTION 'Start time and end time are required';
    END IF;

    IF p_start_time >= p_end_time THEN
        RAISE EXCEPTION 'Start time must be earlier than end time';
    END IF;

    IF p_slot_minutes IS NULL OR p_slot_minutes <= 0 THEN
        RAISE EXCEPTION 'Slot minutes must be greater than zero';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.provider_availability
        WHERE provider_id = p_provider_id
          AND weekday = p_weekday
          AND start_time = p_start_time
          AND end_time = p_end_time
    ) THEN
        RAISE EXCEPTION
            'Provider availability already exists for this weekday and time';
    END IF;

    INSERT INTO public.provider_availability (
        provider_id,
        weekday,
        start_time,
        end_time,
        slot_minutes
    )
    VALUES (
        p_provider_id,
        p_weekday,
        p_start_time,
        p_end_time,
        p_slot_minutes
    );
END;
$$;
-------------------------------------------------------------------------------------------------
--8
CREATE OR REPLACE PROCEDURE public.insert_portal_request(
    IN p_reference VARCHAR(20),
    IN p_status VARCHAR(20),
    IN p_intent VARCHAR(30),
    IN p_source_email_id INTEGER,
    IN p_extracted JSONB,
    IN p_patient_id INTEGER,
    IN p_match_confidence DOUBLE PRECISION,
    IN p_match_details JSONB,
    IN p_provider_id INTEGER,
    IN p_ai_confidence DOUBLE PRECISION,
    IN p_ai_summary TEXT,
    IN p_decision VARCHAR(20),
    IN p_review_reasons JSONB,
    IN p_draft_response TEXT,
    IN p_appointment_id INTEGER,
    IN p_assigned_to VARCHAR(120),
    IN p_priority VARCHAR(10),
    IN p_llm_provider VARCHAR(40),
    IN p_resolution_note TEXT,
    IN p_resolved_by VARCHAR(120)
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_reference IS NULL OR BTRIM(p_reference) = '' THEN
        RAISE EXCEPTION 'Reference is required';
    END IF;

    IF p_status IS NULL OR BTRIM(p_status) = '' THEN
        RAISE EXCEPTION 'Status is required';
    END IF;

    IF p_extracted IS NULL THEN
        RAISE EXCEPTION 'Extracted JSON is required';
    END IF;

    IF p_match_details IS NULL THEN
        RAISE EXCEPTION 'Match details JSON is required';
    END IF;

    IF p_review_reasons IS NULL THEN
        RAISE EXCEPTION 'Review reasons JSON is required';
    END IF;

    IF p_priority IS NULL OR BTRIM(p_priority) = '' THEN
        RAISE EXCEPTION 'Priority is required';
    END IF;

    -- Duplicate reference validation
    IF EXISTS (
        SELECT 1
        FROM public.portal_requests
        WHERE reference = BTRIM(p_reference)
    ) THEN
        RAISE EXCEPTION 'Portal request reference already exists: %',
            BTRIM(p_reference);
    END IF;

    -- Confidence validation
    IF p_match_confidence IS NOT NULL
       AND (p_match_confidence < 0 OR p_match_confidence > 1) THEN
        RAISE EXCEPTION 'Match confidence must be between 0 and 1';
    END IF;

    IF p_ai_confidence IS NOT NULL
       AND (p_ai_confidence < 0 OR p_ai_confidence > 1) THEN
        RAISE EXCEPTION 'AI confidence must be between 0 and 1';
    END IF;

    -- Insert portal request
    INSERT INTO public.portal_requests (
        reference,
        status,
        intent,
        source_email_id,
        extracted,
        patient_id,
        match_confidence,
        match_details,
        provider_id,
        ai_confidence,
        ai_summary,
        decision,
        review_reasons,
        draft_response,
        appointment_id,
        assigned_to,
        priority,
        llm_provider,
        resolution_note,
        resolved_by,
        created_at,
        updated_at
    )
    VALUES (
        BTRIM(p_reference),
        BTRIM(p_status),
        p_intent,
        p_source_email_id,
        p_extracted,
        p_patient_id,
        p_match_confidence,
        p_match_details,
        p_provider_id,
        p_ai_confidence,
        p_ai_summary,
        p_decision,
        p_review_reasons,
        p_draft_response,
        p_appointment_id,
        p_assigned_to,
        p_priority,
        p_llm_provider,
        p_resolution_note,
        p_resolved_by,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    );
END;
$$;
------------------------------------------------------------------------------------
--9
CREATE OR REPLACE PROCEDURE public.insert_phi_release(
    IN p_patient_id INTEGER,
    IN p_recipient_name VARCHAR(200),
    IN p_relationship_to_patient VARCHAR(50),
    IN p_phone VARCHAR(30),
    IN p_may_discuss_appointments BOOLEAN,
    IN p_may_discuss_billing BOOLEAN,
    IN p_may_discuss_clinical BOOLEAN,
    IN p_signed_on DATE,
    IN p_expires_on DATE,
    IN p_revoked_at TIMESTAMPTZ,
    IN p_created_by VARCHAR(120)
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_patient_id IS NULL THEN
        RAISE EXCEPTION 'Patient ID is required';
    END IF;

    IF p_recipient_name IS NULL OR BTRIM(p_recipient_name) = '' THEN
        RAISE EXCEPTION 'Recipient name is required';
    END IF;

    -- Patient existence validation
    IF NOT EXISTS (
        SELECT 1
        FROM public.patients
        WHERE id = p_patient_id
    ) THEN
        RAISE EXCEPTION 'Patient ID % does not exist', p_patient_id;
    END IF;

    -- Date validation
    IF p_signed_on IS NOT NULL
       AND p_expires_on IS NOT NULL
       AND p_expires_on < p_signed_on THEN
        RAISE EXCEPTION 'Expiry date cannot be earlier than signed date';
    END IF;

    -- Insert record
    INSERT INTO public.phi_releases (
        patient_id,
        recipient_name,
        relationship_to_patient,
        phone,
        may_discuss_appointments,
        may_discuss_billing,
        may_discuss_clinical,
        signed_on,
        expires_on,
        revoked_at,
        created_by,
        created_at
    )
    VALUES (
        p_patient_id,
        BTRIM(p_recipient_name),
        p_relationship_to_patient,
        p_phone,
        p_may_discuss_appointments,
        p_may_discuss_billing,
        p_may_discuss_clinical,
        p_signed_on,
        p_expires_on,
        p_revoked_at,
        p_created_by,
        CURRENT_TIMESTAMP
    );
END;
$$;
--------------------------------------------------------------------------------------
--10
CREATE OR REPLACE PROCEDURE public.insert_payer(
    IN p_name VARCHAR(200),
    IN p_payer_code VARCHAR(40),
    IN p_aliases JSONB,
    IN p_electronic_payer_id VARCHAR(20),
    IN p_financial_category VARCHAR(40),
    IN p_claims_addresses JSONB
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_name IS NULL OR BTRIM(p_name) = '' THEN
        RAISE EXCEPTION 'Payer name is required';
    END IF;

    IF p_payer_code IS NULL OR BTRIM(p_payer_code) = '' THEN
        RAISE EXCEPTION 'Payer code is required';
    END IF;

    IF p_aliases IS NULL THEN
        RAISE EXCEPTION 'Aliases JSON is required';
    END IF;

    -- JSON validation
    IF jsonb_typeof(p_aliases) NOT IN ('array', 'object') THEN
        RAISE EXCEPTION 'Aliases must be a JSON array or object';
    END IF;

    IF p_claims_addresses IS NOT NULL
       AND jsonb_typeof(p_claims_addresses) NOT IN ('array', 'object') THEN
        RAISE EXCEPTION 'Claims addresses must be a JSON array or object';
    END IF;

    -- Duplicate name validation
    IF EXISTS (
        SELECT 1
        FROM public.payers
        WHERE LOWER(name) = LOWER(BTRIM(p_name))
    ) THEN
        RAISE EXCEPTION 'Payer name already exists: %', BTRIM(p_name);
    END IF;

    -- Duplicate payer code validation
    IF EXISTS (
        SELECT 1
        FROM public.payers
        WHERE UPPER(payer_code) = UPPER(BTRIM(p_payer_code))
    ) THEN
        RAISE EXCEPTION 'Payer code already exists: %', BTRIM(p_payer_code);
    END IF;

    -- Insert payer
    INSERT INTO public.payers (
        name,
        payer_code,
        aliases,
        electronic_payer_id,
        financial_category,
        claims_addresses
    )
    VALUES (
        BTRIM(p_name),
        BTRIM(p_payer_code),
        p_aliases,
        p_electronic_payer_id,
        p_financial_category,
        p_claims_addresses
    );
END;
$$;
--------------------------------------------------------------------------------------------
--11
CREATE OR REPLACE PROCEDURE public.insert_payer_plan(
    IN p_payer_id INTEGER,
    IN p_name VARCHAR(200),
    IN p_plan_type VARCHAR(20)
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_payer_id IS NULL THEN
        RAISE EXCEPTION 'Payer ID is required';
    END IF;

    IF p_name IS NULL OR BTRIM(p_name) = '' THEN
        RAISE EXCEPTION 'Plan name is required';
    END IF;

    IF p_plan_type IS NULL OR BTRIM(p_plan_type) = '' THEN
        RAISE EXCEPTION 'Plan type is required';
    END IF;

    -- Validate payer existence
    IF NOT EXISTS (
        SELECT 1
        FROM public.payers
        WHERE id = p_payer_id
    ) THEN
        RAISE EXCEPTION 'Payer ID % does not exist', p_payer_id;
    END IF;

    -- Validate duplicate plan name for the same payer
    IF EXISTS (
        SELECT 1
        FROM public.payer_plans
        WHERE payer_id = p_payer_id
          AND LOWER(name) = LOWER(BTRIM(p_name))
    ) THEN
        RAISE EXCEPTION
            'Plan name already exists for payer ID %: %',
            p_payer_id,
            BTRIM(p_name);
    END IF;

    -- Insert payer plan
    INSERT INTO public.payer_plans (
        payer_id,
        name,
        plan_type
    )
    VALUES (
        p_payer_id,
        BTRIM(p_name),
        BTRIM(p_plan_type)
    );
END;
$$;
---------------------------------------------------------------------------------------------
--12

CREATE OR REPLACE PROCEDURE public.insert_patient(
    IN p_mrn VARCHAR(30),
    IN p_prefix VARCHAR(20),
    IN p_first_name VARCHAR(100),
    IN p_middle_name VARCHAR(100),
    IN p_last_name VARCHAR(100),
    IN p_suffix VARCHAR(20),
    IN p_preferred_name VARCHAR(100),
    IN p_previous_name VARCHAR(200),
    IN p_is_test_patient BOOLEAN,
    IN p_marital_status VARCHAR(30),
    IN p_date_of_birth DATE,
    IN p_sex VARCHAR(20),
    IN p_ssn_encrypted TEXT,
    IN p_ssn_last4 VARCHAR(4),
    IN p_drivers_license_encrypted TEXT,
    IN p_drivers_license_last4 VARCHAR(4),
    IN p_drivers_license_state VARCHAR(2),
    IN p_phone VARCHAR(30),
    IN p_home_phone VARCHAR(30),
    IN p_work_phone VARCHAR(30),
    IN p_preferred_contact_method VARCHAR(20),
    IN p_email VARCHAR(255),
    IN p_address_line1 VARCHAR(200),
    IN p_address_line2 VARCHAR(200),
    IN p_city VARCHAR(100),
    IN p_state VARCHAR(40),
    IN p_postal_code VARCHAR(20),
    IN p_preferred_provider_id INTEGER,
    IN p_notes TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_mrn IS NULL OR BTRIM(p_mrn) = '' THEN
        RAISE EXCEPTION 'MRN is required';
    END IF;

    IF p_first_name IS NULL OR BTRIM(p_first_name) = '' THEN
        RAISE EXCEPTION 'First name is required';
    END IF;

    IF p_last_name IS NULL OR BTRIM(p_last_name) = '' THEN
        RAISE EXCEPTION 'Last name is required';
    END IF;

    -- Validate date of birth
    IF p_date_of_birth IS NOT NULL
       AND p_date_of_birth > CURRENT_DATE THEN
        RAISE EXCEPTION 'Date of birth cannot be in the future';
    END IF;

    -- Validate last four digits
    IF p_ssn_last4 IS NOT NULL
       AND p_ssn_last4 !~ '^[0-9]{4}$' THEN
        RAISE EXCEPTION 'SSN last four digits must contain exactly 4 digits';
    END IF;

    IF p_drivers_license_last4 IS NOT NULL
       AND p_drivers_license_last4 !~ '^[0-9]{4}$' THEN
        RAISE EXCEPTION 'Driver license last four digits must contain exactly 4 digits';
    END IF;

    IF p_drivers_license_state IS NOT NULL
       AND LENGTH(BTRIM(p_drivers_license_state)) <> 2 THEN
        RAISE EXCEPTION 'Driver license state must contain 2 characters';
    END IF;

    -- Check duplicate MRN
    IF EXISTS (
        SELECT 1
        FROM public.patients
        WHERE LOWER(mrn) = LOWER(BTRIM(p_mrn))
    ) THEN
        RAISE EXCEPTION 'Patient MRN already exists: %', BTRIM(p_mrn);
    END IF;

    -- Validate preferred provider
    IF p_preferred_provider_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM public.providers
           WHERE id = p_preferred_provider_id
       ) THEN
        RAISE EXCEPTION 'Preferred provider ID % does not exist',
            p_preferred_provider_id;
    END IF;

    -- Insert patient
    INSERT INTO public.patients (
        mrn,
        prefix,
        first_name,
        middle_name,
        last_name,
        suffix,
        preferred_name,
        previous_name,
        is_test_patient,
        marital_status,
        date_of_birth,
        sex,
        ssn_encrypted,
        ssn_last4,
        drivers_license_encrypted,
        drivers_license_last4,
        drivers_license_state,
        phone,
        home_phone,
        work_phone,
        preferred_contact_method,
        email,
        address_line1,
        address_line2,
        city,
        state,
        postal_code,
        preferred_provider_id,
        notes,
        created_at,
        updated_at
    )
    VALUES (
        BTRIM(p_mrn),
        p_prefix,
        BTRIM(p_first_name),
        p_middle_name,
        BTRIM(p_last_name),
        p_suffix,
        p_preferred_name,
        p_previous_name,
        p_is_test_patient,
        p_marital_status,
        p_date_of_birth,
        p_sex,
        p_ssn_encrypted,
        p_ssn_last4,
        p_drivers_license_encrypted,
        p_drivers_license_last4,
        UPPER(p_drivers_license_state),
        p_phone,
        p_home_phone,
        p_work_phone,
        p_preferred_contact_method,
        p_email,
        p_address_line1,
        p_address_line2,
        p_city,
        p_state,
        p_postal_code,
        p_preferred_provider_id,
        p_notes,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    );
END;
$$;
------------------------------------------------------------------------------------------
--13

CREATE OR REPLACE PROCEDURE public.insert_patient_document(
    IN p_patient_id INTEGER,
    IN p_kind VARCHAR(40),
    IN p_filename VARCHAR(255),
    IN p_content_type VARCHAR(100),
    IN p_storage_path VARCHAR(500),
    IN p_size_bytes INTEGER,
    IN p_uploaded_by VARCHAR(120)
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_patient_id IS NULL THEN
        RAISE EXCEPTION 'Patient ID is required';
    END IF;

    IF p_kind IS NULL OR BTRIM(p_kind) = '' THEN
        RAISE EXCEPTION 'Document kind is required';
    END IF;

    IF p_filename IS NULL OR BTRIM(p_filename) = '' THEN
        RAISE EXCEPTION 'Filename is required';
    END IF;

    IF p_content_type IS NULL OR BTRIM(p_content_type) = '' THEN
        RAISE EXCEPTION 'Content type is required';
    END IF;

    IF p_storage_path IS NULL OR BTRIM(p_storage_path) = '' THEN
        RAISE EXCEPTION 'Storage path is required';
    END IF;

    IF p_uploaded_by IS NULL OR BTRIM(p_uploaded_by) = '' THEN
        RAISE EXCEPTION 'Uploaded by is required';
    END IF;

    -- Validate file size
    IF p_size_bytes IS NULL OR p_size_bytes < 0 THEN
        RAISE EXCEPTION 'File size must be zero or greater';
    END IF;

    -- Validate patient existence
    IF NOT EXISTS (
        SELECT 1
        FROM public.patients
        WHERE id = p_patient_id
    ) THEN
        RAISE EXCEPTION 'Patient ID % does not exist', p_patient_id;
    END IF;

    -- Insert document
    INSERT INTO public.patient_documents (
        patient_id,
        kind,
        filename,
        content_type,
        storage_path,
        size_bytes,
        uploaded_by,
        uploaded_at
    )
    VALUES (
        p_patient_id,
        BTRIM(p_kind),
        BTRIM(p_filename),
        BTRIM(p_content_type),
        BTRIM(p_storage_path),
        p_size_bytes,
        BTRIM(p_uploaded_by),
        CURRENT_TIMESTAMP
    );
END;
$$;
-------------------------------------------------------------------------------------------------
--14

CREATE OR REPLACE PROCEDURE public.insert_password_reset_token(
    IN p_user_id INTEGER,
    IN p_token_hash VARCHAR(64),
    IN p_expires_at TIMESTAMPTZ,
    IN p_requested_ip VARCHAR(64)
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'User ID is required';
    END IF;

    IF p_token_hash IS NULL OR BTRIM(p_token_hash) = '' THEN
        RAISE EXCEPTION 'Token hash is required';
    END IF;

    IF LENGTH(BTRIM(p_token_hash)) <> 64 THEN
        RAISE EXCEPTION 'Token hash must contain exactly 64 characters';
    END IF;

    IF p_expires_at IS NULL THEN
        RAISE EXCEPTION 'Expiry timestamp is required';
    END IF;

    -- Validate expiry timestamp
    IF p_expires_at <= CURRENT_TIMESTAMP THEN
        RAISE EXCEPTION 'Expiry timestamp must be in the future';
    END IF;

    -- Validate user existence
    IF NOT EXISTS (
        SELECT 1
        FROM public.users
        WHERE id = p_user_id
    ) THEN
        RAISE EXCEPTION 'User ID % does not exist', p_user_id;
    END IF;

    -- Check duplicate token hash
    IF EXISTS (
        SELECT 1
        FROM public.password_reset_tokens
        WHERE token_hash = BTRIM(p_token_hash)
    ) THEN
        RAISE EXCEPTION 'Token hash already exists';
    END IF;

    -- Insert token
    INSERT INTO public.password_reset_tokens (
        user_id,
        token_hash,
        created_at,
        expires_at,
        used_at,
        requested_ip
    )
    VALUES (
        p_user_id,
        BTRIM(p_token_hash),
        CURRENT_TIMESTAMP,
        p_expires_at,
        NULL,
        p_requested_ip
    );
END;
$$;
------------------------------------------------------------------------------
--15

CREATE OR REPLACE PROCEDURE public.insert_oauth_state(
    IN p_state_hash VARCHAR(64),
    IN p_provider VARCHAR(30),
    IN p_flow VARCHAR(20),
    IN p_code_verifier_encrypted TEXT,
    IN p_created_by VARCHAR(120),
    IN p_expires_at TIMESTAMPTZ
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_state_hash IS NULL OR BTRIM(p_state_hash) = '' THEN
        RAISE EXCEPTION 'State hash is required';
    END IF;

    IF LENGTH(BTRIM(p_state_hash)) <> 64 THEN
        RAISE EXCEPTION 'State hash must contain exactly 64 characters';
    END IF;

    IF p_provider IS NULL OR BTRIM(p_provider) = '' THEN
        RAISE EXCEPTION 'Provider is required';
    END IF;

    IF p_flow IS NULL OR BTRIM(p_flow) = '' THEN
        RAISE EXCEPTION 'Flow is required';
    END IF;

    IF p_created_by IS NULL OR BTRIM(p_created_by) = '' THEN
        RAISE EXCEPTION 'Created by is required';
    END IF;

    IF p_expires_at IS NULL THEN
        RAISE EXCEPTION 'Expiry timestamp is required';
    END IF;

    -- Validate expiry timestamp
    IF p_expires_at <= CURRENT_TIMESTAMP THEN
        RAISE EXCEPTION 'Expiry timestamp must be in the future';
    END IF;

    -- Validate duplicate state hash
    IF EXISTS (
        SELECT 1
        FROM public.oauth_states
        WHERE state_hash = BTRIM(p_state_hash)
    ) THEN
        RAISE EXCEPTION 'OAuth state hash already exists';
    END IF;

    -- Insert OAuth state
    INSERT INTO public.oauth_states (
        state_hash,
        provider,
        flow,
        code_verifier_encrypted,
        created_by,
        created_at,
        expires_at,
        used_at
    )
    VALUES (
        BTRIM(p_state_hash),
        BTRIM(p_provider),
        BTRIM(p_flow),
        p_code_verifier_encrypted,
        BTRIM(p_created_by),
        CURRENT_TIMESTAMP,
        p_expires_at,
        NULL
    );
END;
$$;
---------------------------------------------------------------------------
--16

CREATE OR REPLACE PROCEDURE public.insert_integration_mailbox(
    IN p_connection_id INTEGER,
    IN p_address VARCHAR(255),
    IN p_display_name VARCHAR(200),
    IN p_folder VARCHAR(200),
    IN p_enabled BOOLEAN,
    IN p_process_inbound BOOLEAN,
    IN p_reply_from_same_mailbox BOOLEAN,
    IN p_mark_as_read BOOLEAN,
    IN p_is_default_sender BOOLEAN,
    IN p_created_by VARCHAR(120),
    IN p_skip_sender_domains TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_connection_id IS NULL THEN
        RAISE EXCEPTION 'Connection ID is required';
    END IF;

    IF p_address IS NULL OR BTRIM(p_address) = '' THEN
        RAISE EXCEPTION 'Email address is required';
    END IF;

    IF p_folder IS NULL OR BTRIM(p_folder) = '' THEN
        RAISE EXCEPTION 'Folder is required';
    END IF;

    IF p_enabled IS NULL THEN
        RAISE EXCEPTION 'Enabled flag is required';
    END IF;

    IF p_process_inbound IS NULL THEN
        RAISE EXCEPTION 'Process inbound flag is required';
    END IF;

    IF p_reply_from_same_mailbox IS NULL THEN
        RAISE EXCEPTION 'Reply from same mailbox flag is required';
    END IF;

    IF p_mark_as_read IS NULL THEN
        RAISE EXCEPTION 'Mark as read flag is required';
    END IF;

    IF p_is_default_sender IS NULL THEN
        RAISE EXCEPTION 'Default sender flag is required';
    END IF;

    -- Validate connection existence
    IF NOT EXISTS (
        SELECT 1
        FROM public.integration_connections
        WHERE id = p_connection_id
    ) THEN
        RAISE EXCEPTION 'Integration connection ID % does not exist',
            p_connection_id;
    END IF;

    -- Validate duplicate email address
    IF EXISTS (
        SELECT 1
        FROM public.integration_mailboxes
        WHERE LOWER(address) = LOWER(BTRIM(p_address))
    ) THEN
        RAISE EXCEPTION 'Mailbox address already exists: %',
            BTRIM(p_address);
    END IF;

    -- Insert mailbox
    INSERT INTO public.integration_mailboxes (
        connection_id,
        address,
        display_name,
        folder,
        enabled,
        process_inbound,
        reply_from_same_mailbox,
        mark_as_read,
        is_default_sender,
        created_by,
        created_at,
        skip_sender_domains
    )
    VALUES (
        p_connection_id,
        LOWER(BTRIM(p_address)),
        p_display_name,
        BTRIM(p_folder),
        p_enabled,
        p_process_inbound,
        p_reply_from_same_mailbox,
        p_mark_as_read,
        p_is_default_sender,
        p_created_by,
        CURRENT_TIMESTAMP,
        p_skip_sender_domains
    );
END;
$$;
---------------------------------------------------------------------------------
--17

CREATE OR REPLACE PROCEDURE public.insert_integration_connection(
    IN p_provider VARCHAR(30),
    IN p_auth_mode VARCHAR(20),
    IN p_tenant_id VARCHAR(100),
    IN p_client_id VARCHAR(100),
    IN p_client_secret_encrypted TEXT,
    IN p_client_secret_hint VARCHAR(10),
    IN p_status VARCHAR(30),
    IN p_connected_account VARCHAR(255),
    IN p_connected_name VARCHAR(255),
    IN p_scopes TEXT,
    IN p_refresh_token_encrypted TEXT,
    IN p_token_expires_at TIMESTAMPTZ,
    IN p_poll_interval_seconds INTEGER,
    IN p_connected_by VARCHAR(120)
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_provider IS NULL OR BTRIM(p_provider) = '' THEN
        RAISE EXCEPTION 'Provider is required';
    END IF;

    IF p_auth_mode IS NULL OR BTRIM(p_auth_mode) = '' THEN
        RAISE EXCEPTION 'Authentication mode is required';
    END IF;

    IF p_status IS NULL OR BTRIM(p_status) = '' THEN
        RAISE EXCEPTION 'Status is required';
    END IF;

    -- Validate polling interval
    IF p_poll_interval_seconds IS NOT NULL
       AND p_poll_interval_seconds <= 0 THEN
        RAISE EXCEPTION 'Poll interval must be greater than zero';
    END IF;

    -- Check duplicate provider
    IF EXISTS (
        SELECT 1
        FROM public.integration_connections
        WHERE LOWER(provider) = LOWER(BTRIM(p_provider))
    ) THEN
        RAISE EXCEPTION 'Integration provider already exists: %',
            BTRIM(p_provider);
    END IF;

    -- Insert integration connection
    INSERT INTO public.integration_connections (
        provider,
        auth_mode,
        tenant_id,
        client_id,
        client_secret_encrypted,
        client_secret_hint,
        status,
        connected_account,
        connected_name,
        scopes,
        refresh_token_encrypted,
        token_expires_at,
        poll_interval_seconds,
        last_error,
        connected_by,
        connected_at,
        updated_at
    )
    VALUES (
        BTRIM(p_provider),
        BTRIM(p_auth_mode),
        p_tenant_id,
        p_client_id,
        p_client_secret_encrypted,
        p_client_secret_hint,
        BTRIM(p_status),
        p_connected_account,
        p_connected_name,
        p_scopes,
        p_refresh_token_encrypted,
        p_token_expires_at,
        p_poll_interval_seconds,
        NULL,
        p_connected_by,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    );
END;
$$;
---------------------------------------------------------------------------------------------------
--18

CREATE OR REPLACE PROCEDURE public.insert_insurance_policy(
    IN p_patient_id INTEGER,
    IN p_payer_id INTEGER,
    IN p_member_id VARCHAR(60),
    IN p_group_number VARCHAR(60),
    IN p_subscriber_name VARCHAR(200),
    IN p_relationship_to_subscriber VARCHAR(30),
    IN p_is_primary BOOLEAN,
    IN p_is_active BOOLEAN,
    IN p_coverage_order VARCHAR(10),
    IN p_plan_name VARCHAR(200),
    IN p_policy_type VARCHAR(20),
    IN p_claims_address VARCHAR(300),
    IN p_notes VARCHAR(500),
    IN p_requires_referral BOOLEAN,
    IN p_requires_precert_inpatient BOOLEAN,
    IN p_requires_preauth_outpatient BOOLEAN,
    IN p_holder_first_name VARCHAR(100),
    IN p_holder_last_name VARCHAR(100),
    IN p_holder_date_of_birth DATE,
    IN p_eligibility_status VARCHAR(20),
    IN p_copay_amount NUMERIC(10,2),
    IN p_coinsurance_pct NUMERIC(5,2),
    IN p_deductible NUMERIC(10,2),
    IN p_remaining_deductible NUMERIC(10,2),
    IN p_out_of_pocket NUMERIC(10,2),
    IN p_remaining_out_of_pocket NUMERIC(10,2),
    IN p_effective_date DATE,
    IN p_end_date DATE
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_patient_id IS NULL THEN
        RAISE EXCEPTION 'Patient ID is required';
    END IF;

    IF p_payer_id IS NULL THEN
        RAISE EXCEPTION 'Payer ID is required';
    END IF;

    IF p_member_id IS NULL OR BTRIM(p_member_id) = '' THEN
        RAISE EXCEPTION 'Member ID is required';
    END IF;

    IF p_relationship_to_subscriber IS NULL
       OR BTRIM(p_relationship_to_subscriber) = '' THEN
        RAISE EXCEPTION 'Relationship to subscriber is required';
    END IF;

    IF p_is_primary IS NULL THEN
        RAISE EXCEPTION 'Is primary flag is required';
    END IF;

    IF p_is_active IS NULL THEN
        RAISE EXCEPTION 'Is active flag is required';
    END IF;

    -- Validate patient
    IF NOT EXISTS (
        SELECT 1
        FROM public.patients
        WHERE id = p_patient_id
    ) THEN
        RAISE EXCEPTION 'Patient ID % does not exist', p_patient_id;
    END IF;

    -- Validate payer
    IF NOT EXISTS (
        SELECT 1
        FROM public.payers
        WHERE id = p_payer_id
    ) THEN
        RAISE EXCEPTION 'Payer ID % does not exist', p_payer_id;
    END IF;

    -- Validate dates
    IF p_effective_date IS NOT NULL
       AND p_end_date IS NOT NULL
       AND p_end_date < p_effective_date THEN
        RAISE EXCEPTION 'End date cannot be earlier than effective date';
    END IF;

    IF p_holder_date_of_birth IS NOT NULL
       AND p_holder_date_of_birth > CURRENT_DATE THEN
        RAISE EXCEPTION 'Policy holder date of birth cannot be in the future';
    END IF;

    -- Validate financial values
    IF p_copay_amount IS NOT NULL AND p_copay_amount < 0 THEN
        RAISE EXCEPTION 'Copay amount cannot be negative';
    END IF;

    IF p_coinsurance_pct IS NOT NULL
       AND (p_coinsurance_pct < 0 OR p_coinsurance_pct > 100) THEN
        RAISE EXCEPTION 'Coinsurance percentage must be between 0 and 100';
    END IF;

    IF p_deductible IS NOT NULL AND p_deductible < 0 THEN
        RAISE EXCEPTION 'Deductible cannot be negative';
    END IF;

    IF p_remaining_deductible IS NOT NULL
       AND p_remaining_deductible < 0 THEN
        RAISE EXCEPTION 'Remaining deductible cannot be negative';
    END IF;

    IF p_out_of_pocket IS NOT NULL AND p_out_of_pocket < 0 THEN
        RAISE EXCEPTION 'Out-of-pocket amount cannot be negative';
    END IF;

    IF p_remaining_out_of_pocket IS NOT NULL
       AND p_remaining_out_of_pocket < 0 THEN
        RAISE EXCEPTION 'Remaining out-of-pocket amount cannot be negative';
    END IF;

    -- Insert insurance policy
    INSERT INTO public.insurance_policies (
        patient_id,
        payer_id,
        member_id,
        group_number,
        subscriber_name,
        relationship_to_subscriber,
        is_primary,
        is_active,
        created_at,
        coverage_order,
        plan_name,
        policy_type,
        claims_address,
        notes,
        requires_referral,
        requires_precert_inpatient,
        requires_preauth_outpatient,
        holder_first_name,
        holder_last_name,
        holder_date_of_birth,
        eligibility_status,
        copay_amount,
        coinsurance_pct,
        deductible,
        remaining_deductible,
        out_of_pocket,
        remaining_out_of_pocket,
        effective_date,
        end_date
    )
    VALUES (
        p_patient_id,
        p_payer_id,
        BTRIM(p_member_id),
        p_group_number,
        p_subscriber_name,
        BTRIM(p_relationship_to_subscriber),
        p_is_primary,
        p_is_active,
        CURRENT_TIMESTAMP,
        p_coverage_order,
        p_plan_name,
        p_policy_type,
        p_claims_address,
        p_notes,
        p_requires_referral,
        p_requires_precert_inpatient,
        p_requires_preauth_outpatient,
        p_holder_first_name,
        p_holder_last_name,
        p_holder_date_of_birth,
        p_eligibility_status,
        p_copay_amount,
        p_coinsurance_pct,
        p_deductible,
        p_remaining_deductible,
        p_out_of_pocket,
        p_remaining_out_of_pocket,
        p_effective_date,
        p_end_date
    );
END;
$$;
----------------------------------------------------------------------------------------------------
--19

CREATE OR REPLACE PROCEDURE public.insert_guarantor(
    IN p_patient_id INTEGER,
    IN p_same_as_patient BOOLEAN,
    IN p_relationship_to_patient VARCHAR(30),
    IN p_first_name VARCHAR(100),
    IN p_last_name VARCHAR(100),
    IN p_date_of_birth DATE,
    IN p_phone VARCHAR(30),
    IN p_email VARCHAR(255),
    IN p_address_line1 VARCHAR(200),
    IN p_city VARCHAR(100),
    IN p_state VARCHAR(40),
    IN p_postal_code VARCHAR(20)
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_patient_id IS NULL THEN
        RAISE EXCEPTION 'Patient ID is required';
    END IF;

    -- Validate patient existence
    IF NOT EXISTS (
        SELECT 1
        FROM public.patients
        WHERE id = p_patient_id
    ) THEN
        RAISE EXCEPTION 'Patient ID % does not exist', p_patient_id;
    END IF;

    -- Validate date of birth
    IF p_date_of_birth IS NOT NULL
       AND p_date_of_birth > CURRENT_DATE THEN
        RAISE EXCEPTION 'Date of birth cannot be in the future';
    END IF;

    -- Validate duplicate guarantor
    IF EXISTS (
        SELECT 1
        FROM public.guarantors
        WHERE patient_id = p_patient_id
    ) THEN
        RAISE EXCEPTION
            'A guarantor record already exists for patient ID %',
            p_patient_id;
    END IF;

    -- Validate guarantor details when not the patient
    IF p_same_as_patient IS FALSE THEN
        IF p_first_name IS NULL OR BTRIM(p_first_name) = '' THEN
            RAISE EXCEPTION 'Guarantor first name is required';
        END IF;

        IF p_last_name IS NULL OR BTRIM(p_last_name) = '' THEN
            RAISE EXCEPTION 'Guarantor last name is required';
        END IF;
    END IF;

    -- Insert guarantor
    INSERT INTO public.guarantors (
        patient_id,
        same_as_patient,
        relationship_to_patient,
        first_name,
        last_name,
        date_of_birth,
        phone,
        email,
        address_line1,
        city,
        state,
        postal_code,
        updated_at
    )
    VALUES (
        p_patient_id,
        p_same_as_patient,
        p_relationship_to_patient,
        p_first_name,
        p_last_name,
        p_date_of_birth,
        p_phone,
        p_email,
        p_address_line1,
        p_city,
        p_state,
        p_postal_code,
        CURRENT_TIMESTAMP
    );
END;
$$;
--------------------------------------------------------------------------------------
--20
CREATE OR REPLACE PROCEDURE public.insert_emr_export(
    IN p_export_id VARCHAR(64),
    IN p_session_id VARCHAR(64),
    IN p_patient_id VARCHAR(128),
    IN p_patient_name TEXT,
    IN p_exported_at TIMESTAMPTZ,
    IN p_export_file TEXT,
    IN p_payload JSONB
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Validate required field
    IF p_export_id IS NULL OR BTRIM(p_export_id) = '' THEN
        RAISE EXCEPTION 'Export ID is required';
    END IF;

    -- Check duplicate export ID
    IF EXISTS (
        SELECT 1
        FROM public.emr_exports
        WHERE export_id = p_export_id
    ) THEN
        RAISE EXCEPTION 'Export ID % already exists', p_export_id;
    END IF;

    -- Validate JSON payload
    IF p_payload IS NOT NULL
       AND jsonb_typeof(p_payload) NOT IN ('object', 'array') THEN
        RAISE EXCEPTION 'Payload must be a JSON object or array';
    END IF;

    INSERT INTO public.emr_exports (
        export_id,
        session_id,
        patient_id,
        patient_name,
        exported_at,
        export_file,
        payload
    )
    VALUES (
        p_export_id,
        p_session_id,
        p_patient_id,
        p_patient_name,
        p_exported_at,
        p_export_file,
        p_payload
    );
END;
$$;
------------------------------------------------------------------------------------------
--21
CREATE OR REPLACE PROCEDURE public.insert_email_message(
    IN p_message_id VARCHAR(500),
    IN p_direction VARCHAR(10),
    IN p_source VARCHAR(20),
    IN p_from_address VARCHAR(255),
    IN p_from_name VARCHAR(200),
    IN p_to_address VARCHAR(255),
    IN p_subject VARCHAR(500),
    IN p_body_text TEXT,
    IN p_in_reply_to VARCHAR(500),
    IN p_request_id INTEGER,
    IN p_send_status VARCHAR(20),
    IN p_send_error TEXT,
    IN p_sent_by VARCHAR(120),
    IN p_received_at TIMESTAMPTZ,
    IN p_delivery_method VARCHAR(20),
    IN p_delivered_at TIMESTAMPTZ,
    IN p_mailbox_id INTEGER,
    IN p_provider_message_id VARCHAR(300),
    IN p_conversation_id VARCHAR(300),
    IN p_body_raw TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_message_id IS NULL OR BTRIM(p_message_id) = '' THEN
        RAISE EXCEPTION 'Message ID is required';
    END IF;

    IF p_direction IS NULL OR BTRIM(p_direction) = '' THEN
        RAISE EXCEPTION 'Direction is required';
    END IF;

    IF p_source IS NULL OR BTRIM(p_source) = '' THEN
        RAISE EXCEPTION 'Source is required';
    END IF;

    IF p_from_address IS NULL OR BTRIM(p_from_address) = '' THEN
        RAISE EXCEPTION 'From address is required';
    END IF;

    IF p_to_address IS NULL OR BTRIM(p_to_address) = '' THEN
        RAISE EXCEPTION 'To address is required';
    END IF;

    IF p_subject IS NULL THEN
        RAISE EXCEPTION 'Subject is required';
    END IF;

    IF p_body_text IS NULL THEN
        RAISE EXCEPTION 'Body text is required';
    END IF;

    IF p_received_at IS NULL THEN
        RAISE EXCEPTION 'Received timestamp is required';
    END IF;

    -- Validate request foreign key
    IF p_request_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM public.portal_requests
           WHERE id = p_request_id
       ) THEN
        RAISE EXCEPTION 'Portal request ID % does not exist', p_request_id;
    END IF;

    -- Check duplicate message ID
    IF EXISTS (
        SELECT 1
        FROM public.email_messages
        WHERE message_id = p_message_id
    ) THEN
        RAISE EXCEPTION 'Message ID % already exists', p_message_id;
    END IF;

    INSERT INTO public.email_messages (
        message_id,
        direction,
        source,
        from_address,
        from_name,
        to_address,
        subject,
        body_text,
        in_reply_to,
        request_id,
        send_status,
        send_error,
        sent_by,
        received_at,
        delivery_method,
        delivered_at,
        mailbox_id,
        provider_message_id,
        conversation_id,
        body_raw
    )
    VALUES (
        p_message_id,
        p_direction,
        p_source,
        p_from_address,
        p_from_name,
        p_to_address,
        p_subject,
        p_body_text,
        p_in_reply_to,
        p_request_id,
        p_send_status,
        p_send_error,
        p_sent_by,
        p_received_at,
        p_delivery_method,
        p_delivered_at,
        p_mailbox_id,
        p_provider_message_id,
        p_conversation_id,
        p_body_raw
    );
END;
$$;
-----------------------------------------------------------------------------------------------
--22
CREATE OR REPLACE PROCEDURE public.insert_eligibility_check(
    IN p_patient_id INTEGER,
    IN p_policy_id INTEGER,
    IN p_provider_id INTEGER,
    IN p_request_id INTEGER,
    IN p_coverage_status VARCHAR(30),
    IN p_in_network BOOLEAN,
    IN p_plan_name VARCHAR(200),
    IN p_copay VARCHAR(40),
    IN p_message TEXT,
    IN p_raw_response JSONB,
    IN p_checked_by VARCHAR(120),
    IN p_checked_at TIMESTAMPTZ,
    IN p_date_of_service DATE,
    IN p_npi_type VARCHAR(12),
    IN p_npi VARCHAR(20),
    IN p_payer_member_name VARCHAR(200),
    IN p_payer_member_dob DATE,
    IN p_payer_member_id VARCHAR(60),
    IN p_plan_type VARCHAR(20),
    IN p_plan_start DATE,
    IN p_plan_end DATE,
    IN p_eligibility_start DATE,
    IN p_eligibility_end DATE,
    IN p_raw_x12 TEXT,
    IN p_discrepancies JSONB,
    IN p_ai_summary TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_patient_id IS NULL THEN
        RAISE EXCEPTION 'Patient ID is required';
    END IF;

    IF p_coverage_status IS NULL
       OR BTRIM(p_coverage_status) = '' THEN
        RAISE EXCEPTION 'Coverage status is required';
    END IF;

    IF p_raw_response IS NULL THEN
        RAISE EXCEPTION 'Raw response is required';
    END IF;

    IF p_checked_by IS NULL
       OR BTRIM(p_checked_by) = '' THEN
        RAISE EXCEPTION 'Checked by is required';
    END IF;

    IF p_checked_at IS NULL THEN
        RAISE EXCEPTION 'Checked timestamp is required';
    END IF;

    -- Validate patient
    IF NOT EXISTS (
        SELECT 1
        FROM public.patients
        WHERE id = p_patient_id
    ) THEN
        RAISE EXCEPTION 'Patient ID % does not exist', p_patient_id;
    END IF;

    -- Validate optional policy
    IF p_policy_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM public.insurance_policies
           WHERE id = p_policy_id
       ) THEN
        RAISE EXCEPTION 'Policy ID % does not exist', p_policy_id;
    END IF;

    -- Validate optional provider
    IF p_provider_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM public.providers
           WHERE id = p_provider_id
       ) THEN
        RAISE EXCEPTION 'Provider ID % does not exist', p_provider_id;
    END IF;

    -- Validate optional portal request
    IF p_request_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM public.portal_requests
           WHERE id = p_request_id
       ) THEN
        RAISE EXCEPTION 'Request ID % does not exist', p_request_id;
    END IF;

    -- Validate JSON object/array
    IF jsonb_typeof(p_raw_response) NOT IN ('object', 'array') THEN
        RAISE EXCEPTION 'Raw response must be a JSON object or array';
    END IF;

    IF p_discrepancies IS NOT NULL
       AND jsonb_typeof(p_discrepancies) NOT IN ('object', 'array') THEN
        RAISE EXCEPTION 'Discrepancies must be a JSON object or array';
    END IF;

    -- Validate plan dates
    IF p_plan_start IS NOT NULL
       AND p_plan_end IS NOT NULL
       AND p_plan_end < p_plan_start THEN
        RAISE EXCEPTION 'Plan end date cannot be before plan start date';
    END IF;

    -- Validate eligibility dates
    IF p_eligibility_start IS NOT NULL
       AND p_eligibility_end IS NOT NULL
       AND p_eligibility_end < p_eligibility_start THEN
        RAISE EXCEPTION 'Eligibility end date cannot be before eligibility start date';
    END IF;

    INSERT INTO public.eligibility_checks (
        patient_id,
        policy_id,
        provider_id,
        request_id,
        coverage_status,
        in_network,
        plan_name,
        copay,
        message,
        raw_response,
        checked_by,
        checked_at,
        date_of_service,
        npi_type,
        npi,
        payer_member_name,
        payer_member_dob,
        payer_member_id,
        plan_type,
        plan_start,
        plan_end,
        eligibility_start,
        eligibility_end,
        raw_x12,
        discrepancies,
        ai_summary
    )
    VALUES (
        p_patient_id,
        p_policy_id,
        p_provider_id,
        p_request_id,
        p_coverage_status,
        p_in_network,
        p_plan_name,
        p_copay,
        p_message,
        p_raw_response,
        p_checked_by,
        p_checked_at,
        p_date_of_service,
        p_npi_type,
        p_npi,
        p_payer_member_name,
        p_payer_member_dob,
        p_payer_member_id,
        p_plan_type,
        p_plan_start,
        p_plan_end,
        p_eligibility_start,
        p_eligibility_end,
        p_raw_x12,
        p_discrepancies,
        p_ai_summary
    );
END;
$$;
------------------------------------------------------------------------------------------
--23
CREATE OR REPLACE PROCEDURE public.insert_eligibility_benefit(
    IN p_check_id INTEGER,
    IN p_network VARCHAR(4),
    IN p_category VARCHAR(40),
    IN p_coverage_level VARCHAR(12),
    IN p_service_type_code VARCHAR(4),
    IN p_service_type VARCHAR(120),
    IN p_amount NUMERIC(10,2),
    IN p_percent NUMERIC(5,2),
    IN p_time_period VARCHAR(30),
    IN p_auth_required BOOLEAN,
    IN p_message VARCHAR(300)
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Required field validation
    IF p_check_id IS NULL THEN
        RAISE EXCEPTION 'Eligibility check ID is required';
    END IF;

    IF p_network IS NULL OR BTRIM(p_network) = '' THEN
        RAISE EXCEPTION 'Network is required';
    END IF;

    IF p_category IS NULL OR BTRIM(p_category) = '' THEN
        RAISE EXCEPTION 'Category is required';
    END IF;

    IF p_coverage_level IS NULL OR BTRIM(p_coverage_level) = '' THEN
        RAISE EXCEPTION 'Coverage level is required';
    END IF;

    IF p_service_type_code IS NULL OR BTRIM(p_service_type_code) = '' THEN
        RAISE EXCEPTION 'Service type code is required';
    END IF;

    IF p_service_type IS NULL OR BTRIM(p_service_type) = '' THEN
        RAISE EXCEPTION 'Service type is required';
    END IF;

    -- Validate eligibility check
    IF NOT EXISTS (
        SELECT 1
        FROM public.eligibility_checks
        WHERE id = p_check_id
    ) THEN
        RAISE EXCEPTION 'Eligibility check ID % does not exist', p_check_id;
    END IF;

    -- Validate amount
    IF p_amount IS NOT NULL AND p_amount < 0 THEN
        RAISE EXCEPTION 'Amount cannot be negative';
    END IF;

    -- Validate percentage
    IF p_percent IS NOT NULL AND (p_percent < 0 OR p_percent > 100) THEN
        RAISE EXCEPTION 'Percentage must be between 0 and 100';
    END IF;

    INSERT INTO public.eligibility_benefits (
        check_id,
        network,
        category,
        coverage_level,
        service_type_code,
        service_type,
        amount,
        "percent",
        time_period,
        auth_required,
        message
    )
    VALUES (
        p_check_id,
        p_network,
        p_category,
        p_coverage_level,
        p_service_type_code,
        p_service_type,
        p_amount,
        p_percent,
        p_time_period,
        p_auth_required,
        p_message
    );
END;
$$;
-----------------------------------------------------------------------------------
--24
CREATE OR REPLACE PROCEDURE public.insert_appointment(
    IN p_appointment_id VARCHAR(64),
    IN p_session_id VARCHAR(64),
    IN p_patient_id VARCHAR(128),
    IN p_icd_10 TEXT,
    IN p_patient_name TEXT,
    IN p_scheduled_for TIMESTAMPTZ,
    IN p_duration_minutes INTEGER,
    IN p_clinician TEXT,
    IN p_reason TEXT,
    IN p_status TEXT,
    IN p_booked_via TEXT,
    IN p_cpt TEXT,
    IN p_patient_email TEXT,
    IN p_email_status TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Validate appointment ID
    IF p_appointment_id IS NULL
       OR BTRIM(p_appointment_id) = '' THEN
        RAISE EXCEPTION 'Appointment ID is required';
    END IF;

    -- Check duplicate appointment ID
    IF EXISTS (
        SELECT 1
        FROM public.appointments
        WHERE appointment_id = p_appointment_id
    ) THEN
        RAISE EXCEPTION 'Appointment ID % already exists',
            p_appointment_id;
    END IF;

    -- Validate duration
    IF p_duration_minutes IS NOT NULL
       AND p_duration_minutes <= 0 THEN
        RAISE EXCEPTION 'Duration must be greater than zero';
    END IF;

    -- Validate booked appointment details
    IF p_status = 'booked' THEN
        IF p_scheduled_for IS NULL THEN
            RAISE EXCEPTION
                'Scheduled date and time are required for booked appointments';
        END IF;

        IF p_clinician IS NULL
           OR BTRIM(p_clinician) = '' THEN
            RAISE EXCEPTION
                'Clinician is required for booked appointments';
        END IF;

        -- Check existing booked slot
        IF EXISTS (
            SELECT 1
            FROM public.appointments a
            WHERE a.scheduled_for = p_scheduled_for
              AND a.clinician = p_clinician
              AND a.status = 'booked'
        ) THEN
            RAISE EXCEPTION
                'This clinician already has a booked appointment at the requested time';
        END IF;
    END IF;

    INSERT INTO public.appointments (
        appointment_id,
        session_id,
        patient_id,
        icd_10,
        patient_name,
        scheduled_for,
        duration_minutes,
        clinician,
        reason,
        status,
        booked_via,
        cpt,
        patient_email,
        email_status,
        created_at,
        updated_at
    )
    VALUES (
        p_appointment_id,
        p_session_id,
        p_patient_id,
        p_icd_10,
        p_patient_name,
        p_scheduled_for,
        p_duration_minutes,
        p_clinician,
        p_reason,
        p_status,
        p_booked_via,
        p_cpt,
        p_patient_email,
        p_email_status,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    );
END;
$$;
---------------------------------------------------------------------------------------
--25
CREATE OR REPLACE PROCEDURE public.insert_app_setting(
    IN p_key VARCHAR(100),
    IN p_value JSONB,
    IN p_updated_by VARCHAR(120)
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Validate key
    IF p_key IS NULL OR BTRIM(p_key) = '' THEN
        RAISE EXCEPTION 'Setting key is required';
    END IF;

    -- Validate value
    IF p_value IS NULL THEN
        RAISE EXCEPTION 'Setting value is required';
    END IF;

    -- Check duplicate key
    IF EXISTS (
        SELECT 1
        FROM public.app_settings
        WHERE key = p_key
    ) THEN
        RAISE EXCEPTION 'Setting key % already exists', p_key;
    END IF;

    INSERT INTO public.app_settings (
        key,
        value,
        updated_by,
        updated_at
    )
    VALUES (
        p_key,
        p_value,
        p_updated_by,
        CURRENT_TIMESTAMP
    );
END;
$$;
----------------------------------------------------------------------------------------