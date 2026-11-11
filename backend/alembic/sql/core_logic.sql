-- core_logic: database rules, triggers, stored procedures and report functions for the core business tables.
-- Applied once by Alembic revision 0004 (tables and columns come from revisions 0001 and 0003).
-- PostgreSQL 14+; btree_gist is a trusted extension, so the schema owner may create it.

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ======================================================================= who is acting

-- The API sets app.actor (the signed-in person's email) at the start of every transaction. Background work and direct
-- SQL have none, which reads as 'system'.
CREATE OR REPLACE FUNCTION app_actor() RETURNS text LANGUAGE sql STABLE AS $$
    SELECT COALESCE(NULLIF(current_setting('app.actor', true), ''), 'system')
$$;

-- ======================================================================= stamping: created_by / updated_by / updated_at

-- jsonb_populate_record ignores keys a table does not have, so one function serves every core table.
CREATE OR REPLACE FUNCTION core_stamp() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW := jsonb_populate_record(NEW, jsonb_build_object(
            'created_by', COALESCE(to_jsonb(NEW) ->> 'created_by', app_actor()),
            'updated_by', app_actor(),
            'updated_at', now()));
    ELSE
        NEW := jsonb_populate_record(NEW, jsonb_build_object('updated_by', app_actor(), 'updated_at', now()));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ======================================================================= change history

-- Writes to record_history only, which has no triggers, so it cannot recurse. Sensitive columns keep no values.
CREATE OR REPLACE FUNCTION core_history() RETURNS trigger AS $$
DECLARE
    old_j jsonb;
    new_j jsonb;
    diff  jsonb := '{}'::jsonb;
    k     text;
    quiet text[] := ARRAY['updated_at', 'updated_by', 'extracted', 'match_details', 'draft_chart'];
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO record_history (table_name, record_id, operation, changed_by, changes)
        VALUES (TG_TABLE_NAME, NEW.id, 'insert', app_actor(), '{}'::jsonb);
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        old_j := to_jsonb(OLD);
        new_j := to_jsonb(NEW);
        FOR k IN SELECT jsonb_object_keys(new_j) LOOP
            CONTINUE WHEN k = ANY (quiet) OR old_j -> k IS NOT DISTINCT FROM new_j -> k;
            IF k LIKE '%\_encrypted' THEN
                diff := diff || jsonb_build_object(k, jsonb_build_object('changed', true));
            ELSE
                diff := diff || jsonb_build_object(k, jsonb_build_object('from', old_j -> k, 'to', new_j -> k));
            END IF;
        END LOOP;
        IF diff <> '{}'::jsonb THEN
            INSERT INTO record_history (table_name, record_id, operation, changed_by, changes)
            VALUES (TG_TABLE_NAME, NEW.id, 'update', app_actor(), diff);
        END IF;
        RETURN NEW;
    END IF;
    old_j := to_jsonb(OLD);
    FOR k IN SELECT jsonb_object_keys(old_j) LOOP
        CONTINUE WHEN k = ANY (quiet) OR k LIKE '%\_encrypted';
        diff := diff || jsonb_build_object(k, old_j -> k);
    END LOOP;
    INSERT INTO record_history (table_name, record_id, operation, changed_by, changes)
    VALUES (TG_TABLE_NAME, OLD.id, 'delete', app_actor(), diff);
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['patients', 'insurance_policies', 'guarantors', 'phi_releases', 'appointments',
                             'portal_requests', 'providers', 'provider_blocks']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_core_stamp ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_core_stamp BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION core_stamp()', t);
        EXECUTE format('DROP TRIGGER IF EXISTS trg_core_history ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_core_history AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION core_history()', t);
    END LOOP;
END $$;

-- ======================================================================= integrity rules

-- No double booking: one provider cannot have two booked visits that overlap, and neither can a patient.
ALTER TABLE appointments ADD CONSTRAINT ck_appt_range CHECK (end_at > start_at);
ALTER TABLE appointments ADD CONSTRAINT ex_appt_provider_overlap
    EXCLUDE USING gist (provider_id WITH =, tsrange(start_at, end_at) WITH &&) WHERE (status = 'booked');
ALTER TABLE appointments ADD CONSTRAINT ex_appt_patient_overlap
    EXCLUDE USING gist (patient_id WITH =, tsrange(start_at, end_at) WITH &&) WHERE (status = 'booked');
ALTER TABLE appointments ADD CONSTRAINT ck_appt_status CHECK (status IN ('booked', 'cancelled', 'completed', 'no_show'));

ALTER TABLE portal_requests ADD CONSTRAINT ck_request_status CHECK (
    status IN ('received', 'processing', 'auto_responded', 'needs_review', 'in_review', 'resolved', 'closed'));

-- A request that is resolved or closed can only be reopened (back to needs_review). resolved_at tracks the final states.
CREATE OR REPLACE FUNCTION request_status_rules() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('resolved', 'closed') AND NEW.status NOT IN (OLD.status, 'needs_review') THEN
        RAISE EXCEPTION 'A % request can only be reopened, not changed to %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IN ('resolved', 'closed') THEN
        NEW.resolved_at := COALESCE(NEW.resolved_at, now());
    ELSE
        NEW.resolved_at := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_request_status ON portal_requests;
CREATE TRIGGER trg_request_status BEFORE UPDATE OF status ON portal_requests
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION request_status_rules();

-- ======================================================================= scheduling procedures

-- Every procedure writes its own audit_log rows, so the application does not repeat that logic.
-- Rule violations raise with SQLSTATE P0001 and a message fit to show staff.

CREATE OR REPLACE FUNCTION book_appointment(
    p_patient integer, p_provider integer, p_start timestamp, p_reason text, p_request integer, p_actor text,
    p_minutes integer DEFAULT NULL
) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    v_name    text;
    v_minutes integer;
    v_end     timestamp;
    v_reason  text;
    v_id      integer;
    v_when    text := to_char(p_start, 'YYYY-MM-DD HH24:MI');
BEGIN
    SELECT full_name INTO v_name FROM providers WHERE id = p_provider;
    IF NOT FOUND THEN RAISE EXCEPTION 'Provider not found'; END IF;
    IF NOT EXISTS (SELECT 1 FROM patients WHERE id = p_patient) THEN RAISE EXCEPTION 'Patient not found'; END IF;

    SELECT slot_minutes INTO v_minutes FROM provider_availability
     WHERE provider_id = p_provider AND weekday = EXTRACT(isodow FROM p_start)::int - 1 LIMIT 1;
    v_minutes := COALESCE(p_minutes, v_minutes, 30);
    IF v_minutes < 1 THEN RAISE EXCEPTION 'Appointment length must be at least one minute'; END IF;
    v_end := p_start + make_interval(mins => v_minutes);

    SELECT reason INTO v_reason FROM provider_blocks
     WHERE provider_id = p_provider AND start_at < v_end AND end_at > p_start LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION '%', format('%s is unavailable at %s%s', v_name, v_when, COALESCE(' (' || v_reason || ')', ''));
    END IF;
    IF EXISTS (SELECT 1 FROM appointments WHERE provider_id = p_provider AND status = 'booked'
                  AND start_at < v_end AND end_at > p_start) THEN
        RAISE EXCEPTION '% is already booked at %', v_name, v_when;
    END IF;
    IF EXISTS (SELECT 1 FROM appointments WHERE patient_id = p_patient AND status = 'booked'
                  AND start_at < v_end AND end_at > p_start) THEN
        RAISE EXCEPTION 'Patient already has an appointment at that time';
    END IF;

    INSERT INTO appointments (patient_id, provider_id, start_at, end_at, status, visit_reason, source_request_id,
                              booked_by, created_at)
    VALUES (p_patient, p_provider, p_start, v_end, 'booked', p_reason, p_request, COALESCE(p_actor, 'system'), now())
    RETURNING id INTO v_id;

    INSERT INTO audit_log (entity_type, entity_id, action, actor, details, created_at) VALUES
        ('appointment', v_id, 'appointment_booked', COALESCE(p_actor, 'system'),
         jsonb_build_object('patient_id', p_patient, 'provider_id', p_provider,
                            'start', to_char(p_start, 'YYYY-MM-DD"T"HH24:MI:SS'), 'request_id', p_request), now()),
        ('patient', p_patient, 'chart_updated', COALESCE(p_actor, 'system'),
         jsonb_build_object('change', 'appointment_booked', 'appointment_id', v_id), now());
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION cancel_appointment(p_appointment integer, p_actor text, p_request integer DEFAULT NULL)
RETURNS void
LANGUAGE plpgsql AS $$
DECLARE a appointments%ROWTYPE;
BEGIN
    SELECT * INTO a FROM appointments WHERE id = p_appointment FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Appointment not found'; END IF;
    IF a.status <> 'booked' THEN
        RAISE EXCEPTION 'Appointment is %, not booked', a.status;
    END IF;
    UPDATE appointments SET status = 'cancelled', cancelled_at = now() WHERE id = p_appointment;
    INSERT INTO audit_log (entity_type, entity_id, action, actor, details, created_at) VALUES
        ('appointment', a.id, 'appointment_cancelled', COALESCE(p_actor, 'system'),
         jsonb_build_object('patient_id', a.patient_id, 'start', to_char(a.start_at, 'YYYY-MM-DD"T"HH24:MI:SS'),
                            'request_id', p_request), now()),
        ('patient', a.patient_id, 'chart_updated', COALESCE(p_actor, 'system'),
         jsonb_build_object('change', 'appointment_cancelled', 'appointment_id', a.id), now());
END;
$$;

-- Move a visit: free the old slot, then book the new one, in one transaction. If the new slot is not available the
-- whole call fails and the original appointment is untouched. Returns the new appointment id.
CREATE OR REPLACE FUNCTION reschedule_appointment(
    p_old integer, p_provider integer, p_start timestamp, p_reason text, p_request integer, p_actor text
) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE v_patient integer; v_new integer;
BEGIN
    SELECT patient_id INTO v_patient FROM appointments WHERE id = p_old FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Appointment not found'; END IF;
    PERFORM cancel_appointment(p_old, p_actor, p_request);
    v_new := book_appointment(v_patient, p_provider, p_start, p_reason, p_request, p_actor);
    RETURN v_new;
END;
$$;

-- ======================================================================= request workflow procedures

CREATE OR REPLACE FUNCTION assign_request(p_request integer, p_assignee text, p_actor text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
    IF COALESCE(btrim(p_assignee), '') = '' THEN p_assignee := p_actor; END IF;
    UPDATE portal_requests
       SET assigned_to = p_assignee,
           status = CASE WHEN status = 'needs_review' THEN 'in_review' ELSE status END
     WHERE id = p_request RETURNING status INTO v_status;
    IF NOT FOUND THEN RAISE EXCEPTION 'Request not found'; END IF;
    INSERT INTO audit_log (entity_type, entity_id, action, actor, details, created_at)
    VALUES ('request', p_request, 'assigned', p_actor, jsonb_build_object('assignee', p_assignee), now());
    RETURN v_status;
END;
$$;

CREATE OR REPLACE FUNCTION resolve_request(p_request integer, p_note text, p_close boolean, p_actor text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_status text := CASE WHEN p_close THEN 'closed' ELSE 'resolved' END;
BEGIN
    IF COALESCE(btrim(p_note), '') = '' THEN RAISE EXCEPTION 'A resolution note is required'; END IF;
    UPDATE portal_requests
       SET status = v_status, resolution_note = p_note, resolved_by = p_actor, resolved_at = now()
     WHERE id = p_request;
    IF NOT FOUND THEN RAISE EXCEPTION 'Request not found'; END IF;
    INSERT INTO audit_log (entity_type, entity_id, action, actor, details, created_at)
    VALUES ('request', p_request, CASE WHEN p_close THEN 'request_closed' ELSE 'request_resolved' END, p_actor,
            jsonb_build_object('note', p_note), now());
    RETURN v_status;
END;
$$;

CREATE OR REPLACE FUNCTION reopen_request(p_request integer, p_actor text) RETURNS text
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE portal_requests SET status = 'needs_review', resolved_at = NULL WHERE id = p_request;
    IF NOT FOUND THEN RAISE EXCEPTION 'Request not found'; END IF;
    INSERT INTO audit_log (entity_type, entity_id, action, actor, details, created_at)
    VALUES ('request', p_request, 'request_reopened', p_actor, '{}'::jsonb, now());
    RETURN 'needs_review';
END;
$$;

-- ======================================================================= dashboard and reports

-- Everything the dashboard shows for the last p_days days (mailbox statistics are added by the API).
-- p_clinic_today is the clinic-local date, which the API knows from CLINIC_TIMEZONE.
CREATE OR REPLACE FUNCTION dashboard_summary(p_days integer, p_clinic_today date) RETURNS jsonb
LANGUAGE sql STABLE AS $$
    WITH r AS (
        SELECT * FROM portal_requests WHERE created_at >= now() - make_interval(days => p_days)
    ), totals AS (
        SELECT count(*) AS total,
               count(*) FILTER (WHERE decision = 'auto_respond') AS auto,
               count(*) FILTER (WHERE status IN ('received','processing','needs_review','in_review','auto_responded')) AS open_n,
               count(*) FILTER (WHERE status IN ('needs_review','in_review')) AS review_n,
               count(*) FILTER (WHERE priority = 'high' AND status IN ('received','processing','needs_review','in_review','auto_responded')) AS high_open,
               round((avg(EXTRACT(epoch FROM resolved_at - created_at) / 60)
                      FILTER (WHERE resolved_at IS NOT NULL))::numeric, 1) AS resolve_min,
               round((avg(EXTRACT(epoch FROM first_response_at - created_at) / 60)
                      FILTER (WHERE first_response_at IS NOT NULL))::numeric, 1) AS response_min
          FROM r
    )
    SELECT jsonb_build_object(
        'window_days', p_days,
        'total_requests', t.total,
        'open_requests', t.open_n,
        'needs_review', t.review_n,
        'auto_response_rate', CASE WHEN t.total = 0 THEN 0 ELSE round(t.auto::numeric / t.total, 3) END,
        'auto_responded', t.auto,
        'avg_minutes_to_resolve', t.resolve_min,
        'avg_minutes_to_first_response', t.response_min,
        'by_status', COALESCE((SELECT jsonb_object_agg(status, n) FROM (SELECT status, count(*) AS n FROM r GROUP BY status) s), '{}'::jsonb),
        'by_intent', COALESCE((SELECT jsonb_object_agg(intent, n) FROM (SELECT COALESCE(intent, 'unknown') AS intent, count(*) AS n FROM r GROUP BY 1) s), '{}'::jsonb),
        'review_reasons', COALESCE((SELECT jsonb_object_agg(code, n)
                                      FROM (SELECT x ->> 'code' AS code, count(*) AS n
                                              FROM r, jsonb_array_elements(COALESCE(r.review_reasons, '[]'::jsonb)) x
                                             GROUP BY 1) s), '{}'::jsonb),
        'daily', COALESCE((SELECT jsonb_agg(jsonb_build_object('date', d, 'received', rec, 'auto', a, 'review', rec - a) ORDER BY d)
                             FROM (SELECT to_char((created_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD') AS d, count(*) AS rec,
                                          count(*) FILTER (WHERE decision = 'auto_respond') AS a
                                     FROM r GROUP BY 1) s), '[]'::jsonb),
        'appointments_today', (SELECT count(*) FROM appointments WHERE status = 'booked'
                                  AND start_at >= p_clinic_today AND start_at < p_clinic_today + 1),
        'patients', (SELECT count(*) FROM patients),
        'high_priority_open', t.high_open
    ) FROM totals t;
$$;

-- Turnaround by request type for the last p_days days: how many, how many were answered automatically, and how long
-- first response and resolution took.
CREATE OR REPLACE FUNCTION request_turnaround_report(p_days integer DEFAULT 30)
RETURNS TABLE (intent text, total bigint, auto_responded bigint, needs_review bigint, resolved bigint,
               avg_minutes_to_first_response numeric, avg_minutes_to_resolve numeric)
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(r.intent, 'unknown'), count(*),
           count(*) FILTER (WHERE r.decision = 'auto_respond'),
           count(*) FILTER (WHERE r.status IN ('needs_review', 'in_review')),
           count(*) FILTER (WHERE r.status IN ('resolved', 'closed')),
           round((avg(EXTRACT(epoch FROM r.first_response_at - r.created_at) / 60)
                  FILTER (WHERE r.first_response_at IS NOT NULL))::numeric, 1),
           round((avg(EXTRACT(epoch FROM r.resolved_at - r.created_at) / 60)
                  FILTER (WHERE r.resolved_at IS NOT NULL))::numeric, 1)
      FROM portal_requests r
     WHERE r.created_at >= now() - make_interval(days => p_days)
     GROUP BY 1 ORDER BY 2 DESC, 1;
$$;
