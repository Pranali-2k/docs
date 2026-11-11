"""Business logic for the chart add-ons (alerts, allergies, notes, reminders, flags, tasks, linked accounts,
medications, interaction warnings, non-visit orders, procedure logs, consents, intramail).

Rules are checked here for clear messages and again by the PostgreSQL triggers in
alembic/sql/chart_addons.sql, which also guard direct database writes.
Domain problems raise ChartError (422) or ChartConflict (409); the API turns them into responses.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.models import Patient, User, utcnow
from app.models_chart import (
    ClinicalOrder,
    ConsentTemplate,
    DrugInteractionRule,
    Intramail,
    Medication,
    PatientAlert,
    PatientAllergy,
    PatientConsent,
    PatientFlag,
    PatientLink,
    PatientNote,
    PatientReminder,
    ProcedureLog,
    Task,
)
from app.services import audit


class ChartError(ValueError):
    """Invalid input (HTTP 422)."""


class ChartConflict(ValueError):
    """Valid input that clashes with the record's current state (HTTP 409)."""


# --------------------------------------------------------------------------- resource registry
# key -> (model, audit label, fields a client may set, fields only settable on create)
RESOURCES: dict[str, dict] = {
    "alerts": dict(model=PatientAlert, label="alert", fields={"text", "severity", "is_active"}, summary="text",
                   roles={"admin", "scheduler"}, deletable=True),
    "allergies": dict(model=PatientAllergy, label="allergy", fields={"allergen", "reaction", "severity", "status", "noted_on"},
                      summary="allergen", roles={"admin"}, deletable=False),
    "notes": dict(model=PatientNote, label="note", fields={"kind", "body"}, summary="body",
                  roles={"admin", "scheduler"}, deletable=True),
    "reminders": dict(model=PatientReminder, label="reminder", fields={"message", "due_on", "status"}, summary="message",
                      roles={"admin", "scheduler"}, deletable=True),
    "flags": dict(model=PatientFlag, label="flag", fields={"flag_type", "note", "is_active"}, summary="flag_type",
                  roles={"admin", "scheduler"}, deletable=True),
    "medications": dict(model=Medication, label="medication",
                        fields={"drug_name", "strength", "route", "frequency", "quantity", "refills", "prescriber_id",
                                "status", "start_on", "stop_on", "instructions"},
                        summary="drug_name", roles={"admin"}, deletable=False),
    "orders": dict(model=ClinicalOrder, label="order",
                   fields={"order_type", "description", "status", "ordered_by_provider_id", "ordered_on", "due_on",
                           "completed_on", "result_note"},
                   summary="description", roles={"admin"}, deletable=False),
    "procedures": dict(model=ProcedureLog, label="procedure",
                       fields={"appointment_id", "provider_id", "cpt_code", "icd10_code", "description", "units",
                               "performed_on"},
                       summary="cpt_code", roles={"admin"}, deletable=False),
    "consents": dict(model=PatientConsent, label="consent",
                     fields={"template_id", "status", "signed_on", "signed_by_name", "expires_on"},
                     summary="template_id", roles={"admin", "scheduler"}, deletable=False),
}

# Allowed status moves: current -> set of next states. States absent as keys are final.
TRANSITIONS = {
    "tasks": {"open": {"in_progress", "done", "cancelled"}, "in_progress": {"open", "done", "cancelled"}},
    "reminders": {"open": {"done", "cancelled"}},
    "orders": {"draft": {"ordered", "cancelled"}, "ordered": {"completed", "cancelled"}},
    "medications": {"active": {"stopped", "completed"}, "stopped": {"active"}},
    "consents": {"pending": {"signed", "revoked"}, "signed": {"revoked"}},
}
CREATE_STATUS = {"tasks": {"open"}, "reminders": {"open"}, "orders": {"draft", "ordered"}, "medications": {"active"},
                 "consents": {"pending"}}

CPT_RE = re.compile(r"^(\d{5}|\d{4}[FTU]|[A-V]\d{4})$")  # CPT (5 digits, category II/III) or HCPCS level II
ICD10_RE = re.compile(r"^[A-TV-Z]\d[0-9A-Z](\.?[0-9A-Z]{1,4})?$")


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def row(obj, drop: set[str] | None = None) -> dict:
    out = {}
    for col in obj.__table__.columns:
        if drop and col.name in drop:
            continue
        v = getattr(obj, col.name)
        out[col.name] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return out


