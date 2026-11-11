"""Core database logic (alembic/sql/core_logic.sql), exercised with raw SQL on PostgreSQL: stamping and change-history
triggers, integrity rules, scheduling and request-workflow procedures, dashboard and report functions.

Each test runs in a transaction that is rolled back. Expected failures use a savepoint so the test can go on.
"""
import json
from datetime import date, timedelta

import psycopg
import pytest
from psycopg import errors

from tests.conftest import pg_url

MONDAY = "2030-01-07 09:00"  # isodow 1, so provider_availability weekday 0


class Ctx:
    def __init__(self, conn):
        self.conn = conn
        self.c = conn.cursor()
        self.n = 0

    def execute(self, sql, params=None):
        return self.c.execute(sql, params)

    def one(self, sql, params=None):
        self.c.execute(sql, params)
        return self.c.fetchone()[0]

    def rows(self, sql, params=None):
        self.c.execute(sql, params)
        return self.c.fetchall()

    def fails(self, exc, match, sql, params=None):
        """Run sql expecting a database error, then carry on."""
        self.c.execute("SAVEPOINT expect")
        with pytest.raises(exc, match=match):
            self.c.execute(sql, params)
        self.c.execute("ROLLBACK TO SAVEPOINT expect")

    def actor(self, who):
        self.c.execute("SELECT set_config('app.actor', %s, true)", (who,))

    def patient(self, last="Core"):
        self.n += 1
        return self.one("INSERT INTO patients (mrn, first_name, last_name, created_at, updated_at) "
                        "VALUES (%s, 'T', %s, now(), now()) RETURNING id", (f"CD{self.n}{id(self)}"[:25], last))

    def provider(self, slot=30):
        self.n += 1
        pid = self.one("INSERT INTO providers (full_name, is_active, location, npi, specialty) "
                       "VALUES ('Dr. Test', true, 'Room 1', %s, 'fm') RETURNING id", (f"NPI{self.n}-{id(self)}"[:20],))
        self.execute("INSERT INTO provider_availability (provider_id, weekday, start_time, end_time, slot_minutes) "
                     "VALUES (%s, 0, '08:00', '17:00', %s)", (pid, slot))
        return pid

    def request(self, status="needs_review"):
        self.n += 1
        return self.one("INSERT INTO portal_requests (reference, status, priority, extracted, match_details, review_reasons, "
                        "created_at, updated_at) VALUES (%s, %s, 'normal', '{}', '{}', '[]', now(), now()) RETURNING id",
                        (f"REF-{self.n}-{id(self)}", status))


@pytest.fixture()
def c(_schema):
    with psycopg.connect(pg_url()) as conn:
        yield Ctx(conn)
        conn.rollback()


# ----------------------------------------------------------------------- stamping

def test_stamp_sets_created_and_updated_by(c):
    c.actor("alice@example.com")
    pid = c.patient()
    assert c.rows("SELECT created_by, updated_by FROM patients WHERE id=%s", (pid,)) == [("alice@example.com",) * 2]
    c.actor("bob@example.com")
    c.execute("UPDATE patients SET last_name='Changed' WHERE id=%s", (pid,))
    assert c.rows("SELECT created_by, updated_by FROM patients WHERE id=%s", (pid,)) == [("alice@example.com", "bob@example.com")]


def test_stamp_defaults_to_system_and_advances_updated_at(c):
    pid = c.patient()
    assert c.one("SELECT created_by FROM patients WHERE id=%s", (pid,)) == "system"
    c.execute("UPDATE patients SET updated_at='2020-01-01' WHERE id=%s", (pid,))
    assert c.one("SELECT updated_at > '2021-01-01' FROM patients WHERE id=%s", (pid,))


