"""REST API for the chart add-ons: header data, clipboard, alerts, allergies, notes, reminders, flags, tasks,
linked accounts, medications and drug warnings, non-visit orders, procedure logs, consents, intramail and
the admin-maintained reference data (interaction rules, consent templates).

Reads need a signed-in account. Writes need the role listed per resource in
app.services.chart_extras.RESOURCES. Anything not permitted is a 403 before any data changes.
"""

import logging
from datetime import date
from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import current_user, require_admin
from app.services import chart_extras as svc

log = logging.getLogger("docshealth.api.chart")
router = APIRouter(prefix="/api", tags=["chart add-ons"])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- request bodies (all optional; the
# service decides what is required on create so PATCH can reuse them)

class AlertBody(_Body):
    text: str | None = Field(default=None, max_length=500)
    severity: Literal["info", "warning", "critical"] | None = None
    is_active: bool | None = None


class AllergyBody(_Body):
    allergen: str | None = Field(default=None, max_length=200)
    reaction: str | None = Field(default=None, max_length=300)
    severity: Literal["mild", "moderate", "severe"] | None = None
    status: Literal["active", "inactive"] | None = None
    noted_on: date | None = None


class NoteBody(_Body):
    kind: Literal["sticky", "general", "clinical"] | None = None
    body: str | None = Field(default=None, max_length=10000)


class ReminderBody(_Body):
    message: str | None = Field(default=None, max_length=500)
    due_on: date | None = None
    status: Literal["open", "done", "cancelled"] | None = None


class FlagBody(_Body):
    flag_type: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=300)
    is_active: bool | None = None


class MedicationBody(_Body):
    drug_name: str | None = Field(default=None, max_length=200)
    strength: str | None = Field(default=None, max_length=60)
    route: str | None = Field(default=None, max_length=40)
    frequency: str | None = Field(default=None, max_length=100)
    quantity: int | None = Field(default=None, ge=0, le=100000)
    refills: int | None = Field(default=None, ge=0, le=99)
    prescriber_id: int | None = None
    status: Literal["active", "stopped", "completed"] | None = None
    start_on: date | None = None
    stop_on: date | None = None
    instructions: str | None = Field(default=None, max_length=2000)


class OrderBody(_Body):
    order_type: Literal["lab", "imaging", "referral", "other"] | None = None
    description: str | None = Field(default=None, max_length=500)
    status: Literal["draft", "ordered", "completed", "cancelled"] | None = None
    ordered_by_provider_id: int | None = None
    ordered_on: date | None = None
    due_on: date | None = None
    completed_on: date | None = None
    result_note: str | None = Field(default=None, max_length=10000)


class ProcedureBody(_Body):
    appointment_id: int | None = None
    provider_id: int | None = None
    cpt_code: str | None = Field(default=None, max_length=5)
    icd10_code: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=300)
    units: int | None = Field(default=None, ge=1, le=999)
    performed_on: date | None = None


class ConsentBody(_Body):
    template_id: int | None = None
    status: Literal["pending", "signed", "revoked"] | None = None
    signed_on: date | None = None
    signed_by_name: str | None = Field(default=None, max_length=200)
    expires_on: date | None = None


BODIES: dict[str, type[_Body]] = {
    "alerts": AlertBody, "allergies": AllergyBody, "notes": NoteBody, "reminders": ReminderBody, "flags": FlagBody,
    "medications": MedicationBody, "orders": OrderBody, "procedures": ProcedureBody, "consents": ConsentBody,
}


class TaskBody(_Body):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    patient_id: int | None = None
    assigned_to_id: int | None = None
    priority: Literal["low", "normal", "high"] | None = None
    due_on: date | None = None
    status: Literal["open", "in_progress", "done", "cancelled"] | None = None


class LinkBody(_Body):
    linked_patient_id: int
    relationship_label: str | None = Field(default=None, max_length=50)


class RuleBody(_Body):
    drug_a: str = Field(min_length=1, max_length=200)
    drug_b: str = Field(min_length=1, max_length=200)
    severity: Literal["minor", "moderate", "major"] | None = None
    message: str = Field(min_length=1, max_length=500)
    source: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class TemplateBody(_Body):
    name: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=50000)
    is_active: bool | None = None


class MailBody(_Body):
    recipient_id: int
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    patient_id: int | None = None


