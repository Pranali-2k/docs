-- 0001_chart_addons: triggers and stored procedures for the chart add-on tables.
-- Tables and columns are created from app/models.py and app/models_chart.py (db.create_all); this script
-- must run AFTER them. It is idempotent (CREATE OR REPLACE, DROP TRIGGER IF EXISTS).
-- Apply with:  python -m scripts.apply_migrations

-- ======================================================================= generic helpers

CREATE OR REPLACE FUNCTION chart_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Direct database writes (psql, ETL, another tool) are recorded in audit_log. Writes from the API connect with
-- application_name = 'docshealth-api' and are audited by the application with richer detail, so they are skipped.
CREATE OR REPLACE FUNCTION chart_audit_direct() RETURNS trigger AS $$
DECLARE
    rec record;
    pid integer;
BEGIN
    IF TG_OP = 'DELETE' THEN rec := OLD; ELSE rec := NEW; END IF;
    IF current_setting('application_name', true) = 'docshealth-api' THEN
        RETURN rec;
    END IF;
    pid := rec.patient_id;
    INSERT INTO audit_log (entity_type, entity_id, action, actor, details, created_at)
    VALUES ('patient', pid, TG_TABLE_NAME || '_' || lower(TG_OP),
            'db:' || session_user,
            jsonb_build_object('table', TG_TABLE_NAME, 'row_id', rec.id, 'direct', true),
            now());
    RETURN rec;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['patient_alerts','patient_allergies','patient_notes','patient_reminders','patient_flags',
                             'tasks','medications','drug_interaction_rules','clinical_orders','procedure_logs',
                             'consent_templates','patient_consents']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_touch ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_touch BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION chart_touch_updated_at()', t);
    END LOOP;
    FOREACH t IN ARRAY ARRAY['patient_alerts','patient_allergies','patient_flags','medications','clinical_orders',
                             'procedure_logs','patient_consents','patient_links','tasks','patient_reminders','patient_notes']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_audit_direct ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_audit_direct AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION chart_audit_direct()', t);
    END LOOP;
END $$;

-- ======================================================================= status rules

-- Tasks: open -> in_progress -> done; open or in_progress -> cancelled; in_progress -> open. done/cancelled are final.
CREATE OR REPLACE FUNCTION chart_task_rules() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.status <> NEW.status THEN
        IF NOT ((OLD.status = 'open'        AND NEW.status IN ('in_progress','done','cancelled'))
             OR (OLD.status = 'in_progress' AND NEW.status IN ('open','done','cancelled'))) THEN
            RAISE EXCEPTION 'Invalid task status change: % -> %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.status = 'done' THEN
        NEW.completed_at := COALESCE(NEW.completed_at, now());
    ELSE
        NEW.completed_at := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_task_rules ON tasks;
CREATE TRIGGER trg_task_rules BEFORE INSERT OR UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION chart_task_rules();

-- Reminders: open -> done | cancelled; both final.
CREATE OR REPLACE FUNCTION chart_reminder_rules() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.status <> NEW.status AND OLD.status <> 'open' THEN
        RAISE EXCEPTION 'Invalid reminder status change: % -> %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'done' THEN
        NEW.completed_at := COALESCE(NEW.completed_at, now());
    ELSE
        NEW.completed_at := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_reminder_rules ON patient_reminders;
CREATE TRIGGER trg_reminder_rules BEFORE INSERT OR UPDATE ON patient_reminders FOR EACH ROW EXECUTE FUNCTION chart_reminder_rules();

-- Orders: draft -> ordered | cancelled; ordered -> completed | cancelled; completed/cancelled are final.
CREATE OR REPLACE FUNCTION chart_order_rules() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.status <> NEW.status THEN
        IF NOT ((OLD.status = 'draft'   AND NEW.status IN ('ordered','cancelled'))
             OR (OLD.status = 'ordered' AND NEW.status IN ('completed','cancelled'))) THEN
            RAISE EXCEPTION 'Invalid order status change: % -> %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.status = 'ordered'   THEN NEW.ordered_on   := COALESCE(NEW.ordered_on, current_date); END IF;
    IF NEW.status = 'completed' THEN NEW.completed_on := COALESCE(NEW.completed_on, current_date); END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_order_rules ON clinical_orders;