def _patient(db: Session, patient_id: int) -> Patient:
    p = db.get(Patient, patient_id)
    if p is None:
        raise LookupError("Patient not found")
    return p


def _active_user(db: Session, user_id: int | None, what: str) -> User | None:
    if user_id is None:
        return None
    u = db.get(User, user_id)
    if u is None or not u.is_active:
        raise ChartError(f"{what} must be an active staff account")
    return u


def _check_fk(db: Session, model, pk: int | None, what: str) -> None:
    if pk is not None and db.get(model, pk) is None:
        raise ChartError(f"{what} not found")


# --------------------------------------------------------------------------- validation per resource

def _validate(db: Session, key: str, data: dict, *, creating: bool) -> dict:
    from app.models import Appointment, Provider

    d = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
    for k, v in list(d.items()):
        if v == "" and k not in {"is_active"}:
            d[k] = None
    if creating:
        required = {"alerts": ["text"], "allergies": ["allergen"], "notes": ["body"], "reminders": ["message"],
                    "flags": ["flag_type"], "medications": ["drug_name"], "orders": ["order_type", "description"],
                    "procedures": ["cpt_code", "performed_on"], "consents": ["template_id"]}.get(key, [])
        missing = [f for f in required if not d.get(f)]
        if missing:
            raise ChartError("Required: " + ", ".join(missing))
    if key == "procedures":
        if "cpt_code" in d and d["cpt_code"]:
            d["cpt_code"] = d["cpt_code"].upper()
            if not CPT_RE.match(d["cpt_code"]):
                raise ChartError("CPT code must be 5 digits (or a letter plus 4 digits for HCPCS)")
        if d.get("icd10_code"):
            d["icd10_code"] = d["icd10_code"].upper()
            if not ICD10_RE.match(d["icd10_code"]):
                raise ChartError("ICD-10 code is not valid")
        if d.get("units") is not None and d["units"] < 1:
            raise ChartError("Units must be at least 1")
        if d.get("performed_on") and d["performed_on"] > date.today():
            raise ChartError("A procedure cannot be dated in the future")
        _check_fk(db, Appointment, d.get("appointment_id"), "Appointment")
        _check_fk(db, Provider, d.get("provider_id"), "Provider")
    if key == "medications":
        for f in ("quantity", "refills"):
            if d.get(f) is not None and d[f] < 0:
                raise ChartError(f"{f.capitalize()} cannot be negative")
        _check_fk(db, Provider, d.get("prescriber_id"), "Prescriber")
    if key == "orders":
        _check_fk(db, Provider, d.get("ordered_by_provider_id"), "Ordering provider")
    if key == "reminders" and creating and d.get("due_on") and d["due_on"] < date.today():
        raise ChartError("A reminder cannot be due in the past")
    if key == "consents" and d.get("template_id") is not None:
        t = db.get(ConsentTemplate, d["template_id"])
        if t is None or not t.is_active:
            raise ChartError("Consent template not found or inactive")
    return d


def _apply_status_side_effects(key: str, obj, old: str | None, new: str) -> None:
    """Mirror of the database triggers, so SQLite and PostgreSQL behave the same."""
    today = date.today()
    now = utcnow()
    if key in {"tasks", "reminders"}:
        obj.completed_at = now if new == "done" else None
    elif key == "orders":
        if new == "ordered" and not obj.ordered_on:
            obj.ordered_on = today
        if new == "completed" and not obj.completed_on:
            obj.completed_on = today
    elif key == "medications":
        obj.stop_on = None if new == "active" else (obj.stop_on or today)
        if obj.start_on and obj.stop_on and obj.stop_on < obj.start_on:
            raise ChartError("Stop date is before start date")
    elif key == "consents":
        if new == "signed":
            if not (obj.signed_by_name or "").strip():
                raise ChartError("A signed consent needs the name of the signer")
            obj.signed_on = obj.signed_on or today
        if new == "revoked":
            obj.revoked_at = now


def _move(key: str, obj, new: str) -> None:
    old = obj.status
    if new == old:
        return
    if new not in TRANSITIONS.get(key, {}).get(old, set()):
        raise ChartConflict(f"Cannot change status from {old} to {new}")
    obj.status = new
    _apply_status_side_effects(key, obj, old, new)


