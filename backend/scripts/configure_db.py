"""Point Docs Health at a PostgreSQL database, verify it, and create the tables.

    python -m scripts.configure_db

Asks for host, port, database (e.g. scribe_docs), user and password (typed hidden),
tests the connection, then writes DATABASE_URL into backend/.env (a backup is kept as
.env.bak) and creates any missing Docs Health tables plus reference data. Existing
tables in that database are never dropped or changed. The password is never printed.
"""
from __future__ import annotations

import getpass
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

ENV = Path(__file__).resolve().parents[1] / ".env"


def ask(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def explain(exc: Exception) -> str:
    msg = str(getattr(exc, "orig", exc)).strip().splitlines()[0].lower()
    if "password authentication failed" in msg:
        return "wrong username or password"
    if "does not exist" in msg:
        return "that database does not exist on the server"
    if "connection refused" in msg or "could not connect" in msg or "timeout" in msg:
        return "server not reachable at that host/port"
    return msg[:200]


def write_env(url: str) -> None:
    content = ENV.read_text(encoding="utf-8-sig") if ENV.exists() else ""
    if ENV.exists():
        shutil.copyfile(ENV, ENV.with_suffix(".bak"))
    line = f"DATABASE_URL={url}"
    if re.search(r"(?m)^\s*DATABASE_URL\s*=.*$", content):
        content = re.sub(r"(?m)^\s*DATABASE_URL\s*=.*$", lambda _: line, content, count=1)
    else:
        content = line + "\n" + content
    ENV.write_text(content, encoding="utf-8")


def main() -> int:
    print("Docs Health - PostgreSQL setup (Ctrl+C to cancel)\n")
    host = ask("Host", "localhost")
    port = ask("Port", "5432")
    database = ask("Database", "scribe_docs")
    user = ask("User", "postgres")
    password = getpass.getpass("Password (hidden): ")
    url = f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{quote(database, safe='')}"

    print("\nTesting connection...")
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            version = conn.execute(text("select version()")).scalar().split(",")[0]
            can_create = conn.execute(text(
                "select has_database_privilege(current_user, current_database(), 'CREATE') "
                "or has_schema_privilege(current_user, 'public', 'CREATE')")).scalar()
        engine.dispose()
    except SQLAlchemyError as exc:
        print(f"  FAILED: {explain(exc)}. Nothing was changed.")
        return 2
    print(f"  OK: {version} | database={database} user={user}")
    if not can_create:
        print("  WARNING: this user may not have CREATE privilege on schema public; table creation may fail.")

    write_env(url)
    print(f"  Saved DATABASE_URL to {ENV} (previous file kept as .env.bak)")

    print("\nCreating Docs Health tables (existing tables are left untouched)...")
    import subprocess

    rc = subprocess.call([sys.executable, "-m", "scripts.check_db", "--create"], cwd=ENV.parent)
    if rc == 0:
        print("\nDone. Restart the API so it uses PostgreSQL.")
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled. Nothing was changed.")
        sys.exit(130)