def test_stamp_keeps_an_explicit_created_by(c):
    c.actor("alice@example.com")
    c.execute("INSERT INTO provider_blocks (provider_id, start_at, end_at, created_at, created_by) VALUES (%s,'2030-01-07 09:00','2030-01-07 10:00',now(),'importer')",
              (c.provider(),))
    assert c.one("SELECT created_by FROM provider_blocks") == "importer"


# ----------------------------------------------------------------------- history

def test_history_records_field_changes_once(c):
    c.actor("alice@example.com")
    pid = c.patient()
    c.execute("UPDATE patients SET last_name='New', city='Austin' WHERE id=%s", (pid,))
    rows = c.rows("SELECT operation, changed_by, changes FROM record_history WHERE table_name='patients' AND record_id=%s ORDER BY id", (pid,))
    assert [r[0] for r in rows] == ["insert", "update"]
    assert rows[1][1] == "alice@example.com"
    assert rows[1][2]["last_name"] == {"from": "Core", "to": "New"} and rows[1][2]["city"] == {"from": None, "to": "Austin"}
    assert "updated_at" not in rows[1][2] and "updated_by" not in rows[1][2]


def test_history_skips_updates_that_change_nothing_visible(c):
    pid = c.patient()
    c.execute("UPDATE patients SET updated_at=now() WHERE id=%s", (pid,))
    c.execute("UPDATE patients SET last_name=last_name WHERE id=%s", (pid,))
    assert c.one("SELECT count(*) FROM record_history WHERE table_name='patients' AND record_id=%s AND operation='update'", (pid,)) == 0


def test_history_never_stores_sensitive_values(c):
    pid = c.patient()
    c.execute("UPDATE patients SET ssn_encrypted='SECRET-TOKEN', ssn_last4='1234' WHERE id=%s", (pid,))
    changes = c.one("SELECT changes FROM record_history WHERE table_name='patients' AND record_id=%s AND operation='update'", (pid,))
    assert changes["ssn_encrypted"] == {"changed": True} and "SECRET" not in json.dumps(changes)
    c.execute("DELETE FROM patients WHERE id=%s", (pid,))
    snap = c.one("SELECT changes FROM record_history WHERE table_name='patients' AND record_id=%s AND operation='delete'", (pid,))
    assert "ssn_encrypted" not in snap and snap["last_name"] == "Core"


# ----------------------------------------------------------------------- integrity rules

def _appt(c, patient, provider, start, end, status="booked"):
    return c.one("INSERT INTO appointments (patient_id, provider_id, start_at, end_at, status, booked_by, created_at) "
                 "VALUES (%s,%s,%s,%s,%s,'t',now()) RETURNING id", (patient, provider, start, end, status))


def test_exclusion_constraints_block_double_booking(c):
    p1, p2, prov = c.patient(), c.patient(), c.provider()
    _appt(c, p1, prov, "2030-01-07 09:00", "2030-01-07 09:30")
    c.fails(errors.ExclusionViolation, "ex_appt_provider_overlap", "INSERT INTO appointments (patient_id, provider_id, start_at, end_at, status, booked_by, created_at) VALUES (%s,%s,'2030-01-07 09:15','2030-01-07 09:45','booked','t',now())", (p2, prov))
    other = c.provider()
    c.fails(errors.ExclusionViolation, "ex_appt_patient_overlap", "INSERT INTO appointments (patient_id, provider_id, start_at, end_at, status, booked_by, created_at) VALUES (%s,%s,'2030-01-07 09:15','2030-01-07 09:45','booked','t',now())", (p1, other))
    _appt(c, p2, prov, "2030-01-07 09:30", "2030-01-07 10:00")           # touching, not overlapping: fine


def test_cancelled_visits_do_not_block_a_slot(c):
    p, prov = c.patient(), c.provider()
    _appt(c, p, prov, "2030-01-07 09:00", "2030-01-07 09:30", status="cancelled")
    _appt(c, p, prov, "2030-01-07 09:00", "2030-01-07 09:30")