# --------------------------------------------------------------------------- generic CRUD

def list_items(db: Session, key: str, patient_id: int, *, status: str | None, q: str | None, sort: str | None,
               limit: int, offset: int) -> dict:
    cfg = RESOURCES[key]
    m = cfg["model"]
    _patient(db, patient_id)
    stmt = select(m).where(m.patient_id == patient_id)
    if status and hasattr(m, "status"):
        stmt = stmt.where(m.status == status)
    if q:
        like = f"%{q.strip().lower()}%"
        text_cols = [c for c in m.__table__.columns if str(c.type).startswith(("VARCHAR", "TEXT"))]
        stmt = stmt.where(or_(*[func.lower(c).like(like) for c in text_cols]))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    col_name, desc = (sort or "-id").lstrip("-"), (sort or "-id").startswith("-")
    if col_name not in m.__table__.columns:
        raise ChartError(f"Cannot sort by {col_name}")
    col = m.__table__.columns[col_name]
    stmt = stmt.order_by(col.desc() if desc else col.asc(), m.id.desc()).limit(limit).offset(offset)
    items = [row(x) for x in db.scalars(stmt)]
    if key == "consents" and items:  # show the form's name even after the template was retired
        names = dict(db.execute(select(ConsentTemplate.id, ConsentTemplate.name)).all())
        for it in items:
            it["template_name"] = names.get(it["template_id"])
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def create_item(db: Session, key: str, patient_id: int, data: dict, *, actor: str) -> tuple[dict, list[dict]]:
    cfg = RESOURCES[key]
    m = cfg["model"]
    p = _patient(db, patient_id)
    d = _validate(db, key, data, creating=True)
    status = d.get("status")
    if key in CREATE_STATUS and status and status not in CREATE_STATUS[key]:
        raise ChartError(f"A new {cfg['label']} must start as {' or '.join(sorted(CREATE_STATUS[key]))}")
    obj = m(patient_id=p.id, created_by=actor, **{k: v for k, v in d.items() if k in cfg["fields"]})
    if key == "consents":
        obj.template_version = db.get(ConsentTemplate, obj.template_id).version
        if (obj.status or "pending") == "signed":
            raise ChartError("Create the consent first, then record the signature")
    if key == "orders" and obj.status == "ordered":
        _apply_status_side_effects(key, obj, None, "ordered")
    if key == "medications":
        _apply_status_side_effects(key, obj, None, "active")
    db.add(obj)
    db.flush()
    audit.record(db, "patient", p.id, f"{cfg['label']}_added", actor, summary=str(getattr(obj, cfg["summary"]))[:120],
                 id=obj.id)
    warnings = medication_warnings(db, p.id, only_med=obj.id) if key == "medications" else []
    return row(obj), warnings


def update_item(db: Session, key: str, patient_id: int, item_id: int, data: dict, *, actor: str) -> tuple[dict, list[dict]]:
    cfg = RESOURCES[key]
    obj = db.get(cfg["model"], item_id)
    if obj is None or obj.patient_id != patient_id:
        raise LookupError(f"{cfg['label'].capitalize()} not found")
    d = _validate(db, key, data, creating=False)
    d = {k: v for k, v in d.items() if k in cfg["fields"]}
    if not d:
        raise ChartError("Nothing to update")
    new_status = d.pop("status", None)
    if key == "consents" and obj.status != "pending" and (set(d) - {"expires_on"}):
        raise ChartConflict("A signed or revoked consent can no longer be edited (only its expiry date can)")
    if key in {"orders"} and obj.status in {"completed", "cancelled"} and (set(d) - {"result_note"}):
        raise ChartConflict(f"A {obj.status} order can no longer be edited")
    changes = {}
    for k, v in d.items():
        old = getattr(obj, k)
        if old != v:
            changes[k] = {"from": None if old is None else str(old), "to": None if v is None else str(v)}
            setattr(obj, k, v)
    if new_status and key not in TRANSITIONS:  # a plain toggle (allergy active/inactive), not a workflow
        if new_status != obj.status:
            changes["status"] = {"from": obj.status, "to": new_status}
            obj.status = new_status
    elif new_status and new_status != obj.status:
        before = obj.status
        _move(key, obj, new_status)
        audit.record(db, "patient", patient_id, f"{cfg['label']}_status", actor,
                     summary=str(getattr(obj, cfg["summary"]))[:120], **{"from": before, "to": new_status}, id=obj.id)
    if changes:
        audit.record(db, "patient", patient_id, f"{cfg['label']}_updated", actor,
                     summary=str(getattr(obj, cfg["summary"]))[:120], changes=changes, id=obj.id)
    db.flush()
    warnings = medication_warnings(db, patient_id, only_med=obj.id) if key == "medications" else []
    return row(obj), warnings


