"""Calling PostgreSQL procedures and functions from the services.

Each call runs in a savepoint, so a rule violation raised by the database undoes only that call and the surrounding
request can carry on (route to review, return a 409, ...). Violations raised on purpose by the procedures (SQLSTATE
P0001), by check constraints (23514) and by the no-double-booking exclusion constraints (23P01) become ProcedureError
with a message fit to show staff; anything else is a real error and propagates.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


class ProcedureError(ValueError):
    """A business rule enforced by the database refused the operation."""


def _message(exc: DBAPIError) -> str:
    diag = getattr(exc.orig, "diag", None)
    return (getattr(diag, "message_primary", None) or str(exc.orig).splitlines()[0]).strip()


def scalar(db: Session, sql: str, params: dict | None = None):
    """Run `SELECT proc(...)` and return its single value. Pending ORM changes are flushed first."""
    db.flush()
    try:
        with db.begin_nested():
            return db.execute(text(sql), params or {}).scalar()
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state in ("P0001", "23514"):
            raise ProcedureError(_message(exc)) from None
        if state == "23P01":
            raise ProcedureError("That time is no longer available: it was just booked") from None
        raise