def test_appointment_checks(c):
    p, prov = c.patient(), c.provider()
    c.fails(errors.CheckViolation, "ck_appt_range", "INSERT INTO appointments (patient_id, provider_id, start_at, end_at, status, booked_by, created_at) VALUES (%s,%s,'2030-01-07 10:00','2030-01-07 09:00','booked','t',now())", (p, prov))
    c.fails(errors.CheckViolation, "ck_appt_status", "INSERT INTO appointments (patient_id, provider_id, start_at, end_at, status, booked_by, created_at) VALUES (%s,%s,'2030-01-07 10:00','2030-01-07 11:00','weird','t',now())", (p, prov))


def test_request_status_rules(c):
    r = c.request("received")
    c.fails(errors.CheckViolation, "ck_request_status", "UPDATE portal_requests SET status='bogus' WHERE id=%s", (r,))
    c.execute("UPDATE portal_requests SET status='resolved' WHERE id=%s", (r,))
    assert c.one("SELECT resolved_at IS NOT NULL FROM portal_requests WHERE id=%s", (r,))
    c.fails(errors.CheckViolation, "can only be reopened", "UPDATE portal_requests SET status='in_review' WHERE id=%s", (r,))
    c.fails(errors.CheckViolation, "can only be reopened", "UPDATE portal_requests SET status='closed' WHERE id=%s", (r,))
    c.execute("UPDATE portal_requests SET status='needs_review' WHERE id=%s", (r,))
    assert c.one("SELECT resolved_at IS NULL FROM portal_requests WHERE id=%s", (r,))


# ----------------------------------------------------------------------- scheduling procedures

def _book(c, patient, provider, start=MONDAY, reason="checkup", request=None, actor="tester"):
    return c.one("SELECT book_appointment(%s,%s,%s,%s,%s,%s)", (patient, provider, start, reason, request, actor))


def test_book_appointment_uses_the_slot_length_and_audits(c):
    p, prov = c.patient(), c.provider(slot=20)
    aid = _book(c, p, prov, request=c.request())
    row = c.rows("SELECT end_at - start_at, status, booked_by, visit_reason FROM appointments WHERE id=%s", (aid,))[0]
    assert row[0] == timedelta(minutes=20) and row[1:] == ("booked", "tester", "checkup")
    actions = {r[0] for r in c.rows("SELECT action FROM audit_log WHERE (entity_type='appointment' AND entity_id=%s) OR (entity_type='patient' AND entity_id=%s)", (aid, p))}
    assert {"appointment_booked", "chart_updated"} <= actions
    details = c.one("SELECT details FROM audit_log WHERE action='appointment_booked' AND entity_id=%s", (aid,))
    assert details["start"] == "2030-01-07T09:00:00" and details["patient_id"] == p


def test_book_appointment_defaults_to_thirty_minutes_without_a_template(c):
    p = c.patient()
    prov = c.one("INSERT INTO providers (full_name, is_active, location, npi, specialty) VALUES ('Dr. None', true, 'R', %s, 'fm') RETURNING id", (f"NONE-{id(c)}",))
    aid = _book(c, p, prov)
    assert c.one("SELECT end_at - start_at FROM appointments WHERE id=%s", (aid,)) == timedelta(minutes=30)