def delete_item(db: Session, key: str, patient_id: int, item_id: int, *, actor: str) -> None:
    cfg = RESOURCES[key]
    if not cfg["deletable"]:
        raise ChartConflict(f"A {cfg['label']} cannot be deleted (it is part of the record); change its status instead")
    obj = db.get(cfg["model"], item_id)
    if obj is None or obj.patient_id != patient_id:
        raise LookupError(f"{cfg['label'].capitalize()} not found")
    audit.record(db, "patient", patient_id, f"{cfg['label']}_removed", actor,
                 summary=str(getattr(obj, cfg["summary"]))[:120], id=obj.id)
    db.delete(obj)


# --------------------------------------------------------------------------- tasks (patient-linked or general)

def _task_stmt(user: User, *, mine: bool, patient_id: int | None, status: str | None, overdue: bool, q: str | None):
    stmt = select(Task)
    if mine:
        stmt = stmt.where(Task.assigned_to_id == user.id)
    if patient_id is not None:
        stmt = stmt.where(Task.patient_id == patient_id)
    if status:
        stmt = stmt.where(Task.status == status)
    if overdue:
        stmt = stmt.where(Task.status.in_(("open", "in_progress")), Task.due_on < date.today())
    if q:
        stmt = stmt.where(func.lower(Task.title).like(f"%{q.strip().lower()}%"))
    return stmt


def list_tasks(db: Session, user: User, *, mine: bool, patient_id: int | None, status: str | None, overdue: bool,
               q: str | None, sort: str | None, limit: int, offset: int) -> dict:
    stmt = _task_stmt(user, mine=mine, patient_id=patient_id, status=status, overdue=overdue, q=q)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    name = (sort or "due_on").lstrip("-")
    if name not in Task.__table__.columns:
        raise ChartError(f"Cannot sort by {name}")
    col = Task.__table__.columns[name]
    stmt = stmt.order_by(col.desc() if (sort or "").startswith("-") else col.asc().nulls_last(), Task.id).limit(limit).offset(offset)
    return {"items": [row(t) for t in db.scalars(stmt)], "total": total, "limit": limit, "offset": offset}


def create_task(db: Session, data: dict, *, actor: str) -> dict:
    title = (data.get("title") or "").strip()
    if not title:
        raise ChartError("A task needs a title")
    if data.get("patient_id") is not None:
        _patient(db, data["patient_id"])
    _active_user(db, data.get("assigned_to_id"), "Assignee")
    if data.get("due_on") and data["due_on"] < date.today():
        raise ChartError("A task cannot be due in the past")
    t = Task(title=title, description=data.get("description"), patient_id=data.get("patient_id"),
             assigned_to_id=data.get("assigned_to_id"), priority=data.get("priority") or "normal",
             due_on=data.get("due_on"), created_by=actor)
    db.add(t)
    db.flush()
    audit.record(db, "patient", t.patient_id, "task_added", actor, summary=title[:120], id=t.id)
    return row(t)


def update_task(db: Session, task_id: int, data: dict, *, actor: str, user: User) -> dict:
    t = db.get(Task, task_id)
    if t is None:
        raise LookupError("Task not found")
    if user.role != "admin" and t.assigned_to_id not in (None, user.id) and t.created_by != user.email:
        raise PermissionError("Only the assignee, the creator or an admin can change this task")
    if t.status in {"done", "cancelled"} and (set(data) - {"status"}):
        raise ChartConflict(f"A {t.status} task can no longer be edited")
    if "assigned_to_id" in data:
        _active_user(db, data["assigned_to_id"], "Assignee")
    changes = {}
    for k in ("title", "description", "assigned_to_id", "priority", "due_on"):
        if k in data and getattr(t, k) != data[k]:
            if k == "title" and not (data[k] or "").strip():
                raise ChartError("A task needs a title")
            changes[k] = {"from": str(getattr(t, k)), "to": str(data[k])}
            setattr(t, k, data[k])
    if data.get("status") and data["status"] != t.status:
        before = t.status
        _move("tasks", t, data["status"])
        audit.record(db, "patient", t.patient_id, "task_status", actor, summary=t.title[:120],
                     **{"from": before, "to": t.status}, id=t.id)
    if changes:
        audit.record(db, "patient", t.patient_id, "task_updated", actor, summary=t.title[:120], changes=changes, id=t.id)
    db.flush()
    return row(t)


