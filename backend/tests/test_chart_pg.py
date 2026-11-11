"""Triggers and stored procedures from migrations/0001_chart_addons.sql, exercised with raw SQL on PostgreSQL.

Runs against DOCSHEALTH_TEST_DATABASE_URL (see conftest.py), whose schema is built from the Alembic migrations.
Every test runs in a transaction that is rolled back, so the database is left as it was.
"""
import os
from datetime import date

import pytest

import psycopg
from psycopg import errors

from tests.conftest import pg_url

URL = pg_url()


class Ctx:
    """A cursor plus the ids of two throwaway patients."""

    def __init__(self, cursor):
        self.c = cursor

    def execute(self, sql, params=None):
        return self.c.execute(sql, params)

    def fetchone(self):
        return self.c.fetchone()


@pytest.fixture()
def cur(_schema):
    with psycopg.connect(URL) as conn:
        c = Ctx(conn.cursor())
        for n, (mrn, first, last) in enumerate((("T1", "A", "One"), ("T2", "B", "Two")), 1):
            c.execute("insert into patients (mrn, first_name, last_name, created_at, updated_at) "
                      "values (%s,%s,%s,now(),now()) returning id", (mrn, first, last))
            setattr(c, f"p{n}", c.fetchone()[0])
        yield c
        conn.rollback()


def one(c, sql, *args):
    c.execute(sql, args)
    return c.fetchone()[0]


def test_task_status_trigger_enforces_transitions_and_completed_at(cur):
    tid = one(cur, "insert into tasks (patient_id, title) values (%s,'t') returning id", cur.p1)
    assert one(cur, "select completed_at from tasks where id=%s", tid) is None
    cur.execute("update tasks set status='done' where id=%s", (tid,))
    assert one(cur, "select completed_at is not null from tasks where id=%s", tid)
    with pytest.raises(errors.CheckViolation, match="Invalid task status change: done -> open"):
        cur.execute("update tasks set status='open' where id=%s", (tid,))


def test_reminder_order_medication_consent_triggers(cur):
    rid = one(cur, "insert into patient_reminders (patient_id, message) values (%s,'m') returning id", cur.p1)
    cur.execute("update patient_reminders set status='cancelled' where id=%s", (rid,))
    with pytest.raises(errors.CheckViolation):
        cur.execute("update patient_reminders set status='done' where id=%s", (rid,))


def test_order_trigger(cur):
    oid = one(cur, "insert into clinical_orders (patient_id, order_type, description) values (%s,'lab','CBC') returning id", cur.p1)
    with pytest.raises(errors.CheckViolation, match="draft -> completed"):
        cur.execute("update clinical_orders set status='completed' where id=%s", (oid,))


def test_order_sets_dates(cur):
    oid = one(cur, "insert into clinical_orders (patient_id, order_type, description) values (%s,'lab','CBC') returning id", cur.p1)
    cur.execute("update clinical_orders set status='ordered' where id=%s", (oid,))
    assert one(cur, "select ordered_on from clinical_orders where id=%s", oid) == date.today()
    cur.execute("update clinical_orders set status='completed' where id=%s", (oid,))
    assert one(cur, "select completed_on from clinical_orders where id=%s", oid) == date.today()


def test_medication_trigger_sets_and_clears_stop_date(cur):
    mid = one(cur, "insert into medications (patient_id, drug_name) values (%s,'X') returning id", cur.p1)
    cur.execute("update medications set status='stopped' where id=%s", (mid,))
    assert one(cur, "select stop_on from medications where id=%s", mid) == date.today()
    cur.execute("update medications set status='active' where id=%s", (mid,))
    assert one(cur, "select stop_on from medications where id=%s", mid) is None
    cur.execute("update medications set status='completed' where id=%s", (mid,))
    with pytest.raises(errors.CheckViolation):
        cur.execute("update medications set status='active' where id=%s", (mid,))


def test_medication_rejects_stop_before_start(cur):
    with pytest.raises(errors.CheckViolation, match="before start"):
        cur.execute("insert into medications (patient_id, drug_name, status, start_on, stop_on) "
                    "values (%s,'X','stopped','2026-02-01','2026-01-01')", (cur.p1,))


def test_consent_trigger_version_signature_and_revocation(cur):
    tid = one(cur, "insert into consent_templates (name, body, version) values ('T-pg','b',3) returning id")
    cid = one(cur, "insert into patient_consents (patient_id, template_id) values (%s,%s) returning id", cur.p1, tid)
    assert one(cur, "select template_version from patient_consents where id=%s", cid) == 3
    with pytest.raises(errors.CheckViolation, match="name of the signer"):
        cur.execute("update patient_consents set status='signed' where id=%s", (cid,))


def test_consent_signed_then_revoked(cur):
    tid = one(cur, "insert into consent_templates (name, body) values ('T-pg2','b') returning id")
    cid = one(cur, "insert into patient_consents (patient_id, template_id) values (%s,%s) returning id", cur.p1, tid)
    cur.execute("update patient_consents set status='signed', signed_by_name='A One' where id=%s", (cid,))
    assert one(cur, "select signed_on from patient_consents where id=%s", cid) == date.today()
    cur.execute("update patient_consents set status='revoked' where id=%s", (cid,))
    assert one(cur, "select revoked_at is not null from patient_consents where id=%s", cid)
    with pytest.raises(errors.CheckViolation):
        cur.execute("update patient_consents set status='signed' where id=%s", (cid,))


