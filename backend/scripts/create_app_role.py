"""Create (or refresh) the least-privilege PostgreSQL login the API runs as.

    python -m scripts.create_app_role [--role docshealth_app]

Uses the admin connection (MIGRATION_DATABASE_URL, falling back to DATABASE_URL when that
is still the admin account). Then, idempotently:
  * creates/updates role <role>: LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, a new random
    password, connection limit 30;
  * grants CONNECT on the database and USAGE on schema public (no CREATE);
  * grants SELECT/INSERT/UPDATE/DELETE on every Docs Health table, except audit_log which is
    SELECT/INSERT only (the app can never alter its own audit trail);
  * grants USAGE/SELECT on the id sequences, plus default privileges for tables the admin
    creates later;
  * verifies by logging in as the new role (read, write probe rolled back, and denied DDL);
  * writes DATABASE_URL (app role) and MIGRATION_DATABASE_URL (admin) to backend/.env,
    keeping a backup at .env.bak. Passwords are never printed.
"""
from __future__ import annotations

import argparse
import re
import secrets
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app import models  # noqa: F401
from app.config import get_settings
from app.db import Base

ENV = Path(__file__).resolve().parents[1] / ".env"
APPEND_ONLY = {"audit_log"}


def set_env(values: dict[str, str]) -> None:
    content = ENV.read_text(encoding="utf-8-sig")
    shutil.copyfile(ENV, ENV.with_suffix(".bak"))
    for key, value in values.items():
        line = f"{key}={value}"
        if re.search(rf"(?m)^\s*{key}\s*=.*$", content):
            content = re.sub(rf"(?m)^\s*{key}\s*=.*$", lambda _: line, content, count=1)
        else:
            content = content.replace("DATABASE_URL=", f"{line}\nDATABASE_URL=", 1) if key != "DATABASE_URL" else content
    ENV.write_text(content, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="docshealth_app")
    args = ap.parse_args()
    role = args.role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{2,40}", role):
        print("Role name must be lower-case letters, digits or underscores.")
        return 2

    s = get_settings()
    admin_url = s.migration_database_url or s.database_url
    admin = make_url(admin_url)
    if not admin.drivername.startswith("postgresql"):
        print("Admin connection is not PostgreSQL; set MIGRATION_DATABASE_URL first.")
        return 2
    if admin.username == role:
        print("The admin URL already uses the app role; set MIGRATION_DATABASE_URL to the admin account.")
        return 2

    password = secrets.token_urlsafe(32)
    tables = [t.name for t in Base.metadata.sorted_tables]
    engine = create_engine(admin_url)
    try:
        with engine.begin() as c:
            if not c.execute(text("select rolsuper or rolcreaterole from pg_roles where rolname=current_user")).scalar():
                print("The admin account cannot create roles.")
                return 3
            missing = [t for t in tables if not c.execute(text("select to_regclass(:t)"), {"t": f"public.{t}"}).scalar()]
            if missing:
                print("Create the tables first (python -m scripts.check_db --create). Missing:", ", ".join(missing))
                return 3
            exists = c.execute(text("select 1 from pg_roles where rolname=:r"), {"r": role}).scalar()
            verb = "ALTER" if exists else "CREATE"
            # DDL can't bind parameters; token_urlsafe output is [A-Za-z0-9_-] only, so quoting is safe.
            assert re.fullmatch(r"[A-Za-z0-9_-]+", password)
            c.execute(text(f"{verb} ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                           f"CONNECTION LIMIT 30 PASSWORD '{password}'"))
            db = admin.database
            c.execute(text(f'GRANT CONNECT, TEMPORARY ON DATABASE "{db}" TO {role}'))
            c.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            c.execute(text(f"REVOKE CREATE ON SCHEMA public FROM {role}"))
            for t in tables:
                c.execute(text(f'REVOKE ALL ON TABLE public."{t}" FROM {role}'))
                privs = "SELECT, INSERT" if t in APPEND_ONLY else "SELECT, INSERT, UPDATE, DELETE"
                c.execute(text(f'GRANT {privs} ON TABLE public."{t}" TO {role}'))
                seq = c.execute(text("select pg_get_serial_sequence(:t, 'id')"), {"t": f"public.{t}"}).scalar()
                if seq:
                    c.execute(text(f"GRANT USAGE, SELECT ON SEQUENCE {seq} TO {role}"))
            c.execute(text(f"ALTER DEFAULT PRIVILEGES FOR ROLE {admin.username} IN SCHEMA public "
                           f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"))
            c.execute(text(f"ALTER DEFAULT PRIVILEGES FOR ROLE {admin.username} IN SCHEMA public "
                           f"GRANT USAGE, SELECT ON SEQUENCES TO {role}"))
        print(f"Role {role}: {'updated' if exists else 'created'} with least-privilege grants on {len(tables)} tables")
    except SQLAlchemyError as exc:
        print("Failed, nothing committed:", str(getattr(exc, "orig", exc)).splitlines()[0])
        return 1
    finally:
        engine.dispose()

    app_url = admin.set(username=role, password=password, drivername="postgresql+psycopg")
    app_url_str = app_url.render_as_string(hide_password=False)
    app_engine = create_engine(app_url_str)
    checks = []
    try:
        with app_engine.connect() as c:
            checks.append(("login", c.execute(text("select current_user")).scalar() == role))
            checks.append(("read patients", c.execute(text("select count(*) from patients")).scalar() is not None))
            c.rollback()
        with app_engine.connect() as c:
            with c.begin() as tx:
                c.execute(text("insert into audit_log (entity_type, action, actor, details, created_at) "
                               "values ('system','role_probe','create_app_role','{}', now())"))
                tx.rollback()
            checks.append(("write (rolled back)", True))
        for label, sql in [("blocked: CREATE TABLE", "create table should_fail (id int)"),
                           ("blocked: UPDATE audit_log", "update audit_log set actor = actor where id = -1"),
                           ("blocked: DROP TABLE", "drop table patients")]:
            with app_engine.connect() as c:
                try:
                    c.execute(text(sql))
                    checks.append((label, False))
                except SQLAlchemyError:
                    checks.append((label, True))
                finally:
                    c.rollback()
    except SQLAlchemyError as exc:
        print("Verification as the new role failed:", str(getattr(exc, "orig", exc)).splitlines()[0])
        return 1
    finally:
        app_engine.dispose()
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not all(ok for _, ok in checks):
        return 1

    admin_str = admin.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    set_env({"MIGRATION_DATABASE_URL": admin_str, "DATABASE_URL": app_url_str})
    print(f"Saved DATABASE_URL (as {role}) and MIGRATION_DATABASE_URL (as {admin.username}) to backend/.env "
          f"(backup: .env.bak). Restart the API.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
