"""EHR-style chart: patient data, protected identifiers, guarantor, PHI releases,
insurance policy editing, NPI eligibility with 271 benefits, discrepancies and Select."""
from sqlalchemy import select

from app.llm.providers import LLMProvider
from app.models import Patient


def _patient(client, email):
    hits = client.get("/api/patients", params={"q": email}).json()
    assert hits, email
    return client.get(f"/api/patients/{hits[0]['id']}").json()


def _primary(chart):
    return next(p for p in chart["policies"] if p["is_active"] and p["coverage_order"] == "primary")


# --------------------------------------------------------------------------- patient data

def test_patient_data_and_protected_identifiers(client, db):
    c = _patient(client, "maria.gonzalez@example.com")
    r = client.patch(f"/api/patients/{c['id']}", json={
        "prefix": "Ms.", "middle_name": "Elena", "suffix": "", "preferred_name": "Mari", "marital_status": "Married",
        "previous_name": "Maria Ruiz", "ssn": "123-45-2110", "drivers_license_number": "G123-4567-8910",
        "drivers_license_state": "il", "home_phone": "312 555 0100",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["marital_status"] == "married" and d["drivers_license_state"] == "IL"
    assert d["ssn_masked"] == "###-##-2110" and d["drivers_license_masked"] == "****8910"
    assert "123452110" not in r.text and "ssn_encrypted" not in r.text

    db.expire_all()
    row = db.get(Patient, c["id"])
    assert row.ssn_encrypted and "2110" not in row.ssn_encrypted  # encrypted at rest

    rev = client.post(f"/api/patients/{c['id']}/reveal", json={"field": "ssn", "reason": "billing verification"})
    assert rev.status_code == 200 and rev.json()["value"] == "123-45-2110"
    hist = client.get(f"/api/patients/{c['id']}/history").json()
    actions = [h["action"] for h in hist]
    assert "sensitive_identifier_viewed" in actions and "demographics_updated" in actions
    change = next(h for h in hist if h["action"] == "demographics_updated")["details"]["changes"]
    assert change["ssn"]["to"] == "***-**-2110"  # history never stores the full SSN


def test_patient_data_validation(client):
    c = _patient(client, "james.carter@example.com")
    for bad in ({"ssn": "000-12-3456"}, {"marital_status": "complicated"}, {"home_phone": "123"},
                {"date_of_birth": "2999-01-01"}, {"first_name": ""}):
        r = client.patch(f"/api/patients/{c['id']}", json=bad)
        assert r.status_code == 422, (bad, r.text)


def test_guarantor_and_phi_release(client):
    c = _patient(client, "walker.family@example.com")
    bad = client.put(f"/api/patients/{c['id']}/guarantor", json={"same_as_patient": False})
    assert bad.status_code == 422
    ok = client.put(f"/api/patients/{c['id']}/guarantor", json={
        "same_as_patient": False, "relationship_to_patient": "parent", "first_name": "Sarah", "last_name": "Walker",
        "phone": "3125550150"})
    assert ok.status_code == 200 and ok.json()["guarantor"]["first_name"] == "Sarah"

    rel = client.post(f"/api/patients/{c['id']}/phi-releases", json={
        "recipient_name": "Sarah Walker", "relationship_to_patient": "mother", "may_discuss_billing": True,
        "signed_on": "2026-09-01", "expires_on": "2027-09-01"})
    assert rel.status_code == 201
    rid = rel.json()["phi_releases"][0]["id"]
    assert client.post(f"/api/patients/{c['id']}/phi-releases/{rid}/revoke").status_code == 200
    assert client.post(f"/api/patients/{c['id']}/phi-releases/{rid}/revoke").status_code == 409
    bad_dates = client.post(f"/api/patients/{c['id']}/phi-releases", json={
        "recipient_name": "X", "signed_on": "2026-09-01", "expires_on": "2026-01-01"})
    assert bad_dates.status_code == 422


# --------------------------------------------------------------------------- insurance

def test_payer_lookup_and_plans(client):
    hits = client.get("/api/payers", params={"q": "94036"}).json()
    assert hits and hits[0]["label"] == "Blue Shield of California (94036)"
    plans = client.get(f"/api/payers/{hits[0]['id']}/plans", params={"q": "epo"}).json()
    assert plans and plans[0]["plan_type"] == "EPO"


def test_edit_archive_and_replace_policy(client):
    c = _patient(client, "priya.shah@example.com")
    pol = _primary(c)
    url = f"/api/patients/{c['id']}/policies/{pol['id']}"
    r = client.patch(url, json={"policy_type": "HMO", "notes": "Card verified at front desk", "requires_referral": True,
                                "effective_date": "2026-01-01", "end_date": "2026-12-31", "group_number": "G77"})
    assert r.status_code == 200, r.text
    assert r.json()["policy_type"] == "HMO" and r.json()["requires_referral"] is True
    for bad in ({"policy_type": "XYZ"}, {"coinsurance_pct": 140}, {"copay_amount": -5},
                {"effective_date": "2026-06-01", "end_date": "2026-01-01"}, {"notes": "x" * 501}):
        assert client.patch(url, json=bad).status_code == 422, bad
    hist = client.get(f"/api/patients/{c['id']}/history", params={"entity": "policy"}).json()
    assert any(h["action"] == "policy_updated" and "policy_type" in h["details"]["changes"] for h in hist)

    payers = {p["name"]: p["id"] for p in client.get("/api/payers").json()}
    new = client.post(f"{url}/replace", json={"payer_id": payers["Aetna"], "member_id": "AET-1234-55",
                                              "policy_type": "PPO", "plan_name": "Aetna Choice POS II"})
    assert new.status_code == 201, new.text
    assert new.json()["coverage_order"] == "primary" and new.json()["member_id"] == "AET123455"
    chart = client.get(f"/api/patients/{c['id']}").json()
    old = next(p for p in chart["policies"] if p["id"] == pol["id"])
    assert old["is_active"] is False and old["archived_at"]
    assert client.patch(url, json={"notes": "late edit"}).status_code == 422  # archived policies are read-only


# --------------------------------------------------------------------------- eligibility

def test_eligibility_by_individual_and_group_npi(client):
    c = _patient(client, "maria.gonzalez@example.com")
    pol = _primary(c)
    rao = next(p for p in client.get("/api/providers").json() if p["full_name"] == "Dr. Anita Rao")
    url = f"/api/patients/{c['id']}/policies/{pol['id']}/eligibility"

    r = client.post(url, json={"npi_type": "individual", "provider_id": rao["id"], "service_date": "2026-10-20"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["coverage_status"] == "active" and e["npi"] == rao["npi"] and e["in_network"] is True
    assert e["date_of_service"] == "2026-10-20" and e["plan_start"] == "2026-01-01"
    cats = {(b["network"], b["category"]) for b in e["benefits"]}
    assert ("in", "co_payment") in cats and ("in", "remaining_deductible") in cats
    assert any(b["auth_required"] for b in e["benefits"])
    assert "ST*271" in e["raw_x12"] and f"XX*{rao['npi']}" in e["raw_x12"]
    assert "copay" in e["ai_summary"].lower()

    g = client.post(url, json={"npi_type": "group", "service_date": "2026-10-20"}).json()
    info = client.get("/api/system/info").json()
    assert g["npi_type"] == "group" and g["npi"] == info["practice_group_npi"]

    view = client.get(f"/api/patients/{c['id']}/eligibility/{e['id']}")
    assert view.status_code == 200 and view.json()["patient_name"] == "GONZALEZ, MARIA"
    assert client.get(f"/api/patients/{c['id']}/policies/{pol['id']}").json()["eligibility_status"] == "active"


def test_discrepancies_are_reported(client):
    avery = _patient(client, "avery.morgan@example.com")
    e = client.post(f"/api/patients/{avery['id']}/policies/{_primary(avery)['id']}/eligibility",
                    json={"npi_type": "group"}).json()
    fields = {d["field"] for d in e["discrepancies"]}
    assert "Policy type" in fields  # chart says PPO, payer says EPO

    jordan = _patient(client, "jordan.ellis@example.com")
    e2 = client.post(f"/api/patients/{jordan['id']}/policies/{_primary(jordan)['id']}/eligibility",
                     json={"npi_type": "group"}).json()
    d = {x["field"]: x for x in e2["discrepancies"]}
    assert d["Date of birth"]["severity"] == "error" and "Patient name" in d


def test_inactive_coverage(client):
    linda = _patient(client, "linda.brooks@example.com")
    pol = _primary(linda)
    e = client.post(f"/api/patients/{linda['id']}/policies/{pol['id']}/eligibility", json={"npi_type": "group"}).json()
    assert e["coverage_status"] == "inactive" and e["eligibility_end"]
    assert "INACTIVE" in e["ai_summary"]


def test_select_benefit_copies_value_to_policy(client):
    c = _patient(client, "tom.nguyen@example.com")
    pol = _primary(c)
    base = f"/api/patients/{c['id']}/policies/{pol['id']}"
    e = client.post(f"{base}/eligibility", json={"npi_type": "group"}).json()
    copay = next(b for b in e["benefits"] if b["category"] == "co_payment" and "PRIMARY CARE" in (b["message"] or ""))
    r = client.post(f"{base}/select-benefit", json={"benefit_id": copay["id"]})
    assert r.status_code == 200 and r.json()["copay_amount"] == copay["amount"]
    ded = next(b for b in e["benefits"] if b["category"] == "remaining_deductible" and b["coverage_level"] == "individual")
    assert client.post(f"{base}/select-benefit", json={"benefit_id": ded["id"]}).json()["remaining_deductible"] == ded["amount"]
    fam = next(b for b in e["benefits"] if b["coverage_level"] == "family")
    assert client.post(f"{base}/select-benefit", json={"benefit_id": fam["id"]}).status_code == 422

    other = _patient(client, "grace.okafor@example.com")
    assert client.post(f"/api/patients/{other['id']}/policies/{_primary(other)['id']}/select-benefit",
                       json={"benefit_id": copay["id"]}).status_code == 422  # benefit from another policy


def test_eligibility_error_paths(client):
    c = _patient(client, "maria.gonzalez@example.com")
    pol = _primary(c)
    url = f"/api/patients/{c['id']}/policies/{pol['id']}/eligibility"
    assert client.post(url, json={"npi_type": "bogus"}).status_code == 422
    assert client.post(url, json={"npi_type": "individual", "provider_id": 99999}).status_code == 404
    assert client.get(f"/api/patients/{c['id']}/eligibility/99999").status_code == 404
    assert client.post(f"/api/patients/{c['id']}/policies/99999/eligibility", json={}).status_code == 404


# --------------------------------------------------------------------------- LLM summary guard

class _LLM(LLMProvider):
    name = "fake"

    def __init__(self, text):
        self.text = text

    def complete(self, system, user, json_mode=False):
        return self.text


def test_llm_summary_must_be_grounded(client, db, monkeypatch):
    from app.models import EligibilityCheck
    from app.services import benefits_summary

    c = _patient(client, "maria.gonzalez@example.com")
    e = client.post(f"/api/patients/{c['id']}/policies/{_primary(c)['id']}/eligibility", json={"npi_type": "group"}).json()
    db.expire_all()
    check = db.get(EligibilityCheck, e["id"])
    patient = db.get(Patient, c["id"])
    template = benefits_summary.summarize(check, patient)

    monkeypatch.setattr(benefits_summary, "get_llm", lambda: _LLM("Active plan. Office copay is $5 only."))
    assert benefits_summary.summarize(check, patient) == template  # invented $5 rejected

    grounded = "Coverage is active. " + " ".join(f for f in benefits_summary._facts(check)[2:3])
    monkeypatch.setattr(benefits_summary, "get_llm", lambda: _LLM(grounded))
    assert benefits_summary.summarize(check, patient) == grounded
    assert db.scalar(select(Patient).where(Patient.id == c["id"]))
