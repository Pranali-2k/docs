"""Test harness: PostgreSQL only.

Set DOCSHEALTH_TEST_DATABASE_URL to a DISPOSABLE database whose name contains "test" or "scratch", for example
    postgresql+psycopg://user:password@127.0.0.1:5432/docshealth_test
The schema is dropped and rebuilt once per run from the real Alembic migrations (so the tests exercise the same
tables, triggers and procedures production gets). Every test then starts from empty tables plus the demo seed.
"""
import os
import re
import tempfile
from pathlib import Path

import pytest

_URL = os.environ.get("DOCSHEALTH_TEST_DATABASE_URL", "")
if not _URL.startswith("postgresql"):
    pytest.exit("Set DOCSHEALTH_TEST_DATABASE_URL to a disposable PostgreSQL database "
                "(postgresql+psycopg://user:password@host:5432/docshealth_test). The tests do not run on SQLite.",
                returncode=2)
if not re.search(r"(test|scratch)", _URL.rsplit("/", 1)[-1].split("?")[0], re.I):
    pytest.exit("DOCSHEALTH_TEST_DATABASE_URL must name a database containing 'test' or 'scratch': the tests drop its tables.",
                returncode=2)

_tmp = Path(tempfile.mkdtemp(prefix="docshealth-test-"))
os.environ["DATABASE_URL"] = _URL
os.environ["MIGRATION_DATABASE_URL"] = ""  # tests never use an admin connection
os.environ["EMAIL_OUTBOUND_MODE"] = "outbox"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMAIL_INBOUND_MODE"] = "none"
os.environ["UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ["MAIL_DROP_DIR"] = str(_tmp / "maildrop")
os.environ["PORTAL_BASE_URL"] = "http://portal.test"
os.environ["PASSWORD_RESET_MAX_PER_WINDOW"] = "3"
os.environ["JWT_SECRET"] = "test-secret-with-at-least-32-bytes-of-entropy"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, engine, migrate_to_head  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402


def pg_url() -> str:
    """The test database as a plain libpq URL (for tests that talk to the driver directly)."""
    return _URL.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    migrate_to_head()
    yield


@pytest.fixture()
def db(_schema):
    with engine.begin() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"))]
        conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    with SessionLocal() as session:
        seed(session, demo=True)
        from app.services.runtime_settings import set_outbound

        set_outbound(session, True, actor="tests")
        from app.services import login_guard

        login_guard.reset_ip_failures()
        session.commit()
        yield session


@pytest.fixture()
def client(db):
    s = get_settings()
    c = TestClient(app)
    token = c.post("/api/auth/login", json={"email": s.seed_scheduler_email, "password": s.seed_scheduler_password})
    c.headers["Authorization"] = f"Bearer {token.json()['access_token']}"
    return c
