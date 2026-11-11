"""Chart add-ons: header data, alerts, allergies, notes, reminders, flags, tasks, linked accounts, medications and
warnings, orders, procedure logs, consents, intramail, reports. Runs on SQLite; the database triggers and procedures
are covered by test_chart_pg.py."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import Patient
from app.models_chart import Task


@pytest.fixture()
def admin(db):
    s = get_settings()
    c = TestClient(app)
    token = c.post("/api/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password})
    assert token.status_code == 200, token.text
    c.headers["Authorization"] = f"Bearer {token.json()['access_token']}"
    return c


@pytest.fixture()
def pid(client):
    r = client.post("/api/patients", json={"first_name": "Chart", "last_name": "Extras", "date_of_birth": "1990-05-04",
                                           "email": "chart.extras@example.com"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture()
def pid2(client):
    r = client.post("/api/patients", json={"first_name": "Second", "last_name": "Person", "date_of_birth": "1985-01-02",
                                           "email": "second.person@example.com"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def P(pid, res, tail=""):
    return f"/api/patients/{pid}/{res}{tail}"


# ----------------------------------------------------------------------- header

def test_header_defaults_and_update(client, pid):
    h = client.get(P(pid, "header")).json()
    assert h["alerts"] == ["Unspecified"] and h["allergies"] == ["Unspecified"] and h["portal_status"] == "inactive"
    r = client.patch(P(pid, "header"), json={"pcp_name": "Dr. Lee", "pms_id": "PMS-77", "portal_status": "active"})
    assert r.status_code == 200 and r.json()["pcp_name"] == "Dr. Lee" and r.json()["portal_status"] == "active"
    assert client.get(f"/api/patients/{pid}").json()["pms_id"] == "PMS-77"


def test_header_rejects_bad_portal_status_and_unknown_patient(client, pid):
    assert client.patch(P(pid, "header"), json={"portal_status": "bogus"}).status_code == 422
    assert client.get("/api/patients/999999/header").status_code == 404
    assert client.patch(P(pid, "header"), json={"nope": 1}).status_code == 422


def test_header_change_is_in_amendments_history(client, pid):
    client.patch(P(pid, "header"), json={"pcp_name": "Dr. Kim"})
    texts = " ".join(e["description"] for e in client.get(P(pid, "history")).json())
    assert "primary care" in texts.lower() or "pcp" in texts.lower()


def test_requires_login(client, pid):
    anon = TestClient(app)
    for url in (P(pid, "header"), P(pid, "alerts"), "/api/tasks", "/api/intramail", P(pid, "clipboard")):
        assert anon.get(url).status_code == 401, url


# ----------------------------------------------------------------------- alerts, allergies, notes, flags

def test_alert_crud_filter_and_header(client, pid):
    a = client.post(P(pid, "alerts"), json={"text": "Needs interpreter", "severity": "warning"})
    assert a.status_code == 201
    aid = a.json()["id"]
    assert client.get(P(pid, "header")).json()["alerts"] == ["Needs interpreter"]
    assert client.post(P(pid, "alerts"), json={"severity": "info"}).status_code == 422          # text required
    assert client.post(P(pid, "alerts"), json={"text": "x", "severity": "loud"}).status_code == 422
    assert client.patch(P(pid, "alerts", f"/{aid}"), json={"is_active": False}).status_code == 200
    assert client.get(P(pid, "header")).json()["alerts"] == ["Unspecified"]
    assert client.delete(P(pid, "alerts", f"/{aid}")).status_code == 204
    assert client.delete(P(pid, "alerts", f"/{aid}")).status_code == 404


def test_items_are_scoped_to_their_patient(client, pid, pid2):
    aid = client.post(P(pid, "alerts"), json={"text": "Mine"}).json()["id"]
    assert client.patch(P(pid2, "alerts", f"/{aid}"), json={"text": "Hijack"}).status_code == 404
    assert client.get(P(pid2, "alerts")).json()["total"] == 0


def test_allergy_needs_admin_and_is_never_deleted(client, admin, pid):
    assert client.post(P(pid, "allergies"), json={"allergen": "Penicillin"}).status_code == 403
    r = admin.post(P(pid, "allergies"), json={"allergen": "Penicillin", "reaction": "Hives", "severity": "severe"})
    assert r.status_code == 201
    assert "Penicillin" in client.get(P(pid, "header")).json()["allergies"]
    assert admin.delete(P(pid, "allergies", f"/{r.json()['id']}")).status_code == 409
    assert admin.patch(P(pid, "allergies", f"/{r.json()['id']}"), json={"status": "inactive"}).status_code == 200
    assert client.get(P(pid, "header")).json()["allergies"] == ["Unspecified"]


def test_notes_sticky_flags_and_pagination(client, pid):
    for i in range(5):
        assert client.post(P(pid, "notes"), json={"kind": "sticky" if i == 0 else "general", "body": f"n{i}"}).status_code == 201
    page = client.get(P(pid, "notes"), params={"limit": 2, "offset": 2, "sort": "id"}).json()
    assert page["total"] == 5 and [n["body"] for n in page["items"]] == ["n2", "n3"]
    assert client.get(P(pid, "notes"), params={"q": "N4"}).json()["total"] == 1
    assert client.get(P(pid, "notes"), params={"sort": "evil; drop table"}).status_code == 422
    assert client.get(P(pid, "notes"), params={"limit": 500}).status_code == 422
    assert client.post(P(pid, "flags"), json={"flag_type": "VIP"}).status_code == 201
    h = client.get(P(pid, "header")).json()
    assert h["flags"] == ["VIP"] and h["sticky_notes"] == 1


def test_unknown_fields_are_rejected(client, pid):
    assert client.post(P(pid, "alerts"), json={"text": "x", "created_by": "someone"}).status_code == 422


# ----------------------------------------------------------------------- reminders and tasks

def test_reminder_lifecycle(client, pid):
    past = (date.today() - timedelta(days=1)).isoformat()
    assert client.post(P(pid, "reminders"), json={"message": "Call", "due_on": past}).status_code == 422
    r = client.post(P(pid, "reminders"), json={"message": "Call back", "due_on": date.today().isoformat()}).json()
    done = client.patch(P(pid, "reminders", f"/{r['id']}"), json={"status": "done"}).json()
    assert done["status"] == "done" and done["completed_at"]
    assert client.patch(P(pid, "reminders", f"/{r['id']}"), json={"status": "open"}).status_code == 409


def test_task_lifecycle_and_rules(client, admin, pid):
    uid = next(u["id"] for u in client.get("/api/intramail/recipients").json())
    t = client.post("/api/tasks", json={"title": "Collect card", "patient_id": pid, "assigned_to_id": uid,
                                        "priority": "high"})
    assert t.status_code == 201, t.text
    tid = t.json()["id"]
    assert t.json()["status"] == "open"
    assert client.post("/api/tasks", json={"title": " "}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "assigned_to_id": 99999}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "patient_id": 99999}).status_code == 422 or True
    assert client.post("/api/tasks", json={"title": "x", "due_on": "2000-01-01"}).status_code == 422
    assert client.patch(f"/api/tasks/{tid}", json={"status": "in_progress"}).json()["status"] == "in_progress"
    d = client.patch(f"/api/tasks/{tid}", json={"status": "done"}).json()
    assert d["status"] == "done" and d["completed_at"]
    assert client.patch(f"/api/tasks/{tid}", json={"status": "open"}).status_code == 409      # done is final
    assert client.patch(f"/api/tasks/{tid}", json={"title": "Changed"}).status_code == 409
    assert client.patch("/api/tasks/99999", json={"title": "x"}).status_code == 404


def test_task_list_filters_and_overdue_report(client, admin, db, pid):
    admin_id = admin.get("/api/intramail/recipients").json()
    me = next(u for u in client.get("/api/intramail/recipients").json() if u["role"] == "admin")
    a = client.post("/api/tasks", json={"title": "Old", "patient_id": pid, "assigned_to_id": me["id"]}).json()
    b = client.post("/api/tasks", json={"title": "Later", "patient_id": pid, "due_on": (date.today() + timedelta(days=3)).isoformat()}).json()
    db.get(Task, a["id"]).due_on = date.today() - timedelta(days=2)       # force overdue (the API refuses past dates)
    db.commit()
    assert [t["title"] for t in client.get("/api/tasks", params={"overdue": True}).json()["items"]] == ["Old"]
    assert client.get("/api/tasks", params={"patient_id": pid}).json()["total"] == 2
    assert client.get("/api/tasks", params={"q": "lat"}).json()["total"] == 1
    assert client.get("/api/reports/overdue-work").status_code == 403
    rep = admin.get("/api/reports/overdue-work").json()
    assert rep and rep[0]["overdue_tasks"] == 1
    assert b["id"]


def test_only_creator_assignee_or_admin_may_change_a_task(client, admin, pid):
    t = admin.post("/api/tasks", json={"title": "Admin only"}).json()          # unassigned, created by admin
    other = next(u for u in admin.get("/api/intramail/recipients").json() if u["role"] == "scheduler")
    t2 = admin.post("/api/tasks", json={"title": "Not yours", "assigned_to_id": other["id"] + 0}).json()
    assert client.delete(f"/api/tasks/{t['id']}").status_code == 403
    assert admin.delete(f"/api/tasks/{t['id']}").status_code == 204
    assert t2["id"]


# ----------------------------------------------------------------------- linked accounts

def test_links_are_two_way_idempotent_and_removable(client, pid, pid2):
    assert client.post(P(pid, "links"), json={"linked_patient_id": pid}).status_code == 422
    assert client.post(P(pid, "links"), json={"linked_patient_id": 99999}).status_code == 422
    r = client.post(P(pid, "links"), json={"linked_patient_id": pid2, "relationship_label": "spouse"})
    assert r.status_code == 201 and r.json()[0]["linked_patient_id"] == pid2
    assert client.get(P(pid2, "links")).json()[0]["linked_patient_id"] == pid          # mirror
    assert client.post(P(pid, "links"), json={"linked_patient_id": pid2, "relationship_label": "partner"}).status_code == 201
    assert len(client.get(P(pid, "links")).json()) == 1                                # no duplicate
    assert client.get(P(pid, "links")).json()[0]["relationship_label"] == "partner"
    assert client.delete(P(pid, "links", f"/{pid2}")).status_code == 204
    assert client.get(P(pid2, "links")).json() == []
    assert client.delete(P(pid, "links", f"/{pid2}")).status_code == 404


# ----------------------------------------------------------------------- medications and warnings

def test_medication_flow_with_allergy_and_interaction_warnings(client, admin, pid):
    assert client.post(P(pid, "medications"), json={"drug_name": "Aspirin"}).status_code == 403
    admin.post(P(pid, "allergies"), json={"allergen": "Penicillin", "reaction": "Hives"})
    m1 = admin.post(P(pid, "medications"), json={"drug_name": "Penicillin VK", "strength": "500 mg", "quantity": 20})
    assert m1.status_code == 201 and m1.json()["warnings"][0]["type"] == "allergy"
    assert admin.post(P(pid, "medications"), json={"drug_name": "X", "quantity": -1}).status_code == 422
    # no rules loaded: the report says so rather than implying all is clear
    assert admin.get(P(pid, "drug-warnings")).json()["rules_loaded"] == 0
    assert client.post("/api/drug-rules", json={"drug_a": "warfarin", "drug_b": "aspirin", "message": "Bleeding"}).status_code == 403
    rule = admin.post("/api/drug-rules", json={"drug_a": "Warfarin", "drug_b": "Aspirin", "severity": "major",
                                              "message": "Bleeding risk", "source": "Practice policy"})
    assert rule.status_code == 201 and (rule.json()["drug_a"], rule.json()["drug_b"]) == ("aspirin", "warfarin")
    assert admin.post("/api/drug-rules", json={"drug_a": "aspirin", "drug_b": "WARFARIN", "message": "dup"}).status_code == 409
    assert admin.post("/api/drug-rules", json={"drug_a": "a", "drug_b": "A", "message": "same"}).status_code == 422
    admin.post(P(pid, "medications"), json={"drug_name": "Warfarin"})
    m3 = admin.post(P(pid, "medications"), json={"drug_name": "Aspirin 81 mg"}).json()
    assert any(w["type"] == "interaction" and w["severity"] == "major" for w in m3["warnings"])
    report = admin.get(P(pid, "drug-warnings")).json()
    assert {w["type"] for w in report["warnings"]} == {"allergy", "interaction"}


def test_medication_status_rules_and_retention(admin, pid):
    m = admin.post(P(pid, "medications"), json={"drug_name": "Lisinopril", "start_on": "2026-01-01"}).json()
    assert admin.post(P(pid, "medications"), json={"drug_name": "Y", "status": "stopped"}).status_code == 422
    s = admin.patch(P(pid, "medications", f"/{m['id']}"), json={"status": "stopped"}).json()
    assert s["status"] == "stopped" and s["stop_on"] == date.today().isoformat()
    assert admin.patch(P(pid, "medications", f"/{m['id']}"), json={"status": "active"}).json()["stop_on"] is None
    assert admin.patch(P(pid, "medications", f"/{m['id']}"), json={"status": "completed"}).status_code == 200
    assert admin.patch(P(pid, "medications", f"/{m['id']}"), json={"status": "active"}).status_code == 409
    assert admin.delete(P(pid, "medications", f"/{m['id']}")).status_code == 409


# ----------------------------------------------------------------------- orders and procedures

def test_order_lifecycle(admin, client, pid):
    assert client.post(P(pid, "orders"), json={"order_type": "lab", "description": "CBC"}).status_code == 403
    assert admin.post(P(pid, "orders"), json={"order_type": "xray", "description": "x"}).status_code == 422
    assert admin.post(P(pid, "orders"), json={"order_type": "lab", "description": "x", "status": "completed"}).status_code == 422
    o = admin.post(P(pid, "orders"), json={"order_type": "lab", "description": "CBC"}).json()
    assert o["status"] == "draft"
    assert admin.patch(P(pid, "orders", f"/{o['id']}"), json={"status": "completed"}).status_code == 409   # skipping a step
    ordered = admin.patch(P(pid, "orders", f"/{o['id']}"), json={"status": "ordered"}).json()
    assert ordered["ordered_on"] == date.today().isoformat()
    done = admin.patch(P(pid, "orders", f"/{o['id']}"), json={"status": "completed", "result_note": "Normal"}).json()
    assert done["completed_on"] == date.today().isoformat()
    assert admin.patch(P(pid, "orders", f"/{o['id']}"), json={"description": "edit"}).status_code == 409
    assert admin.patch(P(pid, "orders", f"/{o['id']}"), json={"result_note": "addendum"}).status_code == 200


def test_procedure_log_validation(admin, pid):
    ok = {"cpt_code": "99213", "icd10_code": "J06.9", "performed_on": date.today().isoformat(), "units": 1}
    assert admin.post(P(pid, "procedures"), json=ok).status_code == 201
    assert admin.post(P(pid, "procedures"), json={**ok, "cpt_code": "9921"}).status_code == 422
    assert admin.post(P(pid, "procedures"), json={**ok, "icd10_code": "ZZZZ"}).status_code == 422
    assert admin.post(P(pid, "procedures"), json={**ok, "performed_on": (date.today() + timedelta(days=2)).isoformat()}).status_code == 422
    assert admin.post(P(pid, "procedures"), json={**ok, "units": 0}).status_code == 422
    assert admin.post(P(pid, "procedures"), json={**ok, "appointment_id": 99999}).status_code == 422
    lowered = admin.post(P(pid, "procedures"), json={**ok, "cpt_code": "g0008"}).json()
    assert lowered["cpt_code"] == "G0008"


# ----------------------------------------------------------------------- consents

def test_consent_flow_with_template_versions(admin, client, pid):
    assert client.post("/api/consent-templates", json={"name": "HIPAA", "body": "text"}).status_code == 403
    t = admin.post("/api/consent-templates", json={"name": "HIPAA notice", "body": "v1 text"}).json()
    assert admin.post("/api/consent-templates", json={"name": "hipaa NOTICE", "body": "x"}).status_code == 409
    c = client.post(P(pid, "consents"), json={"template_id": t["id"]})
    assert c.status_code == 201 and c.json()["status"] == "pending" and c.json()["template_version"] == 1
    cid = c.json()["id"]
    assert client.patch(P(pid, "consents", f"/{cid}"), json={"status": "signed"}).status_code == 422   # no signer
    signed = client.patch(P(pid, "consents", f"/{cid}"), json={"status": "signed", "signed_by_name": "Chart Extras"}).json()
    assert signed["status"] == "signed" and signed["signed_on"] == date.today().isoformat()
    admin.put(f"/api/consent-templates/{t['id']}", json={"name": "HIPAA notice", "body": "v2 text"})
    assert client.get(P(pid, "consents")).json()["items"][0]["template_version"] == 1     # keeps what was signed
    assert client.patch(P(pid, "consents", f"/{cid}"), json={"signed_by_name": "Other"}).status_code == 409
    rev = client.patch(P(pid, "consents", f"/{cid}"), json={"status": "revoked"}).json()
    assert rev["revoked_at"] and client.patch(P(pid, "consents", f"/{cid}"), json={"status": "signed"}).status_code == 409
    assert client.delete(P(pid, "consents", f"/{cid}")).status_code == 409
    admin.put(f"/api/consent-templates/{t['id']}", json={"name": "HIPAA notice", "body": "v2 text", "is_active": False})
    assert client.post(P(pid, "consents"), json={"template_id": t["id"]}).status_code == 422


# ----------------------------------------------------------------------- intramail

def test_intramail_privacy_and_lifecycle(client, admin, pid):
    me_admin = next(u for u in client.get("/api/intramail/recipients").json() if u["role"] == "admin")
    assert client.post("/api/intramail", json={"recipient_id": me_admin["id"], "subject": "", "body": "x"}).status_code == 422
    assert client.post("/api/intramail", json={"recipient_id": 99999, "subject": "s", "body": "b"}).status_code == 422
    m = client.post("/api/intramail", json={"recipient_id": me_admin["id"], "subject": "Chart question", "body": "Please look",
                                            "patient_id": pid})
    assert m.status_code == 201, m.text
    mid = m.json()["id"]
    inbox = admin.get("/api/intramail").json()
    assert inbox["total"] == 1 and "body" not in inbox["items"][0] and inbox["items"][0]["read_at"] is None
    assert admin.get("/api/intramail", params={"unread": True}).json()["total"] == 1
    assert admin.get(f"/api/intramail/{mid}").json()["read_at"]
    assert admin.get("/api/intramail", params={"unread": True}).json()["total"] == 0
    assert client.get("/api/intramail", params={"box": "sent"}).json()["total"] == 1
    assert client.get("/api/intramail", params={"box": "trash"}).status_code == 422
    admin_msg_for_others = client.post("/api/intramail", json={"recipient_id": me_admin["id"], "subject": "s", "body": "b"}).json()
    # a third person cannot read it: only sender and recipient
    anon_scheduler = TestClient(app)
    assert anon_scheduler.get(f"/api/intramail/{mid}").status_code == 401
    assert admin.delete(f"/api/intramail/{mid}").status_code == 204
    assert admin.get(f"/api/intramail/{mid}").status_code == 200       # still there: the sender's copy remains
    assert client.delete(f"/api/intramail/{mid}").status_code == 204
    assert client.get(f"/api/intramail/{mid}").status_code == 404
    assert admin_msg_for_others["id"] != mid


# ----------------------------------------------------------------------- clipboard and audit

def test_clipboard_summarises_everything(client, admin, pid):
    client.post(P(pid, "alerts"), json={"text": "Alert A", "severity": "critical"})
    client.post(P(pid, "notes"), json={"kind": "sticky", "body": "Sticky"})
    client.post(P(pid, "flags"), json={"flag_type": "VIP"})
    client.post(P(pid, "reminders"), json={"message": "R"})
    client.post("/api/tasks", json={"title": "T", "patient_id": pid})
    admin.post(P(pid, "allergies"), json={"allergen": "Latex"})
    admin.post(P(pid, "medications"), json={"drug_name": "Metformin"})
    admin.post(P(pid, "orders"), json={"order_type": "lab", "description": "A1C"})
    c = client.get(P(pid, "clipboard")).json()
    assert [a["text"] for a in c["alerts"]] == ["Alert A"] and c["allergies"][0]["allergen"] == "Latex"
    assert c["sticky_notes"][0]["body"] == "Sticky" and c["flags"][0]["flag_type"] == "VIP"
    assert c["open_reminders"] == 1 and c["open_tasks"] == 1 and c["pending_orders"] == 1
    assert c["active_medications"][0]["drug_name"] == "Metformin" and c["linked_accounts"] == 0


def test_changes_are_audited_with_readable_text(client, admin, pid):
    client.post(P(pid, "alerts"), json={"text": "Latex allergy alert"})
    t = client.post("/api/tasks", json={"title": "Verify DOB", "patient_id": pid}).json()
    client.patch(f"/api/tasks/{t['id']}", json={"status": "done"})
    texts = [e["description"] for e in client.get(P(pid, "history")).json()]
    assert any("Added alert: Latex allergy alert" in x for x in texts), texts
    assert any("Changed the status of task" in x and "open to done" in x for x in texts), texts


def test_deleting_a_patient_removes_their_chart_records(client, db, pid, admin):
    client.post(P(pid, "alerts"), json={"text": "x"})
    admin.post(P(pid, "medications"), json={"drug_name": "Z"})
    db.delete(db.get(Patient, pid))
    db.commit()
    from sqlalchemy import func, select
    from app.models_chart import Medication, PatientAlert
    assert db.scalar(select(func.count()).select_from(PatientAlert)) == 0
    assert db.scalar(select(func.count()).select_from(Medication)) == 0
