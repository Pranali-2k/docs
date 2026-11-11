"""Database engine and session management (PostgreSQL only).

Schema changes are Alembic revisions (backend/alembic). The API never creates or alters tables: it checks that the
database is at the latest revision when it starts (ensure_schema_current).

Every statement runs in a transaction that carries the signed-in person's email as `app.actor`. PostgreSQL triggers read
it (current_setting('app.actor', true)) to fill created_by / updated_by and the change history.
"""
import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

log = logging.getLogger("docshealth.db")
BACKEND_DIR = Path(__file__).resolve().parents[1]


class Base(DeclarativeBase):
    pass


def _make_engine(url: str, *, pooled: bool = True):
    if not url.startswith("postgresql"):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL URL (postgresql+psycopg://user:password@host:5432/dbname)")
    s = get_settings()
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    connect_args = {"application_name": "docshealth-api"}  # lets triggers tell API writes from direct ones
    if pooled:
        kwargs.update(pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_recycle=s.db_pool_recycle_seconds,
                      pool_timeout=s.db_pool_timeout_seconds)
        connect_args["options"] = f"-c statement_timeout={int(s.db_statement_timeout_ms)}"
    return create_engine(url, connect_args=connect_args, **kwargs)


engine = _make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def _carry_actor(session, transaction, connection):
    """Each transaction tells PostgreSQL who is acting, for the triggers (transaction-local, never leaks to the pool)."""
    actor = session.info.get("actor")
    if actor:
        connection.exec_driver_sql("SELECT set_config('app.actor', %s, true)", (actor,))


def set_actor(db: Session, actor: str) -> None:
    db.info["actor"] = actor
    db.execute(text("SELECT set_config('app.actor', :a, true)"), {"a": actor})


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------------- migrations (Alembic)

def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def migrate_to_head() -> None:
    """Apply every pending Alembic revision (what `python -m alembic upgrade head` does)."""
    from alembic import command

    command.upgrade(_alembic_config(), "head")


def ensure_schema_current() -> None:
    """Raise a clear error when the database is not at the latest revision; the API never changes the schema itself."""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    with engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if current != head:
        raise RuntimeError(f"Database schema is at revision {current}, the code needs {head}. Run: python -m alembic upgrade head")
