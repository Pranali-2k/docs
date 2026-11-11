"""End-to-end PRD workflow tests through the HTTP API."""
import pytest
from fastapi.testclient import TestClient

from app.demo_samples import SAMPLES
from app.main import app


def inject(client, key):
    r = client.post(f"/api/demo/samples/{key}")
    assert r.status_code == 201, r.text
    return r.json()["request"]


def codes(req):
    return {x["code"] for x in req["review_reasons"]}


def test_requires_auth(db):
    assert TestClient(app).get("/api/requests").status_code == 401


def test_auto_book_happy_path(client):
    req = inject(client, "auto_book")
    assert req["status"] == "resolved" and req["decision"] == "auto_respond"
    assert req["appointment"]["provider_name"] == "Dr. Anita Rao"
    assert req["eligibility_checks"][0]["coverage_status"] == "active"
    assert req["eligibility_checks"][0]["in_network"] is True
    out = [e for e in req["emails"] if e["direction"] == "outbound"]
    assert out and out[0]["send_status"] == "stored" and "confirmed" in out[0]["subject"]
    actions = [t["action"] for t in req["timeline"]]
    for step in ["request_created", "ai_extracted", "patient_matched", "auto_responded", "response_sent"]:
        assert step in actions


def test_reschedule_and_cancel(client):
    r = inject(client, "auto_reschedule")
    assert r["status"] == "resolved", r["review_reasons"]
    chart = client.get(f"/api/patients/{r['patient']['id']}").json()
    old = next(a for a in chart["appointments"] if a["status"] == "cancelled")
    new = next(a for a in chart["appointments"] if a["status"] == "booked")
    assert old["start_at"][:10] != new["start_at"][:10]
    assert int(new["start_at"][11:13]) >= 12  # "Thursday afternoon"
    c = inject(client, "auto_cancel")
    assert c["status"] == "resolved", c["review_reasons"]
    assert c["appointment"]["status"] == "cancelled"


@pytest.mark.parametrize("key,expected", [
    ("new_patient", "UNMATCHED_PATIENT"),
    ("eligibility_failed", "ELIGIBILITY_FAILED"),
    ("out_of_network", "OUT_OF_NETWORK"),
    ("conflicting_insurance", "CONFLICTING_INFO"),
    ("ambiguous_match", "AMBIGUOUS_MATCH"),
    ("red_flag", "CLINICAL_RED_FLAG"),
    ("general_question", "UNSUPPORTED_INTENT"),
    ("incomplete", "INCOMPLETE_DATA"),
])
def test_manual_review_triggers(client, key, expected):
    req = inject(client, key)
    assert req["status"] == "needs_review"
    assert expected in codes(req)
    assert req["appointment"] is None
    assert not [e for e in req["emails"] if e["direction"] == "outbound"], "no auto email on review"
    assert req["draft_response"]


def test_every_sample_is_covered():
    assert len(SAMPLES) == 11


def test_new_patient_resolution_flow(client):
    req = inject(client, "new_patient")
    payers = {p["name"]: p["id"] for p in client.get("/api/payers").json()}
    r = client.post(f"/api/requests/{req['id']}/create-patient", json={"payer_id": payers["Aetna"]})
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["patient"]["full_name"] == "Olivia Bennett"
    assert done["status"] == "resolved", done["review_reasons"]
    assert done["appointment"]["provider_name"] == "Dr. Marcus Patel"
    preferred = done["extracted"]["appointment"]["preferred_dates"]
    assert done["appointment"]["start_at"][:10] in preferred  # "sometime next week" is honoured


