"""Core database logic: stamping and history triggers, integrity rules, scheduling and request-workflow procedures,
dashboard and report functions (see alembic/sql/core_logic.sql).

Revision ID: 0004
Revises: 0003
"""
from pathlib import Path

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "core_logic.sql"


def upgrade() -> None:
    # Driver-level execute with no parameters, so psycopg leaves the % in format() calls alone.
    op.get_bind().connection.driver_connection.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    for t in ("patients", "insurance_policies", "guarantors", "phi_releases", "appointments", "portal_requests",
              "providers", "provider_blocks"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_core_stamp ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_core_history ON {t}")
    op.execute("DROP TRIGGER IF EXISTS trg_request_status ON portal_requests")
    op.execute("ALTER TABLE portal_requests DROP CONSTRAINT IF EXISTS ck_request_status")
    for c in ("ex_appt_provider_overlap", "ex_appt_patient_overlap", "ck_appt_range", "ck_appt_status"):
        op.execute(f"ALTER TABLE appointments DROP CONSTRAINT IF EXISTS {c}")
    for fn in ("book_appointment(integer, integer, timestamp, text, integer, text, integer)",
               "cancel_appointment(integer, text, integer)",
               "reschedule_appointment(integer, integer, timestamp, text, integer, text)",
               "assign_request(integer, text, text)", "resolve_request(integer, text, boolean, text)",
               "reopen_request(integer, text)", "dashboard_summary(integer, date)", "request_turnaround_report(integer)",
               "core_stamp()", "core_history()", "request_status_rules()", "app_actor()"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn} CASCADE")
