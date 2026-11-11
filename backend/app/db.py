"""Database engine and session management (PostgreSQL primary, SQLite fallback)."""
import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

log = logging.getLogger("docshealth.db")


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # enforce FKs on SQLite like Postgres does
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


engine = _make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    """Create missing tables, then add any model columns missing from existing tables.

    A small forward-only upgrade so an existing database picks up new nullable
    columns without losing data. Destructive changes still need Alembic.
    """
    from app import models  # noqa: F401  (register models)

    url = get_settings().migration_database_url
    ddl_engine = _make_engine(url) if url else engine
    try:
        _create_all(ddl_engine)
    finally:
        if ddl_engine is not engine:
            ddl_engine.dispose()


def _create_all(ddl_engine) -> None:
    Base.metadata.create_all(ddl_engine)
    inspector = inspect(ddl_engine)
    with ddl_engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable and column.server_default is None:
                    raise RuntimeError(f"Cannot auto-add NOT NULL column {table.name}.{column.name}; write a migration")
                ddl_type = column.type.compile(dialect=ddl_engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}'))
                log.info("added column %s.%s", table.name, column.name)
