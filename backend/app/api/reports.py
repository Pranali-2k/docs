"""Reports and record history, served by PostgreSQL functions and the record_history table (admin only)."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import RecordHistory, User
from app.security import require_admin

router = APIRouter(prefix="/api", tags=["reports"])

HISTORY_TABLES = {"patients", "insurance_policies", "guarantors", "phi_releases", "appointments", "portal_requests",
                  "providers", "provider_blocks"}


@router.get("/reports/turnaround")
def turnaround(days: int = Query(30, ge=1, le=3650), db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Requests by type: how many, answered automatically, in review, resolved, and average response and resolution
    times in minutes (request_turnaround_report())."""
    rows = db.execute(text("SELECT * FROM request_turnaround_report(:d)"), {"d": days}).mappings().all()
    return [{k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in dict(r).items()} for r in rows]


@router.get("/history/{table}/{record_id}")
def record_history(table: str, record_id: int, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
                   _: User = Depends(require_admin)):
    """Field-level change history of one core record, newest first (who changed which columns, from what to what)."""
    if table not in HISTORY_TABLES:
        raise HTTPException(404, f"No history is kept for '{table}'")
    rows = db.scalars(select(RecordHistory).where(RecordHistory.table_name == table, RecordHistory.record_id == record_id)
                      .order_by(RecordHistory.id.desc()).limit(limit))
    return [{"id": r.id, "operation": r.operation, "changed_by": r.changed_by,
             "changed_at": r.changed_at.isoformat() if r.changed_at else None,
             "changes": r.changes if isinstance(r.changes, dict) else json.loads(r.changes or "{}")} for r in rows]
