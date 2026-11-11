"""Load a few ModMed patients into DOCS Health and run appointment emails for them.

    .venv\\Scripts\\python -m scripts.seed_modmed_examples            # load
    .venv\\Scripts\\python -m scripts.seed_modmed_examples --dry-run  # show what would load

Reads, read-only, ModMed FHIR Patient records (default
C:\\new\\patient_chatbot_5\\data\\patients_original.json), then through the running API:

* registers six of them as DOCS patients (the ModMed id is kept in the chart notes),
* books two appointments, and
* sends five appointment-request emails into the same inbox the Microsoft mailbox feeds
  (new appointment, reschedule, cancel), so the AI assistant handles them like real mail.

Nothing is sent to anyone: outbound email stays in the outbox. Patient addresses are
rewritten to example.com so a later switch to live sending cannot reach a real person.
Repeatable: registered patients and emails are matched by name/date of birth and message id.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

SOURCE = Path(r"C:\new\patient_chatbot_5\data\patients_original.json")
PICK = ["375404", "375405", "375406", "375407", "375408", "375409",
        "375410", "375411", "375414", "375415", "375416", "375417",
        "375425", "375426", "375427", "375428", "375429", "375430"]  # last six: asking for a named doctor  # skips ModMed's "Test" records


def call(base: str, method: str, path: str, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(
        f"{base}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", **({"authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except ValueError:
            return exc.code, None


def to_patient(p: dict) -> dict:
    name = p["name"][0]
    first = " ".join(name.get("given", [])).strip()
    last = name["family"]
    phone = next((t["value"] for t in p.get("telecom", []) if t.get("system") == "phone"), None)
    addr = (p.get("address") or [{}])[0]
    local = f"{first.split()[0]}.{last}".lower().replace("'", "").replace(" ", "")
    return {
        "first_name": first.split()[0], "middle_name": " ".join(first.split()[1:]) or None, "last_name": last,
        "date_of_birth": p["birthDate"], "sex": p.get("gender"), "phone": phone,
        "email": f"{local}@example.com", "address_line1": (addr.get("line") or [None])[0],
        "city": addr.get("city"), "state": addr.get("state"), "postal_code": addr.get("postalCode"),
        "notes": f"Example patient from ModMed (id {p['id']}).",
    }


def next_weekdays(n: int):
    d, out = date.today(), []
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8030")
    parser.add_argument("--source", default=str(SOURCE))
    parser.add_argument("--email", default=os.environ.get("SEED_SCHEDULER_EMAIL", "scheduler@docshealth.local"))
    parser.add_argument("--password", default=os.environ.get("SEED_SCHEDULER_PASSWORD", "DemoSched!2026"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    records = {p["id"]: p for p in json.loads(Path(args.source).read_text(encoding="utf-8"))}
    chosen = [to_patient(records[i]) for i in PICK if i in records]
    if args.dry_run:
        for p in chosen:
            print(f"would register {p['first_name']} {p['last_name']} ({p['date_of_birth']}) <{p['email']}>")
        return 0

    status, login = call(args.api, "POST", "/api/auth/login", body={"email": args.email, "password": args.password})
    if status != 200:
        print(f"sign-in failed ({status})")
        return 1
    token = login["access_token"]

    patients: dict[str, dict] = {}
    for p in chosen:
        status, chart = call(args.api, "POST", "/api/patients", token, p)
        if status == 409:  # already registered: find it
            _, found = call(args.api, "GET", f"/api/patients?q={p['last_name']}", token)
            rows = found.get("items", found) if isinstance(found, dict) else found
            chart = next((r for r in rows if r.get("date_of_birth") == p["date_of_birth"]), None)
            note = "already registered"
        elif status == 201:
            note = "registered"
        else:
            print(f"{p['last_name']}: could not register ({status}) {chart}")
            continue
        patients[p["last_name"]] = {"data": p, "chart": chart, "new": note == "registered"}
        print(f"{p['first_name']} {p['last_name']}: {note}")

    # ModMed records carry no insurance. One patient gets a policy so her email books by itself;
    # the others stop at "no insurance on file", which is the manual-review path staff see.
    _, payers = call(args.api, "GET", "/api/payers?q=aetna", token)
    for last in ("Peoples", "Baxter", "Ross", "Schafer", "Ladd", "Chin", "Solis", "Castaneda", "Sherman",
                 "Hathaway", "Knox"):
        info = patients.get(last)
        if info and info["new"] and payers:
            status, _ = call(args.api, "POST", f"/api/patients/{info['chart']['id']}/insurance", token,
                             {"payer_id": payers[0]["id"], "member_id": "AET777888999", "group_number": "45120",
                              "coverage_order": "primary"})
            print(f"{last}: insurance -> {status}")

    _, providers = call(args.api, "GET", "/api/providers", token)
    provider = providers[0]
    days = next_weekdays(6)

    def book(patient_key: str, day: date, reason: str):
        info = patients.get(patient_key)
        if not info or not info["new"]:
            return None
        _, slots = call(args.api, "GET", f"/api/providers/{provider['id']}/slots?day={day}", token)
        if not slots:
            return None
        pid = info["chart"].get("id") or info["chart"]["patient"]["id"]
        status, appt = call(args.api, "POST", "/api/appointments", token,
                            {"patient_id": pid, "provider_id": provider["id"], "start_at": slots[0]["start_at"],
                             "visit_reason": reason})
        print(f"{patient_key}: appointment {day} -> {status}")
        return slots[0]["label"], day if status == 201 else None

    booked = {"Cornett": book("Cornett", days[1], "Follow-up visit"), "Costello": book("Costello", days[2], "Check-up"),
              "Schafer": book("Schafer", days[3], "Annual physical"), "Ladd": book("Ladd", days[4], "Blood pressure follow-up")}

    def when(key: str) -> str:
        entry = booked.get(key)
        return f"{entry[1]:%A %B %d} at {entry[0]}" if entry and entry[1] else "my upcoming appointment"

    def mail(key: str, kind: str, subject: str, text: str):
        info = patients.get(key)
        if not info:
            return
        p = info["data"]
        who = f"{p['first_name']} {p['last_name']}"
        dob = date.fromisoformat(p["date_of_birth"]).strftime("%m/%d/%Y")
        suffix = {"Cornett": "-v2", "Schafer": "-v3"}.get(key, "")  # reworded examples get a new id
        status, out = call(args.api, "POST", "/api/intake/email", token, {
            "from_address": p["email"], "from_name": who, "subject": subject,
            "body": f"{text}\n\nDOB {dob}, phone {p['phone']}.\n\nThank you,\n{who}",
            "message_id": f"<modmed-example-{kind}-{key.lower()}{suffix}@examples.docshealth.local>", "process": True,
        })
        req = (out or {}).get("request") or {}
        appt = req.get("appointment") or {}
        booked_with = f" with {appt.get('provider_name')} on {appt.get('start_at')}" if appt else ""
        print(f"email {kind} from {who}: {status} created={(out or {}).get('created')} "
              f"-> {req.get('status')} ({req.get('intent') or req.get('category')}){booked_with}")

    mail("Peoples", "new", "Appointment request", "I'd like to schedule a visit with Dr. Rao next week, mornings are best.")
    mail("O'hara", "new", "New appointment", "Could I get a check-up appointment with Dr. Patel sometime next week?")
    mail("Aragon", "new", "Appointment needed", "Please book me a follow-up visit with Dr. Rao, any afternoon works.")
    mail("Cornett", "reschedule", "Need to reschedule", f"Something came up at work. Can I reschedule my appointment on {when('Cornett')} to Thursday afternoon instead?")
    mail("Costello", "cancel", "Cancel my appointment", f"Please cancel my appointment on {when('Costello')}.")
    mail("Baxter", "new", "Appointment request", "I would like a visit with Dr. Rao for a skin rash that has lasted a week. Mornings work best.")
    mail("Ross", "new", "Booking a check-up", "Please book me a routine check-up with Dr. Patel sometime next week, any morning.")
    mail("Lucero", "new", "New patient visit", "I am a new patient and would like an appointment with Dr. Rao for a sports physical.")
    mail("Cortez", "new", "Need to be seen urgently", "I have had chest pain and trouble breathing since this morning. Can I get an appointment today?")
    mail("Schafer", "reschedule", "Reschedule my physical", f"Something came up at work. Can I reschedule my appointment on {when('Schafer')} to Thursday afternoon instead?")
    # Asking for a particular doctor: the booking must be with that doctor.
    mail("Chin", "doctor", "Blood pressure check", "I would like an appointment with Dr. Marcus Patel for a blood pressure check, any morning next week.")
    mail("Solis", "doctor", "Visit with Dr. Rao", "Please book me with Dr. Anita Rao for a general check-up, afternoons are best.")
    mail("Castaneda", "doctor", "Appointment with Dr. Patel", "I need to see Dr. Marcus Patel about recurring stomach pain. Mornings please.")
    mail("Sherman", "doctor", "Dr. Torres appointment", "I would like to book a visit with Dr. Elena Torres, any day next week.")
    mail("Hathaway", "doctor", "Follow up with Dr. Rao", "Please schedule a follow-up with Dr. Anita Rao, preferably in the afternoon.")
    mail("Knox", "doctor", "See Dr. Torres", "Can I get an appointment with Dr. Elena Torres sometime next week?")
    mail("Ladd", "cancel", "Please cancel", f"I no longer need my appointment on {when('Ladd')}. Please cancel it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
