"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.api import availability, chart_extras, integrations, patients, reports, requests, scheduling, system
from app.config import get_settings
from app.db import SessionLocal, ensure_schema_current
from app.prodcheck import check as check_production

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("docshealth")


async def _mail_poller(interval: int) -> None:
    """Background Outlook/IMAP polling, enabled with MAIL_POLL_INTERVAL_SECONDS > 0."""
    from app.services import email_gateway, pipeline

    while True:
        await asyncio.sleep(interval)
        try:
            with SessionLocal() as db:
                for req in await asyncio.to_thread(email_gateway.poll_mailbox, db):
                    try:
                        pipeline.process_request(db, req, actor="ai-assistant")
                        db.commit()
                    except Exception:  # one bad email must not stop the rest
                        db.rollback()
                        log.exception("processing request %s failed", req.id)
        except Exception:  # keep polling; errors are logged
            log.exception("mailbox poll failed")


async def _integration_poller() -> None:
    """Poll Microsoft 365 mailboxes configured in Settings → Integrations (interval set there)."""
    from app.services import mailbox_sync

    while True:
        await asyncio.sleep(20)
        try:
            def run():
                with SessionLocal() as db:
                    return mailbox_sync.run_due(db)

            for r in await asyncio.to_thread(run):
                if r.get("created"):
                    log.info("mailbox %s: %d new request(s)", r["mailbox"], len(r["created"]))
        except Exception:  # keep polling; errors are logged and shown on the mailbox
            log.exception("integration mailbox poll failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    check_production(s)  # raises (and the API won't start) if production settings are unsafe
    try:
        ensure_schema_current()  # the schema is managed by Alembic; the API only checks it is up to date
    except SQLAlchemyError:
        log.exception("database schema check failed; API will report the database as unavailable")
    tasks = []
    if s.email_inbound_mode != "none" and s.mail_poll_interval_seconds > 0:
        tasks.append(asyncio.create_task(_mail_poller(s.mail_poll_interval_seconds)))
    if s.environment != "test":
        tasks.append(asyncio.create_task(_integration_poller()))
    yield
    for task in tasks:
        task.cancel()


def _error(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message, **extra})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(IntegrityError)
    async def integrity(request: Request, exc: IntegrityError):
        log.warning("integrity error on %s %s: %s", request.method, request.url.path, exc.orig)
        return _error(409, "That change conflicts with existing data (duplicate or missing reference).")

    @app.exception_handler(OperationalError)
    async def db_unavailable(request: Request, exc: OperationalError):
        log.error("database unavailable on %s %s: %s", request.method, request.url.path, exc.orig)
        return _error(503, "The database is temporarily unavailable. Please try again shortly.")

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        error_id = uuid.uuid4().hex[:12]
        log.exception("unhandled error %s on %s %s", error_id, request.method, request.url.path)
        return _error(500, f"Something went wrong on our side. Reference: {error_id}", error_id=error_id)


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Docs Health Scheduling & Email Automation API", version="1.0.0", lifespan=lifespan,
                  docs_url=None if s.is_production else "/docs", redoc_url=None,
                  openapi_url=None if s.is_production else "/openapi.json")
    app.add_middleware(
        CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=True, allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def security_and_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", request_id):
            request_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")  # PHI must not be cached
        if s.is_production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if response.status_code >= 500:
            log.error("%s %s -> %s in %.0f ms [req %s]", request.method, request.url.path, response.status_code,
                      (time.perf_counter() - started) * 1000, request_id)
        return response
    for r in (system.router, requests.router, patients.router, chart_extras.router, reports.router, scheduling.router,
              availability.router, integrations.router):
        app.include_router(r)
    return app


app = create_app()
