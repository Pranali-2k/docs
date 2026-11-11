"""Sample data for the insert procedures, loaded through the Python functions.

    python -m scripts.procs.seed_sample_data --url postgresql://user:pass@localhost:5432/docs_health_procs

(`bootstrap_procs_db.py` runs this for you.) Everything goes in by calling the procedures
in docs_procedures.py, so the procedures' own validation applies to the sample data too.

Patients are ModMed sandbox patients read from Dr Patel's data/patients_original.json
(fictional people from ModMed's sandbox); if that file is not found, a small built-in list
of the same kind is used. Staff, providers, payers, mailboxes and tokens are fictional.
No real credentials: passwords are hashed placeholders, tokens are random, and the
integration connection has no secret.

Repeatable: a row that already exists (the procedure says "already exists") is skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import psycopg

from scripts.procs import docs_procedures as db

DR_PATEL_PATIENTS = Path(r"C:\new\patient_chatbot_5\data\patients_original.json")
FALLBACK_PATIENTS = [
    {"id": "375404", "given": "Valeria", "family": "Costello", "dob": "1972-05-07", "sex": "female", "phone": "5559274399"},
    {"id": "375405", "given": "Shana", "family": "O'hara", "dob": "1982-06-02", "sex": "female", "phone": "5558505336"},
    {"id": "375406", "given": "Savannah", "family": "Peoples", "dob": "1983-06-16", "sex": "female", "phone": "5550106016"},
    {"id": "375407", "given": "Brian", "family": "Cornett", "dob": "1984-12-16", "sex": "male", "phone": "5550107007"},
    {"id": "375408", "given": "Brian", "family": "Aragon", "dob": "1969-08-11", "sex": "male", "phone": "5550108008"},
    {"id": "375409", "given": "Bryan", "family": "Frederick", "dob": "1997-12-05", "sex": "male", "phone": "5550109009"},
]


class Tally:
    def __init__(self) -> None:
        self.added = 0
        self.skipped = 0

    def run(self, call, *args, **kwargs) -> bool:
        """Call one function; a duplicate is skipped, any other rejection is raised."""
        try:
            call(*args, **kwargs)
        except db.ProcedureError as error:
            if "already exists" in str(error):
                self.skipped += 1
                return False
            raise
        self.added += 1
        return True


def load_patients(limit: int = 8) -> list[dict]:
    """ModMed sandbox patients as flat dicts: id, given, family, dob, sex, phone, city, state, postal."""
    if DR_PATEL_PATIENTS.exists():
        out = []
        for p in json.loads(DR_PATEL_PATIENTS.read_text("utf-8")):
            name = (p.get("name") or [{}])[0]
            if not name.get("family") or "test" in json.dumps(name).lower():
                continue
            phone = next((t["value"] for t in p.get("telecom", []) if t.get("system") == "phone"), "")
            address = (p.get("address") or [{}])[0]
            out.append({
                "id": p["id"], "given": " ".join(name.get("given", [])) or "Unknown", "family": name["family"],
                "dob": p.get("birthDate"), "sex": p.get("gender"), "phone": "".join(c for c in phone if c.isdigit())[-10:],
                "line": " ".join(address.get("line", [])), "city": address.get("city"),
                "state": address.get("state"), "postal": address.get("postalCode"),
            })
            if len(out) >= limit:
                break
        if out:
            return out
    return FALLBACK_PATIENTS[:limit]


def one(conn: psycopg.Connection, query: str, *params):
    row = conn.execute(query, params).fetchone()
    return row[0] if row else None


def absent(conn: psycopg.Connection, query: str, *params) -> bool:
    """True when the query finds no row - for the tables whose procedures have no duplicate check."""
    return conn.execute(query, params).fetchone() is None


def seed(conn: psycopg.Connection) -> Tally:
    t = Tally()
    now = datetime.now(UTC)
    today = date.today()

    # ---- staff ---------------------------------------------------------------------------
    placeholder = "pbkdf2_sha256$1$sample$" + hashlib.sha256(b"sample").hexdigest()  # not a usable password
    for email, name, role in (("admin@docshealth.example", "Avery Admin", "admin"),
                              ("scheduler@docshealth.example", "Sam Scheduler", "scheduler"),
                              ("billing@docshealth.example", "Bea Billing", "scheduler")):
        t.run(db.insert_user, conn, email=email, full_name=name, role=role, password_hash=placeholder, is_active=True)

    # ---- payers and plans ----------------------------------------------------------------
    payers = (("Aetna", "AETNA", ["Aetna Inc.", "Aetna Health"], "60054", "commercial"),
              ("UnitedHealthcare", "UHC", ["United Healthcare", "UHC"], "87726", "commercial"),
              ("Cigna", "CIGNA", ["Cigna Healthcare"], "62308", "commercial"),
              ("Medicare", "MEDICARE", ["Medicare Part B"], "00882", "medicare"))
    for name, code, aliases, edi, category in payers:
        t.run(db.insert_payer, conn, name=name, payer_code=code, aliases=aliases, electronic_payer_id=edi,
              financial_category=category, claims_addresses=[{"line1": f"PO Box {1000 + len(code)}", "city": "Dallas", "state": "TX"}])
    payer_ids = {name: one(conn, "SELECT id FROM public.payers WHERE name = %s", name) for name, *_ in payers}
    for payer, plan, kind in (("Aetna", "Aetna Choice POS II", "PPO"), ("Aetna", "Aetna HMO Open Access", "HMO"),
                              ("UnitedHealthcare", "UHC Choice Plus", "PPO"), ("Cigna", "Cigna Open Access Plus", "PPO"),
                              ("Medicare", "Medicare Part B", "Medicare")):
        t.run(db.insert_payer_plan, conn, payer_id=payer_ids[payer], name=plan, plan_type=kind)

    # ---- providers, networks, availability -----------------------------------------------
    providers = (("Dr. Elena Torres", "Orthopedic Surgery", "1000000011", "Main Clinic"),
                 ("Dr. Marcus Patel", "Sports Medicine", "1000000029", "Main Clinic"),
                 ("Dr. Priya Rao", "Internal Medicine", "1000000037", "North Clinic"))
    for name, specialty, npi, location in providers:
        t.run(db.insert_provider, conn, full_name=name, specialty=specialty, npi=npi, location=location, is_active=True)
    provider_ids = {name: one(conn, "SELECT id FROM public.providers WHERE npi = %s", npi) for name, _s, npi, _l in providers}
    for name, pid in provider_ids.items():
        for payer, payer_id in payer_ids.items():
            t.run(db.insert_provider_network, conn, provider_id=pid, payer_id=payer_id,
                  in_network=not (name == "Dr. Priya Rao" and payer == "Cigna"))
        for weekday in range(0, 5):  # Monday to Friday
            t.run(db.insert_provider_availability, conn, provider_id=pid, weekday=weekday,
                  start_time=time(9, 0), end_time=time(17, 0), slot_minutes=30)

    # ---- patients and everything that hangs off them -------------------------------------
    people = load_patients()
    first_provider = provider_ids["Dr. Elena Torres"]
    for p in people:
        t.run(db.insert_patient, conn, mrn=f"MM{p['id']}", first_name=p["given"], last_name=p["family"],
              is_test_patient=False, date_of_birth=date.fromisoformat(p["dob"]) if p.get("dob") else None,
              sex=(p.get("sex") or "").title() or None, phone=p.get("phone") or None,
              preferred_contact_method="phone", email=f"{p['given'].split()[0].lower()}.{p['family'].lower().replace(chr(39), '')}@patients.example",
              address_line1=p.get("line") or None, city=p.get("city"), state=p.get("state"), postal_code=p.get("postal"),
              preferred_provider_id=first_provider, notes="Sample patient from the ModMed sandbox.")
    patient_ids = {p["id"]: one(conn, "SELECT id FROM public.patients WHERE mrn = %s", f"MM{p['id']}") for p in people}

    payer_cycle = list(payer_ids.items())
    for i, p in enumerate(people):
        pid = patient_ids[p["id"]]
        payer, payer_id = payer_cycle[i % len(payer_cycle)]
        t.run(db.insert_insurance_policy, conn, patient_id=pid, payer_id=payer_id, member_id=f"W{200300400 + i}",
              group_number=f"G{5000 + i}", subscriber_name=f"{p['given']} {p['family']}",
              relationship_to_subscriber="self", is_primary=True, is_active=True, coverage_order="primary",
              plan_name=f"{payer} sample plan", policy_type="PPO", requires_referral=False,
              requires_precert_inpatient=True, requires_preauth_outpatient=False, eligibility_status="active",
              copay_amount=30, coinsurance_pct=20, deductible=1500, remaining_deductible=900,
              out_of_pocket=4000, remaining_out_of_pocket=3100, effective_date=today.replace(month=1, day=1))
        t.run(db.insert_guarantor, conn, patient_id=pid, same_as_patient=True, relationship_to_patient="self")
    for p in people[:3]:  # a release for a few patients
        if absent(conn, "SELECT 1 FROM public.phi_releases WHERE patient_id = %s", patient_ids[p["id"]]):
          t.run(db.insert_phi_release, conn, patient_id=patient_ids[p["id"]], recipient_name="Jordan Family-Member",
              relationship_to_patient="spouse", phone="5550100100", may_discuss_appointments=True,
              may_discuss_billing=True, may_discuss_clinical=False, signed_on=today - timedelta(days=30),
              expires_on=today + timedelta(days=335), created_by="scheduler@docshealth.example")
    for p in people[:3]:  # document records (the files themselves are not created)
        if absent(conn, "SELECT 1 FROM public.patient_documents WHERE patient_id = %s", patient_ids[p["id"]]):
          t.run(db.insert_patient_document, conn, patient_id=patient_ids[p["id"]], kind="insurance_card_front",
              filename=f"{p['family'].lower()}_card_front.jpg", content_type="image/jpeg",
              storage_path=f"sample/{p['id']}/card_front.jpg", size_bytes=84213, uploaded_by="scheduler@docshealth.example")

    # ---- mailbox, an inbound request and its reply ---------------------------------------
    t.run(db.insert_integration_connection, conn, provider="microsoft_graph", auth_mode="application",
          status="not_connected", poll_interval_seconds=60, connected_by="admin@docshealth.example")
    connection_id = one(conn, "SELECT id FROM public.integration_connections WHERE provider = %s", "microsoft_graph")
    t.run(db.insert_integration_mailbox, conn, connection_id=connection_id, address="scheduling@docshealth.example",
          display_name="Docs Health Scheduling", folder="Inbox", enabled=True, process_inbound=True,
          reply_from_same_mailbox=True, mark_as_read=True, is_default_sender=True, created_by="admin@docshealth.example",
          skip_sender_domains="noreply.example,notifications.example")
    mailbox_id = one(conn, "SELECT id FROM public.integration_mailboxes WHERE address = %s", "scheduling@docshealth.example")

    request_patient = people[0]
    asked = (f"Hi, this is {request_patient['given']} {request_patient['family']}. Please book a visit with "
             f"Dr. Torres next Wednesday morning. My date of birth is {request_patient.get('dob', '1972-05-07')}.")
    t.run(db.insert_email_message, conn, message_id="<sample-request-1@patients.example>", direction="inbound",
          source="mailbox", from_address="valeria.costello@patients.example", from_name=f"{request_patient['given']} {request_patient['family']}",
          to_address="scheduling@docshealth.example", subject="Appointment request", body_text=asked, received_at=now - timedelta(hours=5),
          delivery_method="graph", mailbox_id=mailbox_id, conversation_id="conv-sample-1", body_raw=asked)
    email_id = one(conn, "SELECT id FROM public.email_messages WHERE message_id = %s", "<sample-request-1@patients.example>")
    t.run(db.insert_portal_request, conn, reference="REQ-1001", status="needs_review", intent="new_appointment",
          source_email_id=email_id, extracted={"provider": "Dr. Torres", "when": "next Wednesday morning"},
          patient_id=patient_ids[request_patient["id"]], match_confidence=0.97,
          match_details={"matched_on": ["name", "date_of_birth"]}, provider_id=provider_ids["Dr. Elena Torres"],
          ai_confidence=0.88, ai_summary="Patient asks for a morning visit with Dr. Torres next Wednesday.",
          decision="review", review_reasons=[], draft_response="Hello, we can offer Wednesday at 9:00 or 9:30. Which suits you?",
          assigned_to="scheduler@docshealth.example", priority="normal", llm_provider="huggingface")
    request_id = one(conn, "SELECT id FROM public.portal_requests WHERE reference = %s", "REQ-1001")
    t.run(db.insert_email_message, conn, message_id="<sample-reply-1@docshealth.example>", direction="outbound",
          source="portal", from_address="scheduling@docshealth.example", to_address="valeria.costello@patients.example",
          subject="Re: Appointment request", body_text="Hello, we can offer Wednesday at 9:00 or 9:30. Which suits you?",
          in_reply_to="<sample-request-1@patients.example>", request_id=request_id, send_status="outbox",
          sent_by="scheduler@docshealth.example", received_at=now - timedelta(hours=4), delivery_method="outbox",
          mailbox_id=mailbox_id, conversation_id="conv-sample-1")

    # ---- eligibility ---------------------------------------------------------------------
    policy_id = one(conn, "SELECT id FROM public.insurance_policies WHERE patient_id = %s",
                    patient_ids[people[0]["id"]])
    first_patient = patient_ids[people[0]["id"]]
    new_check = absent(conn, "SELECT 1 FROM public.eligibility_checks WHERE patient_id = %s", first_patient)
    if new_check:
      t.run(db.insert_eligibility_check, conn, patient_id=first_patient, policy_id=policy_id,
          provider_id=provider_ids["Dr. Elena Torres"], request_id=request_id, coverage_status="active", in_network=True,
          plan_name="Sample plan", copay="$30", message="Coverage active.", raw_response={"source": "sample", "active": True},
          checked_by="scheduler@docshealth.example", checked_at=now - timedelta(hours=3), date_of_service=today + timedelta(days=6),
          plan_start=today.replace(month=1, day=1), plan_end=today.replace(month=12, day=31),
          eligibility_start=today.replace(month=1, day=1), eligibility_end=today.replace(month=12, day=31),
          discrepancies=[], ai_summary="Active coverage; in network.")
    check_id = one(conn, "SELECT id FROM public.eligibility_checks WHERE patient_id = %s ORDER BY id DESC LIMIT 1",
                   patient_ids[people[0]["id"]])
    for network, category, level, code, service, amount, percent, period in (
            ("IN", "copay", "individual", "98", "Professional visit - office", 30, None, "visit"),
            ("IN", "coinsurance", "individual", "98", "Professional visit - office", None, 20, "calendar_year"),
            ("IN", "deductible", "individual", "30", "Health benefit plan coverage", 1500, None, "calendar_year")):
        if new_check:  # benefits belong to the check, so they are added only with it
            t.run(db.insert_eligibility_benefit, conn, check_id=check_id, network=network, category=category,
                  coverage_level=level, service_type_code=code, service_type=service, amount=amount, percent=percent,
                  time_period=period, auth_required=False)

    # ---- login and OAuth plumbing (random values; nothing usable) ------------------------
    admin_id = one(conn, "SELECT id FROM public.users WHERE email = %s", "admin@docshealth.example")
    if absent(conn, "SELECT 1 FROM public.password_reset_tokens WHERE user_id = %s", admin_id):
      t.run(db.insert_password_reset_token, conn, user_id=admin_id, token_hash=hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
          expires_at=now + timedelta(hours=1), requested_ip="127.0.0.1")
    if absent(conn, "SELECT 1 FROM public.oauth_states WHERE created_by = %s", "admin@docshealth.example"):
      t.run(db.insert_oauth_state, conn, state_hash=hashlib.sha256(secrets.token_bytes(16)).hexdigest(), provider="microsoft_graph",
          flow="connect", created_by="admin@docshealth.example", expires_at=now + timedelta(minutes=10))
    for key, value in (("clinic_name", {"value": "Docs Health Sample Clinic"}),
                       ("appointment_slot_minutes", {"value": 30}),
                       ("arrive_early_minutes", {"value": 15}),
                       ("outbound_mail", {"mode": "outbox"})):
        t.run(db.insert_app_setting, conn, key=key, value=value, updated_by="admin@docshealth.example")

    # ---- Dr Patel's side: sessions, summaries, exports and call-booked appointments ------
    complaints = (("Right knee pain x 2 weeks", "Pain on stairs, mild evening swelling. Pain severity: 5/10.", "M25.561", "73562"),
                  ("Low back pain x 1 week", "Began after lifting a box; no leg symptoms. Pain severity: 6/10.", "M54.50", "72100"),
                  ("Morning headaches x 1 month", "Daily, mild light sensitivity. Pain severity: 4/10.", "R51.9", "99203"),
                  ("Left shoulder pain x 4 days", "Pain reaching overhead; no injury recalled. Pain severity: 5/10.", "M25.512", "73030"))
    day = today + timedelta(days=1)
    for i, (p, (complaint, history, icd, cpt)) in enumerate(zip(people, complaints)):
        session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"docs-health-procs-session-{p['id']}"))
        name = f"{p['given']} {p['family']}"
        started = now - timedelta(days=2, hours=i)
        t.run(db.insert_session_registry, conn, session_id=session_id, patient_id=p["id"], patient_name=name,
              timestamp=started, conversation_table=f"conversation_{session_id[:8]}")
        t.run(db.insert_session_summary, conn, session_id=session_id, patient_id=p["id"], patient_name=name,
              json_filename=f"session_phone_{session_id[:8]}.json", language="en", message_count=12, generated_at=started,
              chief_complaint=complaint, history_of_present_illness=history, current_symptoms=history,
              allergies="No known drug allergies", current_medications="Ibuprofen as needed",
              past_medical_history="None reported", family_history="Non-contributory", social_history="Non-smoker",
              other_notes="Sample data.", summary_text=f"CHIEF COMPLAINT\n{complaint}\n\nHISTORY OF PRESENT ILLNESS\n{history}",
              llm_provider="sample", llm_model="none", icd_10=icd, icd_10_details=[{"code": icd, "confidence": 0.7}],
              icd_10_status="suggested", cpt=cpt, cpt_details=[{"code": cpt, "confidence": 0.5}], cpt_status="suggested")
        t.run(db.insert_summary, conn, summary_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"docs-health-procs-summary-{p['id']}")),
              session_id=session_id, patient_id=int(p["id"]), chief_complaint=complaint, current_symptoms=history,
              allergies="No known drug allergies", past_history="None reported", additional_notes="Sample data.")
        t.run(db.insert_emr_export, conn, export_id=f"EXP-{p['id']}", session_id=session_id, patient_id=p["id"],
              patient_name=name, exported_at=now - timedelta(days=1, hours=i), export_file=f"emr_{p['id']}.json",
              payload={"patient": name, "icd_10": icd, "cpt": cpt, "source": "sample"})
        while day.weekday() > 4:  # booked on weekdays only
            day += timedelta(days=1)
        slot = datetime.combine(day, time(9 + i, 0)).astimezone()
        t.run(db.insert_appointment, conn, appointment_id=f"APT-SAMPLE-{p['id']}", session_id=session_id, patient_id=p["id"],
              icd_10=icd, patient_name=name, scheduled_for=slot, duration_minutes=30, clinician="Dr. Patel",
              reason=f"{complaint}. Booked during a phone call.", status="booked", booked_via="phone", cpt=cpt,
              patient_email=f"{p['given'].split()[0].lower()}.{p['family'].lower().replace(chr(39), '')}@patients.example",
              email_status="not_sent")
        day += timedelta(days=1)
    conn.commit()
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="postgresql://user:password@host:port/database")
    args = ap.parse_args()
    with psycopg.connect(args.url) as conn:
        tally = seed(conn)
    print(f"sample data: {tally.added} rows added, {tally.skipped} already there")


if __name__ == "__main__":
    main()