def test_staff_manual_booking_and_resolution(client):
    req = inject(client, "eligibility_failed")
    slots = client.get(f"/api/requests/{req['id']}/slots").json()["slots"]
    assert slots
    r = client.post(f"/api/requests/{req['id']}/book",
                    json={"provider_id": slots[0]["provider_id"], "start_at": slots[0]["start_at"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "resolved"
    other = inject(client, "out_of_network")  # the same slot cannot be double-booked
    clash = client.post(f"/api/requests/{other['id']}/book",
                        json={"provider_id": slots[0]["provider_id"], "start_at": slots[0]["start_at"]})
    assert clash.status_code == 409


def test_duplicate_message_id_is_idempotent(client):
    body = {"from_address": "a@example.com", "subject": "x", "body": "please cancel", "message_id": "<m1@x>"}
    first = client.post("/api/intake/email", json=body).json()
    second = client.post("/api/intake/email", json=body).json()
    assert first["created"] and not second["created"]
    assert first["request"]["id"] == second["request"]["id"]


def test_registration_eligibility_and_documents(client):
    payers = {p["name"]: p["id"] for p in client.get("/api/payers").json()}
    body = {"first_name": "nora", "last_name": "quinn", "date_of_birth": "1991-02-03", "phone": "312-555-0000",
            "email": "nora@example.com", "insurance": {"payer_id": payers["Cigna"], "member_id": "cig-100200"}}
    p = client.post("/api/patients", json=body)
    assert p.status_code == 201, p.text
    chart = p.json()
    assert chart["first_name"] == "Nora" and chart["policies"][0]["member_id"] == "CIG100200"
    assert client.post("/api/patients", json=body).status_code == 409  # duplicate guard
    e = client.post(f"/api/patients/{chart['id']}/eligibility", json={}).json()
    assert e["coverage_status"] == "active"
    up = client.post(f"/api/patients/{chart['id']}/documents", data={"kind": "photo_id"},
                     files={"file": ("id.png", b"\x89PNG\r\n\x1a\n fake", "image/png")})
    assert up.status_code == 201, up.text
    scan = client.post(f"/api/patients/{chart['id']}/documents", data={"kind": "referral"},
                       files={"file": ("scan.pdf", b"%PDF-1.4\n...", "application/pdf")})
    assert scan.status_code == 201, scan.text
    renamed = client.post(f"/api/patients/{chart['id']}/documents", data={"kind": "photo_id"},
                          files={"file": ("id.jpg", b"MZ\x90\x00 not a jpeg", "image/jpeg")})
    assert renamed.status_code == 422 and "match" in renamed.text
    empty = client.post(f"/api/patients/{chart['id']}/documents", data={"kind": "photo_id"},
                        files={"file": ("e.jpg", b"", "image/jpeg")})
    assert empty.status_code == 422
    bad = client.post(f"/api/patients/{chart['id']}/documents", data={"kind": "photo_id"},
                      files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 422


def test_dashboard_and_audit(client):
    inject(client, "auto_book")
    inject(client, "red_flag")
    d = client.get("/api/dashboard/summary").json()
    assert d["total_requests"] == 2 and d["auto_responded"] == 1
    assert d["review_reasons"]["CLINICAL_RED_FLAG"] == 1 and d["high_priority_open"] == 1
    assert client.get("/api/audit").json()


def test_bulk_close_and_reprocess(client):
    a = inject(client, "red_flag")          # needs review
    b = inject(client, "auto_book")         # resolved by the AI
    assert client.post("/api/requests/bulk", json={"ids": [a["id"]], "action": "close"}).status_code == 422  # note required

    rerun = client.post("/api/requests/bulk", json={"ids": [a["id"], b["id"], 999999], "action": "reprocess"}).json()
    assert [d["reference"] for d in rerun["done"]] == [a["reference"]]
    reasons = {s.get("reference") or s["id"]: s["reason"] for s in rerun["skipped"]}
    assert "reopen" in reasons[b["reference"]] or "booked" in reasons[b["reference"]]
    assert reasons[999999] == "not found"

    closed = client.post("/api/requests/bulk", json={"ids": [a["id"], b["id"]], "action": "close",
                                                     "note": "Internal mail, not a patient request"}).json()
    assert [d["status"] for d in closed["done"]] == ["closed"] and closed["skipped"][0]["reason"].startswith("already")
    detail = client.get(f"/api/requests/{a['id']}").json()
    assert detail["status"] == "closed" and detail["resolution_note"] == "Internal mail, not a patient request"
    actions = {x["action"] for x in client.get("/api/audit", params={"entity_type": "request"}).json()}
    assert {"bulk_close", "bulk_reprocess", "request_closed"} <= actions