def test_rule_trigger_normalizes_pairs(cur):
    rid = one(cur, "insert into drug_interaction_rules (drug_a, drug_b, message) values ('  Warfarin ','ASPIRIN','m') returning id")
    cur.execute("select drug_a, drug_b from drug_interaction_rules where id=%s", (rid,))
    assert cur.fetchone() == ("aspirin", "warfarin")
    with pytest.raises(errors.CheckViolation, match="two different drugs"):
        cur.execute("insert into drug_interaction_rules (drug_a, drug_b, message) values ('a','A','m')")


def test_link_trigger_mirrors_and_cascades_deletes(cur):
    cur.execute("insert into patient_links (patient_id, linked_patient_id, relationship_label) values (%s,%s,'sibling')", (cur.p1, cur.p2))
    assert one(cur, "select count(*) from patient_links where patient_id=%s and linked_patient_id=%s", cur.p2, cur.p1) == 1
    cur.execute("delete from patient_links where patient_id=%s", (cur.p1,))
    assert one(cur, "select count(*) from patient_links where patient_id in (%s,%s)", cur.p1, cur.p2) == 0


def test_link_cannot_point_at_itself(cur):
    with pytest.raises(errors.CheckViolation):
        cur.execute("insert into patient_links (patient_id, linked_patient_id) values (%s,%s)", (cur.p1, cur.p1))


def test_link_procedures(cur):
    cur.execute("call link_patients(%s,%s,'spouse','tester')", (cur.p1, cur.p2))
    cur.execute("call link_patients(%s,%s,'partner','tester')", (cur.p1, cur.p2))     # repeat updates the label
    assert one(cur, "select count(*) from patient_links where patient_id in (%s,%s)", cur.p1, cur.p2) == 2
    assert one(cur, "select relationship_label from patient_links where patient_id=%s", cur.p1) == "partner"
    cur.execute("call unlink_patients(%s,%s)", (cur.p2, cur.p1))
    assert one(cur, "select count(*) from patient_links where patient_id in (%s,%s)", cur.p1, cur.p2) == 0
    with pytest.raises(errors.CheckViolation, match="itself"):
        cur.execute("call link_patients(%s,%s,null,'t')", (cur.p1, cur.p1))


def test_link_procedure_rejects_missing_patient(cur):
    with pytest.raises(errors.ForeignKeyViolation, match="not found"):
        cur.execute("call link_patients(%s, 999999, null, 't')", (cur.p1,))


def test_clipboard_function(cur):
    cur.execute("insert into patient_alerts (patient_id, text) values (%s,'A')", (cur.p1,))
    cur.execute("insert into patient_alerts (patient_id, text, is_active) values (%s,'old',false)", (cur.p1,))
    cur.execute("insert into patient_notes (patient_id, kind, body) values (%s,'sticky','S')", (cur.p1,))
    cur.execute("insert into tasks (patient_id, title) values (%s,'t')", (cur.p1,))
    data = one(cur, "select patient_clipboard(%s)", cur.p1)
    assert [a["text"] for a in data["alerts"]] == ["A"] and data["open_tasks"] == 1
    assert data["sticky_notes"][0]["body"] == "S" and data["allergies"] == [] and data["linked_accounts"] == 0


def test_overdue_report_function(cur):
    cur.execute("insert into tasks (patient_id, title, due_on) values (%s,'late','2020-01-01')", (cur.p1,))
    cur.execute("insert into tasks (patient_id, title, due_on) values (%s,'fine','2999-01-01')", (cur.p1,))
    cur.execute("select overdue_tasks from overdue_work_report('2026-11-11') where assignee_id is null and overdue_tasks > 0")
    assert cur.fetchone()[0] >= 1


def test_direct_writes_are_audited_but_api_writes_are_not(cur):
    cur.execute("insert into patient_alerts (patient_id, text) values (%s,'direct')", (cur.p1,))
    n = one(cur, "select count(*) from audit_log where entity_id=%s and action='patient_alerts_insert' and details->>'direct'='true'", cur.p1)
    assert n == 1
    cur.execute("set application_name = 'docshealth-api'")
    cur.execute("insert into patient_alerts (patient_id, text) values (%s,'via api')", (cur.p1,))
    assert one(cur, "select count(*) from audit_log where entity_id=%s and action='patient_alerts_insert'", cur.p1) == 1


def test_updated_at_is_touched(cur):
    aid = one(cur, "insert into patient_alerts (patient_id, text, updated_at) values (%s,'x','2020-01-01') returning id", cur.p1)
    cur.execute("update patient_alerts set text='y' where id=%s", (aid,))
    assert one(cur, "select updated_at > '2021-01-01' from patient_alerts where id=%s", aid)


def test_check_constraints_block_bad_values(cur):
    with pytest.raises(errors.CheckViolation):
        cur.execute("insert into patient_alerts (patient_id, text, severity) values (%s,'x','loud')", (cur.p1,))


def test_cascade_delete_of_patient(cur):
    cur.execute("insert into medications (patient_id, drug_name) values (%s,'X')", (cur.p1,))
    cur.execute("insert into patient_links (patient_id, linked_patient_id) values (%s,%s)", (cur.p1, cur.p2))
    cur.execute("delete from patients where id=%s", (cur.p1,))
    assert one(cur, "select count(*) from medications where patient_id=%s", cur.p1) == 0
    assert one(cur, "select count(*) from patient_links where patient_id in (%s,%s)", cur.p1, cur.p2) == 0
