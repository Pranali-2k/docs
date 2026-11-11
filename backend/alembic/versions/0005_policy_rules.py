"""Insurance policy rules: one active policy per patient and coverage order, and a trigger that keeps the primary flag
and the archive fields consistent.

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_policy_active_order", "insurance_policies",
        ["patient_id", sa.text("COALESCE(coverage_order, CASE WHEN is_primary THEN 'primary' ELSE 'secondary' END)")],
        unique=True, postgresql_where=sa.text("is_active"))
    op.execute("""
        CREATE OR REPLACE FUNCTION policy_rules() RETURNS trigger AS $$
        BEGIN
            IF NEW.is_active THEN
                NEW.is_primary := COALESCE(NEW.coverage_order, CASE WHEN NEW.is_primary THEN 'primary' ELSE 'secondary' END) = 'primary';
                NEW.archived_at := NULL;
                NEW.archived_by := NULL;
            ELSE
                NEW.is_primary := false;
                NEW.archived_at := COALESCE(NEW.archived_at, now());
                NEW.archived_by := COALESCE(NEW.archived_by, app_actor());
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS trg_policy_rules ON insurance_policies;
        CREATE TRIGGER trg_policy_rules BEFORE INSERT OR UPDATE ON insurance_policies
            FOR EACH ROW EXECUTE FUNCTION policy_rules();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_policy_rules ON insurance_policies")
    op.execute("DROP FUNCTION IF EXISTS policy_rules() CASCADE")
    op.drop_index("uq_policy_active_order", table_name="insurance_policies")
