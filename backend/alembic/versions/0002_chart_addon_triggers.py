"""Triggers and stored procedures for the chart add-on tables (see alembic/sql/chart_addons.sql).

Revision ID: 0002
Revises: 0001
"""
from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "chart_addons.sql"


def run_sql_file(path: Path) -> None:
    # Driver-level execute with no parameters, so psycopg leaves the % in format() calls alone.
    op.get_bind().connection.driver_connection.execute(path.read_text(encoding="utf-8"))


def upgrade() -> None:
    run_sql_file(SQL)


def downgrade() -> None:
    op.execute("""
        DROP PROCEDURE IF EXISTS link_patients(integer, integer, text, text);
        DROP PROCEDURE IF EXISTS unlink_patients(integer, integer);
        DROP FUNCTION IF EXISTS patient_clipboard(integer);
        DROP FUNCTION IF EXISTS overdue_work_report(date);
    """)
    # trigger functions are dropped with CASCADE: their triggers go with them
    for fn in ("chart_touch_updated_at", "chart_audit_direct", "chart_task_rules", "chart_reminder_rules",
               "chart_order_rules", "chart_medication_rules", "chart_consent_rules", "chart_rule_normalize",
               "chart_link_mirror"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")