class HeaderBody(_Body):
    pcp_name: str | None = Field(default=None, max_length=200)
    pms_id: str | None = Field(default=None, max_length=60)
    portal_status: Literal["inactive", "invited", "active"] | None = None


# --------------------------------------------------------------------------- error mapping

def _guard(db: Session, fn: Callable):
    """Run a service call: commit on success, roll back and map domain / database errors to HTTP responses."""
    try:
        result = fn()
        db.commit()
        return result
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from None
    except svc.ChartConflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except svc.ChartError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    except PermissionError as exc:
        db.rollback()
        raise HTTPException(403, str(exc)) from None
    except IntegrityError as exc:
        db.rollback()
        log.info("integrity error: %s", exc.orig)
        raise HTTPException(409, "That change conflicts with existing data") from None
    except DBAPIError as exc:  # a PostgreSQL trigger refused the change (check_violation)
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23514":
            msg = str(exc.orig).splitlines()[0].replace("CONTEXT:", "").strip()
            raise HTTPException(409, msg) from None
        raise


def _need(user: User, roles: set[str]) -> None:
    if user.role not in roles:
        raise HTTPException(403, f"Requires role: {' or '.join(sorted(roles))}")


def _page(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return {"limit": limit, "offset": offset}


# --------------------------------------------------------------------------- header and clipboard

@router.get("/patients/{patient_id}/header")
def get_header(patient_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """The chart header: PCP, PMS id, portal status, alerts, allergies, flags."""
    return _guard(db, lambda: svc.header(db, patient_id))


@router.patch("/patients/{patient_id}/header")
def patch_header(patient_id: int, body: HeaderBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    from app.models import Patient
    from app.services import registration

    def go():
        p = db.get(Patient, patient_id)
        if p is None:
            raise LookupError("Patient not found")
        try:
            registration.update_patient(db, p, body.model_dump(exclude_unset=True), actor=user.email)
        except registration.RegistrationError as exc:
            raise svc.ChartError(str(exc)) from None
        return svc.header(db, patient_id)

    return _guard(db, go)


@router.get("/patients/{patient_id}/clipboard")
def get_clipboard(patient_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _guard(db, lambda: svc.clipboard(db, patient_id))


@router.get("/patients/{patient_id}/drug-warnings")
def get_drug_warnings(patient_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _guard(db, lambda: svc.drug_warning_report(db, patient_id))


# --------------------------------------------------------------------------- generic patient-owned resources

def _register(key: str) -> None:
    cfg = svc.RESOURCES[key]
    Body = BODIES[key]
    roles = cfg["roles"]
    base = f"/patients/{{patient_id}}/{key}"

    @router.get(base, name=f"list_{key}")
    def list_(patient_id: int, status: str | None = None, q: str | None = None, sort: str | None = None,
              page: dict = Depends(_page), db: Session = Depends(get_db), _: User = Depends(current_user)):
        return _guard(db, lambda: svc.list_items(db, key, patient_id, status=status, q=q, sort=sort, **page))

    @router.post(base, status_code=201, name=f"create_{key}")
    def create(patient_id: int, body: Body, db: Session = Depends(get_db), user: User = Depends(current_user)):
        _need(user, roles)
        item, warnings = _guard(db, lambda: svc.create_item(db, key, patient_id, body.model_dump(exclude_unset=True),
                                                            actor=user.email))
        return {**item, "warnings": warnings} if key == "medications" else item

    @router.patch(f"{base}/{{item_id}}", name=f"update_{key}")
    def update(patient_id: int, item_id: int, body: Body, db: Session = Depends(get_db),
               user: User = Depends(current_user)):
        _need(user, roles)
        item, warnings = _guard(db, lambda: svc.update_item(db, key, patient_id, item_id,
                                                            body.model_dump(exclude_unset=True), actor=user.email))
        return {**item, "warnings": warnings} if key == "medications" else item

    @router.delete(f"{base}/{{item_id}}", status_code=204, name=f"delete_{key}")
    def delete(patient_id: int, item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
        _need(user, roles)
        _guard(db, lambda: svc.delete_item(db, key, patient_id, item_id, actor=user.email))
        return Response(status_code=204)


for _key in svc.RESOURCES:
    _register(_key)


# --------------------------------------------------------------------------- tasks

@router.get("/tasks")
def list_tasks(mine: bool = False, patient_id: int | None = None, status: str | None = None, overdue: bool = False,
               q: str | None = None, sort: str | None = None, page: dict = Depends(_page),
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _guard(db, lambda: svc.list_tasks(db, user, mine=mine, patient_id=patient_id, status=status,
                                             overdue=overdue, q=q, sort=sort, **page))


@router.post("/tasks", status_code=201)
def create_task(body: TaskBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    data = body.model_dump(exclude_unset=True)
    data.pop("status", None)
    return _guard(db, lambda: svc.create_task(db, data, actor=user.email))


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, body: TaskBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    data = body.model_dump(exclude_unset=True)
    data.pop("patient_id", None)  # a task stays with its patient
    return _guard(db, lambda: svc.update_task(db, task_id, data, actor=user.email, user=user))


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(db, lambda: svc.delete_task(db, task_id, actor=user.email, user=user))
    return Response(status_code=204)


@router.get("/reports/overdue-work")
def overdue_work(as_of: date | None = None, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return _guard(db, lambda: svc.overdue_report(db, as_of))


# --------------------------------------------------------------------------- linked accounts

@router.get("/patients/{patient_id}/links")
def get_links(patient_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _guard(db, lambda: svc.list_links(db, patient_id))


@router.post("/patients/{patient_id}/links", status_code=201)
def add_link(patient_id: int, body: LinkBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    def go():
        svc.link_patients(db, patient_id, body.linked_patient_id, body.relationship_label, actor=user.email)
        return svc.list_links(db, patient_id)

    return _guard(db, go)


@router.delete("/patients/{patient_id}/links/{other_id}", status_code=204)
def remove_link(patient_id: int, other_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(db, lambda: svc.unlink_patients(db, patient_id, other_id, actor=user.email))
    return Response(status_code=204)


# --------------------------------------------------------------------------- reference data (admin) and templates

@router.get("/drug-rules")
def list_rules(page: dict = Depends(_page), db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.models_chart import DrugInteractionRule

    rows = db.scalars(select(DrugInteractionRule).order_by(DrugInteractionRule.drug_a, DrugInteractionRule.drug_b)
                      .limit(page["limit"]).offset(page["offset"]))
    return [svc.row(r) for r in rows]


@router.post("/drug-rules", status_code=201)
def create_rule(body: RuleBody, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _guard(db, lambda: svc.save_rule(db, body.model_dump(exclude_unset=True), actor=user.email))


@router.put("/drug-rules/{rule_id}")
def update_rule(rule_id: int, body: RuleBody, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _guard(db, lambda: svc.save_rule(db, body.model_dump(exclude_unset=True), actor=user.email, rule_id=rule_id))


@router.get("/consent-templates")
def list_templates(active: bool | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.models_chart import ConsentTemplate

    stmt = select(ConsentTemplate).order_by(ConsentTemplate.name)
    if active is not None:
        stmt = stmt.where(ConsentTemplate.is_active.is_(active))
    return [svc.row(t) for t in db.scalars(stmt)]


@router.post("/consent-templates", status_code=201)
def create_template(body: TemplateBody, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _guard(db, lambda: svc.save_template(db, body.model_dump(exclude_unset=True), actor=user.email))


@router.put("/consent-templates/{template_id}")
def update_template(template_id: int, body: TemplateBody, db: Session = Depends(get_db),
                    user: User = Depends(require_admin)):
    return _guard(db, lambda: svc.save_template(db, body.model_dump(exclude_unset=True), actor=user.email,
                                                template_id=template_id))


# --------------------------------------------------------------------------- intramail

@router.get("/intramail/recipients")
def recipients(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(User).where(User.is_active.is_(True), User.id != user.id).order_by(User.full_name))
    return [{"id": u.id, "name": u.full_name, "email": u.email, "role": u.role} for u in rows]


@router.get("/intramail")
def inbox(box: str = "inbox", unread: bool = False, page: dict = Depends(_page), db: Session = Depends(get_db),
          user: User = Depends(current_user)):
    return _guard(db, lambda: svc.list_mail(db, user, box=box, unread=unread, **page))


@router.post("/intramail", status_code=201)
def send(body: MailBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _guard(db, lambda: svc.send_mail(db, user, body.model_dump(exclude_unset=True)))


@router.get("/intramail/{mail_id}")
def read(mail_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _guard(db, lambda: svc.read_mail(db, user, mail_id))


@router.delete("/intramail/{mail_id}", status_code=204)
def remove(mail_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(db, lambda: svc.delete_mail(db, user, mail_id))
    return Response(status_code=204)
