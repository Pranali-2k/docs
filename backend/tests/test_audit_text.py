"""Audit trail entries read as sentences, with names and dates instead of ids."""
import re
from pathlib import Path

from app.models import AuditLog
from app.services import audit_text


def inject(client, key):
    r = client.post(f"/api/demo/samples/{key}")
    assert r.status_code == 201, r.text
    return r.json()["request"]


def test_cancel_and_book_read_as_sentences(client):
    req = inject(client, "auto_book")
    appt = req["appointment"]
    cancelled = client.post(f"/api/appointments/{appt['id']}/cancel").json()
    chart = client.get(f"/api/patients/{cancelled['patient_id']}/history").json() if "patient_id" in cancelled else None
    audit = client.get("/api/audit", params={"entity_type": "patient"}).json()
    by_change = {}
    for e in audit:
        by_change.setdefault((e["details"] or {}).get("change"), e)
    cancel = by_change["appointment_cancelled"]
    assert cancel["description"].startswith("Appointment cancelled on the chart: ")
    assert appt["provider_name"] in cancel["description"] and "at" in cancel["description"]
    assert "appointment_id" not in cancel["description"] and "=" not in cancel["description"]
    assert " · MRN " in cancel["record_label"]  # "Name · MRN", not "patient #26"
    booked = by_change["appointment_booked"]
    assert booked["description"].startswith("Appointment booked and added to the chart: ")
    assert chart is None or isinstance(chart, list)


def test_request_timeline_descriptions(client):
    req = inject(client, "red_flag")
    tl = client.get(f"/api/requests/{req['id']}").json()["timeline"]
    text = {e["action"]: e["description"] for e in tl}
    assert text["request_created"].startswith("Received an email from ")
    assert text["ai_extracted"].startswith("The AI read the email") and "% confident" in text["ai_extracted"]
    assert text["routed_to_review"].startswith("Sent to manual review: ") and "Clinical red flag" in text["routed_to_review"]
    assert all(e["description"].endswith((".", ")", "!", "?")) for e in tl)
    assert all("=" not in e["description"] for e in tl)


def test_unknown_actions_and_odd_details_never_break(db):
    entries = [
        AuditLog(entity_type="request", entity_id=999999, action="brand_new_action", actor="x", details={"a_key": 1, "b": None}),
        AuditLog(entity_type="patient", entity_id=None, action="chart_updated", actor="x", details={"change": None}),
        AuditLog(entity_type="request", entity_id=1, action="ai_extracted", actor="x", details={"confidence": "oops"}),
        AuditLog(entity_type="system", entity_id=None, action="demographics_updated", actor="x", details={"changes": "not a dict"}),
        AuditLog(entity_type="request", entity_id=1, action="routed_to_review", actor="x", details=None),
    ]
    rows = audit_text.annotate(db, entries)
    assert rows[0]["description"] == "Brand new action (a key: 1)."
    assert all(r["description"] for r in rows)


def test_every_recorded_action_has_a_describer():
    """Any action added to the code later must get a description (or be listed here as intentionally generic)."""
    src = "\n".join(p.read_text(encoding="utf-8") for p in Path("app").rglob("*.py") if p.name != "audit_text.py")
    found = set(re.findall(r'audit\.record\(\s*db,\s*"[a-z_]+",\s*[^,]+,\s*"([a-z_]+)"', src))
    found |= set(re.findall(r'"(login_locked|login_failed|email_test_sent)"', src))
    generic_ok = set()
    missing = sorted(a for a in found if a not in audit_text.DESCRIBE and a not in generic_ok)
    assert not missing, f"add a description in app/services/audit_text.py for: {missing}"