def test_book_appointment_rejects_conflicts_with_clear_messages(c):
    p1, p2, prov = c.patient(), c.patient(), c.provider()
    _book(c, p1, prov)
    c.fails(errors.RaiseException, r"Dr\. Test is already booked at 2030-01-07 09:00", "SELECT book_appointment(%s,%s,%s,'x',NULL,'t')", (p2, prov, MONDAY))
    c.fails(errors.RaiseException, "Patient already has an appointment at that time", "SELECT book_appointment(%s,%s,%s,'x',NULL,'t')", (p1, c.provider(), MONDAY))
    c.execute("INSERT INTO provider_blocks (provider_id, start_at, end_at, reason, created_at, created_by) VALUES (%s,'2030-01-07 13:00','2030-01-07 14:00','Lunch',now(),'t')", (prov,))
    c.fails(errors.RaiseException, r"unavailable at 2030-01-07 13:00 \(Lunch\)", "SELECT book_appointment(%s,%s,'2030-01-07 13:00','x',NULL,'t')", (p2, prov))
    c.fails(errors.RaiseException, "Provider not found", "SELECT book_appointment(%s,999999,%s,'x',NULL,'t')", (p2, MONDAY))
    c.fails(errors.RaiseException, "Patient not found", "SELECT book_appointment(999999,%s,%s,'x',NULL,'t')", (prov, MONDAY))
    c.fails(errors.RaiseException, "at least one minute", "SELECT book_appointment(%s,%s,'2030-01-07 15:00','x',NULL,'t',0)", (p2, prov))


def test_cancel_appointment(c):
    p, prov = c.patient(), c.provider()
    aid = _book(c, p, prov)
    c.execute("SELECT cancel_appointment(%s,'tester',NULL)", (aid,))
    assert c.rows("SELECT status, cancelled_at IS NOT NULL FROM appointments WHERE id=%s", (aid,)) == [("cancelled", True)]
    assert c.one("SELECT count(*) FROM audit_log WHERE action='appointment_cancelled' AND entity_id=%s", (aid,)) == 1
    c.fails(errors.RaiseException, "Appointment is cancelled, not booked", "SELECT cancel_appointment(%s,'tester',NULL)", (aid,))
    c.fails(errors.RaiseException, "Appointment not found", "SELECT cancel_appointment(999999,'tester',NULL)")
    _book(c, p, prov)                                     # the freed slot can be booked again


def test_reschedule_moves_the_visit_atomically(c):
    p, prov = c.patient(), c.provider()
    old = _book(c, p, prov)
    new = c.one("SELECT reschedule_appointment(%s,%s,'2030-01-07 10:00','move',NULL,'tester')", (old, prov))
    assert c.one("SELECT status FROM appointments WHERE id=%s", (old,)) == "cancelled"
    assert c.rows("SELECT status, patient_id FROM appointments WHERE id=%s", (new,)) == [("booked", p)]


def test_reschedule_into_an_overlapping_slot_of_the_same_visit_works(c):
    p, prov = c.patient(), c.provider()
    old = _book(c, p, prov)
    new = c.one("SELECT reschedule_appointment(%s,%s,'2030-01-07 09:15','shift',NULL,'t')", (old, prov))
    assert new != old


def test_failed_reschedule_leaves_the_original_booked(c):
    p1, p2, prov = c.patient(), c.patient(), c.provider()
    mine = _book(c, p1, prov)
    _book(c, p2, prov, start="2030-01-07 10:00")
    c.execute("SAVEPOINT s")
    with pytest.raises(errors.RaiseException, match="already booked"):
        c.execute("SELECT reschedule_appointment(%s,%s,'2030-01-07 10:00','x',NULL,'t')", (mine, prov))
    c.execute("ROLLBACK TO SAVEPOINT s")
    assert c.one("SELECT status FROM appointments WHERE id=%s", (mine,)) == "booked"
    assert c.one("SELECT count(*) FROM audit_log WHERE action='appointment_cancelled' AND entity_id=%s", (mine,)) == 0


# ----------------------------------------------------------------------- request workflow procedures

def test_assign_request(c):
    r = c.request("needs_review")
    assert c.one("SELECT assign_request(%s,NULL,'alice@example.com')", (r,)) == "in_review"
    assert c.rows("SELECT assigned_to, status FROM portal_requests WHERE id=%s", (r,)) == [("alice@example.com", "in_review")]
    r2 = c.request("auto_responded")
    assert c.one("SELECT assign_request(%s,'bob@example.com','alice@example.com')", (r2,)) == "auto_responded"   # status unchanged
    assert c.one("SELECT details->>'assignee' FROM audit_log WHERE action='assigned' AND entity_id=%s", (r2,)) == "bob@example.com"
    c.fails(errors.RaiseException, "Request not found", "SELECT assign_request(999999,'x','y')")