CREATE TRIGGER trg_order_rules BEFORE INSERT OR UPDATE ON clinical_orders FOR EACH ROW EXECUTE FUNCTION chart_order_rules();

-- Medications: active -> stopped | completed; stopped -> active (restart); completed is final.
-- Leaving "active" sets stop_on; returning to it clears stop_on.
CREATE OR REPLACE FUNCTION chart_medication_rules() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.status <> NEW.status THEN
        IF NOT ((OLD.status = 'active'  AND NEW.status IN ('stopped','completed'))
             OR (OLD.status = 'stopped' AND NEW.status = 'active')) THEN
            RAISE EXCEPTION 'Invalid medication status change: % -> %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.status = 'active' THEN
        NEW.stop_on := NULL;
    ELSE
        NEW.stop_on := COALESCE(NEW.stop_on, current_date);
    END IF;
    IF NEW.start_on IS NOT NULL AND NEW.stop_on IS NOT NULL AND NEW.stop_on < NEW.start_on THEN
        RAISE EXCEPTION 'Stop date is before start date' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_medication_rules ON medications;
CREATE TRIGGER trg_medication_rules BEFORE INSERT OR UPDATE ON medications FOR EACH ROW EXECUTE FUNCTION chart_medication_rules();

-- Consents: pending -> signed | revoked; signed -> revoked; revoked is final. Signing needs a signer name.
-- A new consent records the template version it was issued against.
CREATE OR REPLACE FUNCTION chart_consent_rules() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT version INTO NEW.template_version FROM consent_templates WHERE id = NEW.template_id;
    ELSIF OLD.status <> NEW.status THEN
        IF NOT ((OLD.status = 'pending' AND NEW.status IN ('signed','revoked'))
             OR (OLD.status = 'signed'  AND NEW.status = 'revoked')) THEN
            RAISE EXCEPTION 'Invalid consent status change: % -> %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.status = 'signed' THEN
        IF NEW.signed_by_name IS NULL OR btrim(NEW.signed_by_name) = '' THEN
            RAISE EXCEPTION 'A signed consent needs the name of the signer' USING ERRCODE = 'check_violation';
        END IF;
        NEW.signed_on := COALESCE(NEW.signed_on, current_date);
    END IF;
    IF NEW.status = 'revoked' THEN
        NEW.revoked_at := COALESCE(NEW.revoked_at, now());
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_consent_rules ON patient_consents;
CREATE TRIGGER trg_consent_rules BEFORE INSERT OR UPDATE ON patient_consents FOR EACH ROW EXECUTE FUNCTION chart_consent_rules();

-- Interaction rules: store each pair once, trimmed, lower-case and in alphabetical order.
CREATE OR REPLACE FUNCTION chart_rule_normalize() RETURNS trigger AS $$
DECLARE tmp text;
BEGIN
    NEW.drug_a := lower(btrim(NEW.drug_a));
    NEW.drug_b := lower(btrim(NEW.drug_b));
    IF NEW.drug_a = NEW.drug_b THEN
        RAISE EXCEPTION 'An interaction rule needs two different drugs' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.drug_a > NEW.drug_b THEN
        tmp := NEW.drug_a; NEW.drug_a := NEW.drug_b; NEW.drug_b := tmp;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_rule_normalize ON drug_interaction_rules;
CREATE TRIGGER trg_rule_normalize BEFORE INSERT OR UPDATE ON drug_interaction_rules FOR EACH ROW EXECUTE FUNCTION chart_rule_normalize();

-- ======================================================================= linked accounts

-- A link is stored in both directions. Inserting or deleting one side maintains the other.
CREATE OR REPLACE FUNCTION chart_link_mirror() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO patient_links (patient_id, linked_patient_id, relationship_label, created_by, created_at)
        VALUES (NEW.linked_patient_id, NEW.patient_id, NEW.relationship_label, NEW.created_by, NEW.created_at)
        ON CONFLICT (patient_id, linked_patient_id) DO NOTHING;
        RETURN NEW;
    END IF;
    DELETE FROM patient_links WHERE patient_id = OLD.linked_patient_id AND linked_patient_id = OLD.patient_id;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_link_mirror ON patient_links;
CREATE TRIGGER trg_link_mirror AFTER INSERT OR DELETE ON patient_links FOR EACH ROW EXECUTE FUNCTION chart_link_mirror();

-- ======================================================================= stored procedures

