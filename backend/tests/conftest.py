import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="docshealth-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
# Tests must never touch a real database: no admin/migration connection either.
os.environ["MIGRATION_DATABASE_URL"] = ""
os.environ["EMAIL_OUTBOUND_MODE"] = "outbox"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMAIL_OUTBOUND_MODE"] = "outbox"
os.environ["EMAIL_INBOUND_MODE"] = "none"
os.environ["UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ["MAIL_DROP_DIR"] = str(_tmp / "maildrop")
os.environ["PORTAL_BASE_URL"] = "http://portal.test"
os.environ["PASSWORD_RESET_MAX_PER_WINDOW"] = "3"
os.environ["JWT_SECRET"] = "test-secret-with-at-least-32-bytes-of-entropy"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, create_all, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture()
def db():
    from app import models  # noqa: F401

    Base.metadata.drop_all(engine)
    create_all()
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
