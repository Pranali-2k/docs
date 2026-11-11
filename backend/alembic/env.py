"""Alembic environment. Credentials come from the application settings (environment / .env), never from alembic.ini.

MIGRATION_DATABASE_URL (an account allowed to create tables, triggers and extensions) is preferred over DATABASE_URL
(the least-privilege runtime account).
"""
from alembic import context
from sqlalchemy import create_engine, pool

from app import models, models_chart  # noqa: F401  (register every table on Base.metadata)
from app.config import get_settings
from app.db import Base

config = context.config
target_metadata = Base.metadata


# Expression indexes are created by migrations; Alembic cannot compare them reliably, so the drift check skips them.
EXPRESSION_INDEXES = {"uq_policy_active_order"}


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "index" and name in EXPRESSION_INDEXES)


def _url() -> str:
    s = get_settings()
    url = s.migration_database_url or s.database_url
    if not url.startswith("postgresql"):
        raise SystemExit("Migrations need PostgreSQL. Set DATABASE_URL (or MIGRATION_DATABASE_URL) to a postgresql+psycopg:// URL.")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True,
                      include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