def delete_task(db: Session, task_id: int, *, actor: str, user: User) -> None:
    t = db.get(Task, task_id)
    if t is None:
        raise LookupError("Task not found")
    if user.role != "admin" and t.created_by != user.email:
        raise PermissionError("Only the creator or an admin can delete this task")
    audit.record(db, "patient", t.patient_id, "task_removed", actor, summary=t.title[:120], id=t.id)
    db.delete(t)


# --------------------------------------------------------------------------- linked accounts

def list_links(db: Session, patient_id: int) -> list[dict]:
    _patient(db, patient_id)
    rows = db.execute(
        select(PatientLink, Patient).join(Patient, Patient.id == PatientLink.linked_patient_id)
        .where(PatientLink.patient_id == patient_id).order_by(Patient.last_name, Patient.first_name)).all()
    return [{"id": l.id, "linked_patient_id": p.id, "name": p.full_name, "mrn": p.mrn,
             "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else None,
             "relationship_label": l.relationship_label} for l, p in rows]


def link_patients(db: Session, patient_id: int, other_id: int, relationship: str | None, *, actor: str) -> None:
    if patient_id == other_id:
        raise ChartError("A chart cannot be linked to itself")
    a, b = _patient(db, patient_id), db.get(Patient, other_id)
    if b is None:
        raise ChartError("The chart to link was not found")
    rel = (relationship or "").strip() or None
    db.execute(text("CALL link_patients(:a, :b, :r, :actor)"), {"a": a.id, "b": b.id, "r": rel, "actor": actor})
    audit.record(db, "patient", a.id, "link_added", actor, summary=f"{b.full_name} ({b.mrn})")
    audit.record(db, "patient", b.id, "link_added", actor, summary=f"{a.full_name} ({a.mrn})")


def unlink_patients(db: Session, patient_id: int, other_id: int, *, actor: str) -> None:
    _patient(db, patient_id)
    exists = db.scalar(select(PatientLink.id).where(PatientLink.patient_id == patient_id,
                                                    PatientLink.linked_patient_id == other_id))
    if exists is None:
        raise LookupError("These charts are not linked")
    db.execute(text("CALL unlink_patients(:a, :b)"), {"a": patient_id, "b": other_id})
    audit.record(db, "patient", patient_id, "link_removed", actor, summary=f"patient #{other_id}")
    audit.record(db, "patient", other_id, "link_removed", actor, summary=f"patient #{patient_id}")


# --------------------------------------------------------------------------- warnings, clipboard, reports

def medication_warnings(db: Session, patient_id: int, only_med: int | None = None) -> list[dict]:
    """'View Current Drug Warnings': allergy matches and interaction rules among the patient's active medications.

    Rules come from drug_interaction_rules, which the practice maintains. With no rules loaded, only allergy
    matches can appear; the result says whether any rules exist so staff are not given false assurance.
    """
    meds = list(db.scalars(select(Medication).where(Medication.patient_id == patient_id, Medication.status == "active")))
    allergies = [a for a in db.scalars(select(PatientAllergy).where(PatientAllergy.patient_id == patient_id,
                                                                    PatientAllergy.status == "active"))]
    rules = list(db.scalars(select(DrugInteractionRule).where(DrugInteractionRule.is_active)))

    def matches(med_name: str, drug: str) -> bool:
        n, d = _norm(med_name), _norm(drug)
        return bool(d) and (n == d or n.startswith(d + " ") or re.search(rf"\b{re.escape(d)}\b", n) is not None)

    found: list[dict] = []
    for m in meds:
        if only_med and m.id != only_med:
            continue
        for a in allergies:
            if matches(m.drug_name, a.allergen):
                found.append({"type": "allergy", "severity": "major", "medication_id": m.id, "drug": m.drug_name,
                              "message": f"Patient has an active allergy to {a.allergen}"
                                         + (f" ({a.reaction})" if a.reaction else "")})
    for i, m1 in enumerate(meds):
        for m2 in meds[i + 1:]:
            if only_med and only_med not in (m1.id, m2.id):
                continue
            for r in rules:
                if (matches(m1.drug_name, r.drug_a) and matches(m2.drug_name, r.drug_b)) or \
                   (matches(m1.drug_name, r.drug_b) and matches(m2.drug_name, r.drug_a)):
                    found.append({"type": "interaction", "severity": r.severity, "medication_id": m1.id,
                                  "drug": f"{m1.drug_name} + {m2.drug_name}", "message": r.message, "source": r.source})
    return found


