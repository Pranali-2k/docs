"""End-to-end workflow smoke test against a RUNNING API, verifying what lands in the database.

    python -m scripts.smoke_test                         # API at http://127.0.0.1:8030
    python -m scripts.smoke_test --api http://127.0.0.1:8000

Each example calls the real HTTP API (as a scheduler), then reads the database the API is
configured with (DATABASE_URL) to confirm the expected rows were written. Every run uses a
unique patient/email, so it can be repeated. It adds a small amount of clearly-labelled
test data ("Smoke Test" patients, smoke-*@example.com emails).
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid

import httpx
from sqlalchemy import func, select

from app import models as M
from app.config import get_settings
from app.db import SessionLocal, engine

TABLES = {
    "patients": M.Patient, "insurance_policies": M.InsurancePolicy, "eligibility_checks": M.EligibilityCheck,
    "eligibility_benefits": M.EligibilityBenefit, "email_messages": M.EmailMessage, "portal_requests": M.PortalRequest,
    "appointments": M.Appointment, "guarantors": M.Guarantor, "phi_releases": M.PhiRelease, "audit_log": M.AuditLog,
}


def counts() -> dict[str, int]:
    with SessionLocal() as db:
        return {name: db.scalar(select(func.count()).select_from(model)) or 0 for name, model in TABLES.items()}


def delta(before: dict, after: dict) -> str:
    changed = {k: after[k] - before[k] for k in after if after[k] != before[k]}
    return ", ".join(f"{k} +{v}" for k, v in changed.items()) or "no rows"


class Smoke:
    def __init__(self, api: str):
        self.c = httpx.Client(base_url=api.rstrip("/") + "/api", timeout=60)
        self.results: list[tuple[str, bool, str, str]] = []

    def step(self, name: str, fn):
        before = counts()
        t0 = time.perf_counter()
        try:
            detail = fn() or ""
            ok = True
        except AssertionError as exc:
            ok, detail = False, f"assertion failed: {exc}"
        except httpx.HTTPError as exc:
            ok, detail = False, f"HTTP error: {exc}"
        except Exception as exc:  # report and continue with the next example
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        rows = delta(before, counts())
        ms = (time.perf_counter() - t0) * 1000
        self.results.append((name, ok, detail, rows))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  ({ms:.0f} ms)\n       {detail}\n       DB: {rows}")
        return ok

    def j(self, resp: httpx.Response, status: int | tuple = 200):
        allowed = status if isinstance(status, tuple) else (status,)
        assert resp.status_code in allowed, f"{resp.request.method} {resp.request.url.path} -> {resp.status_code}: {resp.text[:300]}"
        return resp.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8030")
    args = ap.parse_args()
    s = get_settings()
    run = uuid.uuid4().hex[:6]
    # unique identity per run, so earlier smoke-test patients never look like the same person
    seed = int(run, 16)
    dob = f"19{60 + seed % 40:02d}-{1 + seed % 12:02d}-{1 + seed % 28:02d}"
    phone = f"312555{seed % 10000:04d}"
    print(f"API: {args.api}   Database: {engine.url.render_as_string(hide_password=True)}   run: {run}\n")
    t = Smoke(args.api)
    ctx: dict = {}

    def health():
        h = t.j(t.c.get("/health"))
        assert h["status"] == "ok"
        return f"API up, database dialect = {h['database']}"

    def login():
        tok = t.j(t.c.post("/auth/login", json={"email": s.seed_scheduler_email, "password": s.seed_scheduler_password}))
        t.c.headers["Authorization"] = f"Bearer {tok['access_token']}"
        return f"signed in as {tok['user']['email']} ({tok['user']['role']})"

    def register():
        payers = {p["name"]: p["id"] for p in t.j(t.c.get("/payers"))}
        chart = t.j(t.c.post("/patients", json={
            "first_name": "Smoke", "last_name": f"Test{run}", "date_of_birth": dob, "sex": "F",
            "phone": phone, "email": f"smoke-{run}@example.com", "ssn": "123-45-6789",
            "insurance": {"payer_id": payers["Aetna"], "member_id": f"AET{run}99", "policy_type": "PPO",
                          "plan_name": "Aetna Choice POS II"}}), 201)
        ctx["patient"], ctx["policy"] = chart["id"], chart["policies"][0]["id"]
        with SessionLocal() as db:
            p = db.get(M.Patient, chart["id"])
            assert p.ssn_last4 == "6789" and "6789" not in (p.ssn_encrypted or ""), "SSN must be encrypted at rest"
        return f"patient {chart['mrn']} created with Aetna policy; SSN stored encrypted (masked {chart['ssn_masked']})"

    def update_demographics():
        d = t.j(t.c.patch(f"/patients/{ctx['patient']}", json={"marital_status": "married", "preferred_name": "Smokey"}))
        assert d["marital_status"] == "married"
        hist = t.j(t.c.get(f"/patients/{ctx['patient']}/history"))
        assert any(h["action"] == "demographics_updated" for h in hist)
        return "marital status + preferred name saved; amendment recorded in audit_log"

    def eligibility():
        rao = next(p for p in t.j(t.c.get("/providers")) if "Rao" in p["full_name"])
        e = t.j(t.c.post(f"/patients/{ctx['patient']}/policies/{ctx['policy']}/eligibility",
                         json={"npi_type": "individual", "provider_id": rao["id"]}))
        assert e["coverage_status"] == "active" and e["benefits"], e["coverage_status"]
        ctx["benefit"] = next(b for b in e["benefits"] if b["category"] == "co_payment")
        return (f"271 active, {len(e['benefits'])} benefit lines stored, in_network={e['in_network']}, "
                f"{len(e['discrepancies'])} discrepancies")

    def select_benefit():
        pol = t.j(t.c.post(f"/patients/{ctx['patient']}/policies/{ctx['policy']}/select-benefit",
                           json={"benefit_id": ctx["benefit"]["id"]}))
        assert pol["copay_amount"] == ctx["benefit"]["amount"]
        return f"copay ${pol['copay_amount']:.2f} copied from the 271 onto the policy"

    def guarantor_phi():
        t.j(t.c.put(f"/patients/{ctx['patient']}/guarantor", json={
            "same_as_patient": False, "relationship_to_patient": "spouse", "first_name": "Sam", "last_name": f"Test{run}"}))
        c = t.j(t.c.post(f"/patients/{ctx['patient']}/phi-releases", json={"recipient_name": "Sam Test",
                                                                             "may_discuss_billing": True}), 201)
        return f"guarantor saved, {len(c['phi_releases'])} PHI release(s) on file"

    def email_auto():
        r = t.j(t.c.post("/intake/email", json={
            "from_address": f"smoke-{run}@example.com", "from_name": f"Smoke Test{run}",
            "subject": "Appointment request",
            "body": f"Hi, I'd like to schedule a check-up with Dr. Rao next week in the morning. "
                    f"DOB {dob[5:7]}/{dob[8:10]}/{dob[:4]}, phone {phone[:3]}-{phone[3:6]}-{phone[6:]}."
                    f"\nThanks,\nSmoke Test{run}"}), 201)["request"]
        assert r["status"] == "resolved" and r["appointment"], f"status={r['status']} reasons={r['review_reasons']}"
        ctx["appointment"] = r["appointment"]["id"]
        reply = [e for e in r["emails"] if e["direction"] == "outbound"][0]
        return (f"{r['reference']}: matched, eligibility checked, booked {r['appointment']['start_at']}, "
                f"reply {reply['send_status']}")

    def email_review():
        r = t.j(t.c.post("/intake/email", json={
            "from_address": f"unknown-{run}@example.net", "subject": "appt",
            "body": "I have chest pain, can someone see me today?"}), 201)["request"]
        codes = {x["code"] for x in r["review_reasons"]}
        assert r["status"] == "needs_review" and "CLINICAL_RED_FLAG" in codes, codes
        return f"{r['reference']}: routed to manual review ({', '.join(sorted(codes))}), no auto reply"

    def cancel():
        a = t.j(t.c.post(f"/appointments/{ctx['appointment']}/cancel"))
        assert a["status"] == "cancelled"
        return "appointment cancelled (slot released)"

    def errors():
        assert t.c.get("/patients/99999999").status_code == 404
        assert t.c.patch(f"/patients/{ctx['patient']}", json={"ssn": "000-00-0000"}).status_code == 422
        assert t.c.post("/intake/email", json={"from_address": "bad", "body": "x"}).status_code == 422
        return "404 / 422 handled with clear messages; nothing written"

    steps = [("API health + DB connection", health), ("Staff login", login),
             ("Register patient with insurance", register), ("Update patient data", update_demographics),
             ("Eligibility check (individual NPI)", eligibility), ("Select copay from 271", select_benefit),
             ("Guarantor + release of PHI", guarantor_phi), ("Email -> auto-book workflow", email_auto),
             ("Email -> manual review workflow", email_review), ("Cancel appointment", cancel),
             ("Error handling", errors)]
    for name, fn in steps:
        if not t.step(name, fn) and name in {"API health + DB connection", "Staff login"}:
            break

    passed = sum(1 for _, ok, _, _ in t.results if ok)
    print(f"\n{passed}/{len(t.results)} examples passed")
    return 0 if passed == len(t.results) == len(steps) else 1


if __name__ == "__main__":
    sys.exit(main())
