"""Check the database the backend is configured to use (DATABASE_URL in backend/.env).

    python -m scripts.check_db              # connect, show server, compare tables, write/read probe
    python -m scripts.check_db --create     # also create any missing tables/columns + reference data

Safe to run against any database: the write probe runs in a transaction that is rolled
back, and --create only adds what is missing (never drops or alters existing data).
Passwords in DATABASE_URL are never printed.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError

from app import models  # noqa: F401  (register all tables)
from app.config import get_settings
from app.db import Base, SessionLocal, create_all, engine

OK, WARN, FAIL = "PASS", "WARN", "FAIL"


def line(status: str, label: str, detail: str = "") -> None:
    print(f"  [{status}] {label}{(' - ' + detail) if detail else ''}")


def explain(exc: Exception) -> str:
    msg = str(getattr(exc, "orig", exc)).strip().splitlines()[0]
    low = msg.lower()
    if "password authentication failed" in low:
        return "PostgreSQL rejected the username/password in DATABASE_URL"
    if "does not exist" in low and "database" in low:
        return "the database named in DATABASE_URL does not exist on the server"
    if "connection refused" in low or "could not connect" in low or "timeout" in low:
        return "the server is not reachable at the host/port in DATABASE_URL (is PostgreSQL running?)"
    if "no pg_hba.conf entry" in low:
        return "PostgreSQL pg_hba.conf does not allow this connection"
    return msg[:300]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--create", action="store_true", help="create missing tables/columns and reference data")
    args = ap.parse_args()

    url = engine.url
    print(f"\nDatabase: {url.render_as_string(hide_password=True)}")
    print(f"Dialect : {engine.dialect.name}\n")
    if engine.dialect.name == "sqlite":
        print("  NOTE: DATABASE_URL points at a local SQLite file, not PostgreSQL. Tables will NOT appear in\n"
              "        PostgreSQL until DATABASE_URL in backend/.env is a postgresql+psycopg:// URL.\n")

    failures = 0

    # 1. connection -----------------------------------------------------------------------
    print("1. Connection")
    try:
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                version = conn.execute(text("select version()")).scalar()
                db, user, schema = conn.execute(text("select current_database(), current_user, current_schema()")).one()
                line(OK, "connected", f"{version.split(',')[0]} | database={db} user={user} schema={schema}")
            else:
                version = conn.execute(text("select sqlite_version()")).scalar()
                line(OK, "connected", f"SQLite {version}")
    except SQLAlchemyError as exc:
        line(FAIL, "cannot connect", explain(exc))
        print("\nFix DATABASE_URL in backend/.env (format: postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME)")
        return 2

    # 2. schema -----------------------------------------------------------------------------
    if args.create:
        print("\n2. Creating missing tables / columns")
        try:
            create_all()
            from app.seed import seed

            with SessionLocal() as db:
                seed(db, demo=False)  # staff accounts, payers, plans, providers, schedules; no demo patients
            line(OK, "schema created/upgraded and reference data loaded")
        except (ProgrammingError, OperationalError) as exc:
            line(FAIL, "could not create tables", explain(exc) + " (does the user have CREATE privilege?)")
            return 3

    print(f"\n{'3' if args.create else '2'}. Tables")
    insp = inspect(engine)
    present = set(insp.get_table_names())
    expected = [t.name for t in Base.metadata.sorted_tables]
    missing = [t for t in expected if t not in present]
    for name in expected:
        if name not in present:
            line(FAIL, name, "missing")
            failures += 1
            continue
        cols = {c["name"] for c in insp.get_columns(name)}
        want = {c.name for c in Base.metadata.tables[name].columns}
        gap = sorted(want - cols)
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(f'select count(*) from "{name}"')).scalar()
        except SQLAlchemyError as exc:
            rows = f"? ({explain(exc)})"
        if gap:
            line(WARN, name, f"{rows} rows, missing columns: {', '.join(gap)}")
        else:
            line(OK, name, f"{rows} rows")
    extra = sorted(present - set(expected))
    if extra:
        print(f"  (other tables in this database, not used by Docs Health: {', '.join(extra)})")
    if missing:
        print(f"\n  {len(missing)} table(s) missing. Run:  python -m scripts.check_db --create")

    # 3. write / read probe ----------------------------------------------------------------
    print(f"\n{'4' if args.create else '3'}. Write / read probe (rolled back, leaves no data)")
    if "audit_log" not in present and not args.create:
        line(WARN, "skipped", "audit_log table does not exist yet")
    else:
        db = SessionLocal()
        try:
            probe = models.AuditLog(entity_type="system", entity_id=None, action="db_check_probe", actor="check_db",
                                    details={"at": datetime.now(timezone.utc).isoformat()})
            db.add(probe)
            db.flush()
            back = db.scalar(select(models.AuditLog).where(models.AuditLog.id == probe.id))
            assert back is not None and back.details["at"] == probe.details["at"]
            line(OK, "insert + select + JSON round-trip", f"row id {probe.id}")
        except (SQLAlchemyError, AssertionError) as exc:
            line(FAIL, "write probe failed", explain(exc))
            failures += 1
        finally:
            db.rollback()
            db.close()

    print("\nResult:", "ALL CHECKS PASSED" if not failures else f"{failures} problem(s) found")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