def drug_warning_report(db: Session, patient_id: int) -> dict:
    _patient(db, patient_id)
    return {"warnings": medication_warnings(db, patient_id),
            "rules_loaded": db.scalar(select(func.count()).select_from(DrugInteractionRule).where(DrugInteractionRule.is_active)) or 0,
            "active_medications": db.scalar(select(func.count()).select_from(Medication)
                                            .where(Medication.patient_id == patient_id, Medication.status == "active")) or 0}


def clipboard(db: Session, patient_id: int) -> dict:
    """Patient Clipboard: the at-a-glance summary. PostgreSQL builds it in the patient_clipboard() function."""
    p = _patient(db, patient_id)
    data = db.execute(text("SELECT patient_clipboard(:p)"), {"p": p.id}).scalar()
    return data if isinstance(data, dict) else json.loads(data)


def header(db: Session, patient_id: int) -> dict:
    """What the chart header line shows: 'Alerts: Unspecified' and 'Allergies: Unspecified' when none are recorded."""
    p = _patient(db, patient_id)
    alerts = [a.text for a in db.scalars(select(PatientAlert).where(PatientAlert.patient_id == p.id, PatientAlert.is_active).order_by(PatientAlert.id))]
    allergies = [a.allergen for a in db.scalars(select(PatientAllergy).where(PatientAllergy.patient_id == p.id, PatientAllergy.status == "active").order_by(PatientAllergy.id))]
    flags = [f.flag_type for f in db.scalars(select(PatientFlag).where(PatientFlag.patient_id == p.id, PatientFlag.is_active))]
    stickies = db.scalar(select(func.count()).select_from(PatientNote).where(PatientNote.patient_id == p.id, PatientNote.kind == "sticky")) or 0
    return {"pcp_name": p.pcp_name, "pms_id": p.pms_id, "portal_status": p.portal_status or "inactive",
            "alerts": alerts or ["Unspecified"], "allergies": allergies or ["Unspecified"], "flags": flags,
            "sticky_notes": stickies}


def overdue_report(db: Session, as_of: date | None = None) -> list[dict]:
    as_of = as_of or date.today()
    rs = db.execute(text("SELECT * FROM overdue_work_report(:d)"), {"d": as_of}).mappings().all()
    return [dict(r) for r in rs]


# --------------------------------------------------------------------------- admin-maintained reference data

def save_rule(db: Session, data: dict, *, actor: str, rule_id: int | None = None) -> dict:
    a, b = _norm(data.get("drug_a")), _norm(data.get("drug_b"))
    if not a or not b or a == b:
        raise ChartError("An interaction rule needs two different drugs")
    a, b = sorted((a, b))
    dup = db.scalar(select(DrugInteractionRule).where(DrugInteractionRule.drug_a == a, DrugInteractionRule.drug_b == b))
    if dup and dup.id != rule_id:
        raise ChartConflict("A rule for this drug pair already exists")
    if rule_id:
        r = db.get(DrugInteractionRule, rule_id)
        if r is None:
            raise LookupError("Rule not found")
    else:
        r = DrugInteractionRule(created_by=actor)
        db.add(r)
    r.drug_a, r.drug_b = a, b
    r.severity = data.get("severity") or r.severity or "moderate"
    r.message = (data.get("message") or "").strip()
    if not r.message:
        raise ChartError("A rule needs a message")
    r.source = data.get("source")
    if data.get("is_active") is not None:
        r.is_active = data["is_active"]
    db.flush()
    audit.record(db, "drug_rule", r.id, "drug_rule_saved", actor, pair=f"{a} + {b}", severity=r.severity)
    return row(r)


