"""Create a separate database with the procedures' tables, the procedures, and sample data.

Run this yourself (it asks for the PostgreSQL superuser password; nothing is stored or logged):

    python -m scripts.procs.bootstrap_procs_db                      # database docs_health_procs
    python -m scripts.procs.bootstrap_procs_db --db my_database
    python -m scripts.procs.bootstrap_procs_db --no-sample-data     # tables and procedures only

Steps, each safe to repeat:
  1. CREATE DATABASE (skipped if it exists)
  2. scripts/procs/schema.sql      - the 27 tables
  3. scripts/procs/procedures.sql  - the 25 insert_* procedures
  4. scripts/procs/seed_sample_data.py - sample rows, through the Python functions
Existing Docs Health and Dr Patel databases are not touched.
"""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path

import psycopg
from psycopg import sql

from scripts.procs import seed_sample_data

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--superuser", default="postgres")
    ap.add_argument("--db", default="docs_health_procs")
    ap.add_argument("--no-sample-data", action="store_true")
    args = ap.parse_args()

    password = getpass.getpass(f"Password for PostgreSQL user '{args.superuser}': ")
    common = dict(host=args.host, port=args.port, user=args.superuser, password=password)

    with psycopg.connect(dbname="postgres", autocommit=True, **common) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (args.db,)).fetchone():
            print(f"database '{args.db}' exists")
        else:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.db)))
            print(f"database '{args.db}' created")

    with psycopg.connect(dbname=args.db, **common) as conn:
        conn.execute((HERE / "schema.sql").read_text(encoding="utf-8"))
        conn.execute((HERE / "procedures.sql").read_text(encoding="utf-8"))
        conn.commit()
        tables = conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'").fetchone()[0]
        procs = conn.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                             "WHERE n.nspname = 'public' AND p.prokind = 'p' AND p.proname LIKE 'insert\\_%'").fetchone()[0]
        print(f"{tables} tables and {procs} procedures are in place")
        if not args.no_sample_data:
            tally = seed_sample_data.seed(conn)
            print(f"sample data: {tally.added} rows added, {tally.skipped} already there")
    print(f"connection URL: postgresql://{args.superuser}:<password>@{args.host}:{args.port}/{args.db}")


if __name__ == "__main__":
    main()