def test_resolve_close_and_reopen_request(c):
    r = c.request("in_review")
    c.fails(errors.RaiseException, "resolution note is required", "SELECT resolve_request(%s,'  ',false,'a')", (r,))
    assert c.one("SELECT resolve_request(%s,'Called the patient',false,'alice@example.com')", (r,)) == "resolved"
    row = c.rows("SELECT status, resolution_note, resolved_by, resolved_at IS NOT NULL FROM portal_requests WHERE id=%s", (r,))[0]
    assert row == ("resolved", "Called the patient", "alice@example.com", True)
    assert c.one("SELECT reopen_request(%s,'alice@example.com')", (r,)) == "needs_review"
    assert c.one("SELECT resolved_at IS NULL FROM portal_requests WHERE id=%s", (r,))
    assert c.one("SELECT resolve_request(%s,'Spam',true,'alice@example.com')", (r,)) == "closed"
    assert {x[0] for x in c.rows("SELECT action FROM audit_log WHERE entity_type='request' AND entity_id=%s", (r,))} == {"request_resolved", "request_reopened", "request_closed"}
    c.fails(errors.RaiseException, "Request not found", "SELECT reopen_request(999999,'a')")


# ----------------------------------------------------------------------- dashboard and reports

def test_dashboard_summary_counts(c):
    c.execute("DELETE FROM portal_requests")
    def req(status, decision, intent, priority="normal", reasons="[]"):
        c.execute("INSERT INTO portal_requests (reference, status, decision, intent, priority, extracted, match_details, review_reasons, created_at, updated_at) "
                  "VALUES (%s,%s,%s,%s,%s,'{}','{}',%s,now() - interval '60 minutes',now())",
                  (f"D-{c.n}", status, decision, intent, priority, reasons))
        c.n += 1
    req("resolved", "auto_respond", "new_appointment")
    req("needs_review", "manual_review", "new_appointment", priority="high", reasons=json.dumps([{"code": "OUT_OF_NETWORK"}, {"code": "LOW_CONFIDENCE"}]))
    req("in_review", "manual_review", None, reasons=json.dumps([{"code": "OUT_OF_NETWORK"}]))
    c.execute("UPDATE portal_requests SET resolved_at = created_at + interval '45 minutes', first_response_at = created_at + interval '15 minutes' WHERE status = 'resolved'")
    s = c.one("SELECT dashboard_summary(30, %s)", (date.today(),))
    assert s["total_requests"] == 3 and s["open_requests"] == 2 and s["needs_review"] == 2
    assert s["auto_responded"] == 1 and s["auto_response_rate"] == 0.333
    assert s["avg_minutes_to_resolve"] == 45.0 and s["avg_minutes_to_first_response"] == 15.0
    assert s["by_status"] == {"resolved": 1, "needs_review": 1, "in_review": 1}
    assert s["by_intent"] == {"new_appointment": 2, "unknown": 1}
    assert s["review_reasons"] == {"OUT_OF_NETWORK": 2, "LOW_CONFIDENCE": 1}  # jsonb keeps no order: the API sorts by count
    assert s["high_priority_open"] == 1 and s["window_days"] == 30
    assert sum(d["received"] for d in s["daily"]) == 3 and s["daily"][0]["auto"] + s["daily"][0]["review"] == 3


def test_dashboard_summary_on_an_empty_window(c):
    c.execute("DELETE FROM portal_requests")
    s = c.one("SELECT dashboard_summary(7, %s)", (date.today(),))
    assert s["total_requests"] == 0 and s["auto_response_rate"] == 0 and s["avg_minutes_to_resolve"] is None
    assert s["by_status"] == {} and s["daily"] == [] and s["review_reasons"] == {}