def save_template(db: Session, data: dict, *, actor: str, template_id: int | None = None) -> dict:
    name = (data.get("name") or "").strip()
    body = (data.get("body") or "").strip()
    if not name or not body:
        raise ChartError("A consent template needs a name and text")
    dup = db.scalar(select(ConsentTemplate).where(func.lower(ConsentTemplate.name) == name.lower()))
    if dup and dup.id != template_id:
        raise ChartConflict("A template with this name already exists")
    if template_id:
        t = db.get(ConsentTemplate, template_id)
        if t is None:
            raise LookupError("Template not found")
        if body != t.body:
            t.version = (t.version or 1) + 1  # signed consents keep the version they were signed against
        t.name, t.body = name, body
        if data.get("is_active") is not None:
            t.is_active = data["is_active"]
    else:
        t = ConsentTemplate(name=name, body=body, version=1, created_by=actor, is_active=data.get("is_active", True))
        db.add(t)
    db.flush()
    audit.record(db, "consent_template", t.id, "consent_template_saved", actor, name=name, version=t.version)
    return row(t)


# --------------------------------------------------------------------------- intramail

def send_mail(db: Session, sender: User, data: dict) -> dict:
    r = _active_user(db, data.get("recipient_id"), "Recipient")
    if r is None:
        raise ChartError("Choose a recipient")
    if r.id == sender.id:
        raise ChartError("You cannot send a message to yourself")
    subject, body = (data.get("subject") or "").strip(), (data.get("body") or "").strip()
    if not subject or not body:
        raise ChartError("A message needs a subject and a body")
    if data.get("patient_id") is not None:
        _patient(db, data["patient_id"])
    m = Intramail(sender_id=sender.id, recipient_id=r.id, patient_id=data.get("patient_id"), subject=subject[:200], body=body)
    db.add(m)
    db.flush()
    audit.record(db, "intramail", m.id, "intramail_sent", sender.email, to=r.email, patient_id=m.patient_id)
    return mail_row(m)


def mail_row(m: Intramail, *, with_body: bool = True) -> dict:
    d = {"id": m.id, "sender_id": m.sender_id, "recipient_id": m.recipient_id, "patient_id": m.patient_id,
         "subject": m.subject, "sent_at": m.sent_at.isoformat() if m.sent_at else None,
         "read_at": m.read_at.isoformat() if m.read_at else None}
    if with_body:
        d["body"] = m.body
    return d


def list_mail(db: Session, user: User, *, box: str, unread: bool, limit: int, offset: int) -> dict:
    if box not in {"inbox", "sent"}:
        raise ChartError("box must be inbox or sent")
    if box == "inbox":
        stmt = select(Intramail).where(Intramail.recipient_id == user.id, Intramail.deleted_by_recipient.is_(False))
        if unread:
            stmt = stmt.where(Intramail.read_at.is_(None))
    else:
        stmt = select(Intramail).where(Intramail.sender_id == user.id, Intramail.deleted_by_sender.is_(False))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Intramail.sent_at.desc(), Intramail.id.desc()).limit(limit).offset(offset))
    return {"items": [mail_row(m, with_body=False) for m in rows], "total": total, "limit": limit, "offset": offset}


def _own_mail(db: Session, user: User, mail_id: int) -> Intramail:
    m = db.get(Intramail, mail_id)
    if m is None or user.id not in (m.sender_id, m.recipient_id):
        raise LookupError("Message not found")  # not revealing that someone else's message exists
    return m


def read_mail(db: Session, user: User, mail_id: int) -> dict:
    m = _own_mail(db, user, mail_id)
    if m.recipient_id == user.id and m.read_at is None:
        m.read_at = datetime.now(timezone.utc)
    return mail_row(m)


def delete_mail(db: Session, user: User, mail_id: int) -> None:
    m = _own_mail(db, user, mail_id)
    if m.sender_id == user.id:
        m.deleted_by_sender = True
    if m.recipient_id == user.id:
        m.deleted_by_recipient = True
    if m.deleted_by_sender and m.deleted_by_recipient:
        db.delete(m)