-- Link two charts. Safe to repeat. Raises if either chart is missing or both are the same.
CREATE OR REPLACE PROCEDURE link_patients(p_patient integer, p_other integer, p_relationship text, p_actor text)
LANGUAGE plpgsql AS $$
BEGIN
    IF p_patient = p_other THEN
        RAISE EXCEPTION 'A chart cannot be linked to itself' USING ERRCODE = 'check_violation';
    END IF;
    IF (SELECT count(*) FROM patients WHERE id IN (p_patient, p_other)) <> 2 THEN
        RAISE EXCEPTION 'Patient not found' USING ERRCODE = 'foreign_key_violation';
    END IF;
    INSERT INTO patient_links (patient_id, linked_patient_id, relationship_label, created_by, created_at)
    VALUES (p_patient, p_other, p_relationship, p_actor, now())
    ON CONFLICT (patient_id, linked_patient_id) DO UPDATE SET relationship_label = EXCLUDED.relationship_label;
END;
$$;

CREATE OR REPLACE PROCEDURE unlink_patients(p_patient integer, p_other integer)
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM patient_links WHERE patient_id = p_patient AND linked_patient_id = p_other;
    DELETE FROM patient_links WHERE patient_id = p_other AND linked_patient_id = p_patient;
END;
$$;

-- Patient Clipboard: what front-desk and clinical staff need to see at a glance.
CREATE OR REPLACE FUNCTION patient_clipboard(p_patient integer) RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'alerts',      COALESCE((SELECT jsonb_agg(jsonb_build_object('id', id, 'text', text, 'severity', severity) ORDER BY id)
                                 FROM patient_alerts WHERE patient_id = p_patient AND is_active), '[]'::jsonb),
        'allergies',   COALESCE((SELECT jsonb_agg(jsonb_build_object('id', id, 'allergen', allergen, 'reaction', reaction, 'severity', severity) ORDER BY id)
                                 FROM patient_allergies WHERE patient_id = p_patient AND status = 'active'), '[]'::jsonb),
        'flags',       COALESCE((SELECT jsonb_agg(jsonb_build_object('id', id, 'flag_type', flag_type, 'note', note) ORDER BY id)
                                 FROM patient_flags WHERE patient_id = p_patient AND is_active), '[]'::jsonb),
        'sticky_notes', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', id, 'body', body) ORDER BY id DESC)
                                 FROM patient_notes WHERE patient_id = p_patient AND kind = 'sticky'), '[]'::jsonb),
        'open_reminders', (SELECT count(*) FROM patient_reminders WHERE patient_id = p_patient AND status = 'open'),
        'open_tasks',  (SELECT count(*) FROM tasks WHERE patient_id = p_patient AND status IN ('open','in_progress')),
        'active_medications', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', id, 'drug_name', drug_name, 'strength', strength, 'frequency', frequency) ORDER BY drug_name)
                                 FROM medications WHERE patient_id = p_patient AND status = 'active'), '[]'::jsonb),
        'pending_orders', (SELECT count(*) FROM clinical_orders WHERE patient_id = p_patient AND status IN ('draft','ordered')),
        'pending_consents', (SELECT count(*) FROM patient_consents WHERE patient_id = p_patient AND status = 'pending'),
        'linked_accounts', (SELECT count(*) FROM patient_links WHERE patient_id = p_patient)
    );
$$;

-- Overdue work per staff member (reporting): open tasks and reminders whose due date has passed.
CREATE OR REPLACE FUNCTION overdue_work_report(p_as_of date DEFAULT current_date)
RETURNS TABLE (assignee_id integer, assignee_email text, overdue_tasks bigint, overdue_reminders bigint)
LANGUAGE sql STABLE AS $$
    WITH t AS (
        SELECT assigned_to_id AS uid, count(*) AS n FROM tasks
        WHERE status IN ('open','in_progress') AND due_on < p_as_of GROUP BY assigned_to_id
    ), r AS (
        SELECT NULL::integer AS uid, count(*) AS n FROM patient_reminders WHERE status = 'open' AND due_on < p_as_of
    )
    SELECT t.uid, u.email::text, t.n, 0::bigint FROM t LEFT JOIN users u ON u.id = t.uid
    UNION ALL
    SELECT NULL, 'reminders (not assigned)', 0::bigint, r.n FROM r WHERE r.n > 0
    ORDER BY 1 NULLS LAST;
$$;