def test_dashboard_counts_todays_appointments_and_patients(c):
    p, prov = c.patient(), c.provider()
    today = date.today()
    start = f"{today.isoformat()} 09:00"
    c.execute("INSERT INTO appointments (patient_id, provider_id, start_at, end_at, status, booked_by, created_at) VALUES (%s,%s,%s,%s,'booked','t',now())",
              (p, prov, start, f"{today.isoformat()} 09:30"))
    s = c.one("SELECT dashboard_summary(30, %s)", (today,))
    assert s["appointments_today"] == 1 and s["patients"] >= 1
    assert c.one("SELECT dashboard_summary(30, %s)", (today + timedelta(days=1),))["appointments_today"] == 0


def test_turnaround_report(c):
    c.execute("DELETE FROM portal_requests")
    for i, (status, decision) in enumerate([("resolved", "auto_respond"), ("needs_review", "manual_review"), ("needs_review", "manual_review")]):
        c.execute("INSERT INTO portal_requests (reference, status, decision, intent, priority, extracted, match_details, review_reasons, created_at, updated_at) "
                  "VALUES (%s,%s,%s,'cancel','normal','{}','{}','[]',now(),now())", (f"T-{i}-{id(c)}", status, decision))
    rows = c.rows("SELECT intent, total, auto_responded, needs_review, resolved FROM request_turnaround_report(30)")
    assert rows == [("cancel", 3, 1, 2, 1)]


# ----------------------------------------------------------------------- insurance policy rules (revision 0005)

def _payer(c):
    c.n += 1
    return c.one("INSERT INTO payers (name, payer_code, aliases) VALUES (%s, %s, '[]') RETURNING id", (f"Payer {c.n}-{id(c)}", f"P{c.n}{id(c)}"[:20]))


def _policy(c, patient, payer, order="primary", active=True, primary=None):
    return c.one("INSERT INTO insurance_policies (patient_id, payer_id, member_id, coverage_order, is_active, is_primary, "
                 "relationship_to_subscriber, created_at) VALUES (%s,%s,'M1',%s,%s,%s,'self',now()) RETURNING id",
                 (patient, payer, order, active, order == "primary" if primary is None else primary))


def test_only_one_active_policy_per_patient_and_order(c):
    p, payer = c.patient(), _payer(c)
    _policy(c, p, payer, "primary")
    c.fails(errors.UniqueViolation, "uq_policy_active_order", "INSERT INTO insurance_policies (patient_id, payer_id, member_id, coverage_order, is_active, is_primary, relationship_to_subscriber, created_at) VALUES (%s,%s,'M2','primary',true,true,'self',now())", (p, payer))
    _policy(c, p, payer, "secondary")                     # a different order is fine
    _policy(c, c.patient(), payer, "primary")             # another patient is fine


def test_an_archived_policy_does_not_block_its_replacement(c):
    p, payer = c.patient(), _payer(c)
    old = _policy(c, p, payer, "primary")
    c.actor("alice@example.com")
    c.execute("UPDATE insurance_policies SET is_active=false WHERE id=%s", (old,))
    _policy(c, p, payer, "primary")
    assert c.rows("SELECT is_primary, archived_at IS NOT NULL, archived_by FROM insurance_policies WHERE id=%s", (old,)) == [(False, True, "alice@example.com")]


def test_policy_trigger_keeps_primary_flag_in_step_with_the_order(c):
    p, payer = c.patient(), _payer(c)
    pol = _policy(c, p, payer, "secondary", primary=True)       # contradictory input
    assert c.one("SELECT is_primary FROM insurance_policies WHERE id=%s", (pol,)) is False
    c.execute("UPDATE insurance_policies SET coverage_order='primary' WHERE id=%s", (pol,))
    assert c.one("SELECT is_primary FROM insurance_policies WHERE id=%s", (pol,)) is True
