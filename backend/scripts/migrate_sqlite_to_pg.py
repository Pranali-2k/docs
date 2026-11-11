"""Copy all Docs Health data from the local SQLite demo database into PostgreSQL.

    python -m scripts.migrate_sqlite_to_pg --source sqlite:///./data/docshealth.db
    (target = MIGRATION_DATABASE_URL, else DATABASE_URL, from backend/.env; must be PostgreSQL)

* Creates any missing tables on the target first.
* Refuses to copy into a table that already has rows unless --replace is given
  (then the Docs Health tables, and only those, are emptied first).
* Copies rows with their original ids. FK checks are deferred during the load
  (session_replication_role = replica, needs superuser) because email/request/appointment
  rows reference each other, then id sequences are moved past the copied ids.
* Naive SQLite timestamps are loaded as UTC (the app always writes UTC).
* Verifies row counts table by table. Everything runs in one transaction: on any
  error nothing is committed.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from app import models  # noqa: F401
from app.config import get_settings
from app.db import Base, create_all, engine as app_engine

# Bulk load needs table ownership (TRUNCATE, replication role): use the admin connection when configured.
target = create_engine(get_settings().migration_database_url) if get_settings().migration_database_url else app_engine


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="SQLite URL of the demo database")
    ap.add_argument("--replace", action="store_true", help="empty Docs Health tables on the target before copying")
    args = ap.parse_args()

    if target.dialect.name != "postgresql":
        print("DATABASE_URL in backend/.env is not PostgreSQL; nothing to do.")
        return 2
    source = create_engine(args.source)
    print(f"Source: {source.url.render_as_string(hide_password=True)}")
    print(f"Target: {target.url.render_as_string(hide_password=True)}\n")

    try:
        create_all()  # creates missing tables / columns on the target
    except SQLAlchemyError as exc:
        print("Could not create tables on target:", str(getattr(exc, "orig", exc)).splitlines()[0])
        return 3

    tables = list(Base.metadata.sorted_tables)
    src_tables = set(inspect(source).get_table_names())
    try:
        with target.begin() as dst:
            occupied = [t.name for t in tables
                        if dst.execute(select(func.count()).select_from(t)).scalar()]
            if occupied and not args.replace:
                print("Target tables already contain data:", ", ".join(occupied))
                print("Re-run with --replace to empty them first (only Docs Health tables are touched).")
                return 4
            dst.execute(text("SET LOCAL session_replication_role = replica"))
            # SQLite keeps UTC timestamps without a zone marker; read them as UTC, not server-local.
            dst.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            if occupied:
                dst.execute(text("TRUNCATE " + ", ".join(f'"{t.name}"' for t in tables) + " RESTART IDENTITY"))
            with source.connect() as src:
                for t in tables:
                    if t.name not in src_tables:
                        print(f"  {t.name:24} skipped (not in source)")
                        continue
                    src_cols = {c["name"] for c in inspect(source).get_columns(t.name)}
                    cols = [c for c in t.columns if c.name in src_cols]
                    rows = [dict(r._mapping) for r in src.execute(select(*[t.c[c.name] for c in cols]))]
                    if rows:
                        dst.execute(t.insert(), rows)
                    if "id" in t.c:
                        dst.execute(text(
                            f"SELECT setval(pg_get_serial_sequence('\"{t.name}\"', 'id'), "
                            f"COALESCE((SELECT MAX(id) FROM \"{t.name}\"), 0) + 1, false)"))
                    copied = dst.execute(select(func.count()).select_from(t)).scalar()
                    status = "OK" if copied == len(rows) else "MISMATCH"
                    print(f"  {t.name:24} {len(rows):5} rows -> {copied:5}  {status}")
                    if status != "OK":
                        raise RuntimeError(f"row count mismatch in {t.name}")
            dst.execute(text("SET LOCAL session_replication_role = origin"))
            # integrity check now that triggers are back on: every FK value must resolve
            for t in tables:
                for fk in t.foreign_key_constraints:
                    col, ref = fk.elements[0].parent, fk.elements[0].column
                    orphans = dst.execute(text(
                        f'SELECT count(*) FROM "{t.name}" c WHERE c."{col.name}" IS NOT NULL AND NOT EXISTS '
                        f'(SELECT 1 FROM "{ref.table.name}" p WHERE p."{ref.name}" = c."{col.name}")')).scalar()
                    if orphans:
                        raise RuntimeError(f"{orphans} orphan reference(s) in {t.name}.{col.name}")
    except (SQLAlchemyError, RuntimeError) as exc:
        print("\nMigration failed, nothing was committed:", str(getattr(exc, "orig", exc)).splitlines()[0])
        return 1
    print("\nMigration committed; all foreign keys resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
