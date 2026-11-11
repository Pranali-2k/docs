"""Seed demo patients by running realistic (fake) emails through the real scheduling workflow.

    python -m scripts.demo_scheduling                     # API at http://127.0.0.1:8030
    python -m scripts.demo_scheduling --api http://127.0.0.1:8000

Each scenario goes through the HTTP API as the scheduler, exactly like staff would:
  A. new patient writes in for an exact time         -> AI draft chart -> staff approve -> booked at that time
  B. attorney sends the client's details (form)       -> draft (on behalf) -> approve -> booked, reply to attorney
  C. parent writes for their child                    -> draft -> approve -> booked
  D. new patient forgets the date of birth            -> draft flags it -> staff add DOB -> booked
  E. patient A (now on file) asks for an exact time   -> AI books that exact slot automatically
  F. patient A asks for a time that is already taken  -> AI offers alternatives -> staff book one
  G. patient A cancels their first appointment        -> AI cancels automatically
All people are fictitious (555 phone numbers, example.com emails) and every chart is flagged as a test patient.
Replies are held while outbound email is off. Refuses to run twice unless --force.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Patient, Provider
from app.services import scheduling

PEOPLE = {
    "avery": ("avery.brooks@example.com", "Avery Brooks"),
    "attorney": ("paralegal@lawfirm.example.com", "Taylor Quinn"),
    "jordan": ("jordan.park@example.com", "Jordan Park"),
    "noah": ("noah.kim@example.com", "Noah Kim"),
}


def free_slots(provider_id: int, count: int, skip_days: int = 3) -> list[datetime]:
    """Open slots for one provider on weekdays from `skip_days` out, one per day so emails name different dates."""
    found: list[datetime] = []
    with SessionLocal() as db:
        day = scheduling.clinic_now().date() + timedelta(days=skip_days)
        for _ in range(60):
            if day.weekday() < 5:
                opts = scheduling.available_slots(db, provider_id, day)
                if len(opts) > 2:
                    found.append(opts[len(opts) // 2])  # mid-session, so it's not simply "the first free slot"
                    if len(found) == count:
                        return found
            day += timedelta(days=1)
    raise SystemExit("Not enough open slots in the next 60 days to run the demo")


def phrase(dt: datetime) -> str:
    return f"{dt.month}/{dt.day} at {dt.strftime('%I:%M %p').lstrip('0').lower()}"


class Demo:
    def __init__(self, api: str):
        s = get_settings()
        self.c = httpx.Client(base_url=api.rstrip("/") + "/api", timeout=120)
        tok = self.c.post("/auth/login", json={"email": s.seed_scheduler_email, "password": s.seed_scheduler_password})
        tok.raise_for_status()
        self.c.headers["Authorization"] = f"Bearer {tok.json()['access_token']}"
        self.rows: list[tuple[str, str, str]] = []

    def j(self, r: httpx.Response, ok=(200, 201)):
        if r.status_code not in ok:
            raise RuntimeError(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def email(self, who: str, subject: str, body: str) -> dict:
        addr, name = PEOPLE[who]
        return self.j(self.c.post("/intake/email", json={"from_address": addr, "from_name": name,
                                                         "subject": subject, "body": body}))["request"]

    def get(self, rid: int) -> dict:
        return self.j(self.c.get(f"/requests/{rid}"))

    def approve(self, req: dict, *, slot=None, patient=None, payer=None, member=None) -> dict:
        d = self.get(req["id"])
        draft = d["draft_chart"]
        if not draft or draft["status"] != "pending":
            raise RuntimeError(f"{req['reference']}: expected a pending draft chart, got {d['review_reasons']}")
        slot = slot or draft["proposed"]
        res = self.j(self.c.post(f"/requests/{req['id']}/draft-chart/approve", json={
            "patient": patient or {}, "payer_id": payer, "member_id": member,
            "book": {"provider_id": slot["provider_id"], "start_at": slot["start_at"]} if slot else None}))
        self.j(self.c.patch(f"/patients/{res['patient']['id']}", json={"is_test_patient": True}))
        return res

    def log(self, name: str, req: dict, outcome: str) -> None:
        self.rows.append((name, req["reference"], outcome))
        print(f"[ok] {name}\n     {req['reference']}: {outcome}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8030")
    ap.add_argument("--force", action="store_true", help="run again even if the demo patients exist")
    args = ap.parse_args()

    with SessionLocal() as db:
        existing = db.scalars(select(Patient.mrn).where(Patient.email.in_(
            [PEOPLE["avery"][0], "carmen.reyes@example.com", PEOPLE["jordan"][0], PEOPLE["noah"][0]]))).all()
        rao = db.scalar(select(Provider).where(Provider.full_name.ilike("%Rao%"), Provider.is_active))
        payers = {p.name: p.id for p in db.scalars(select(__import__("app.models", fromlist=["Payer"]).Payer))}
    if existing and not args.force:
        print(f"Demo patients already exist (MRN {', '.join(existing)}). Use --force to add another set.")
        return 0
    if rao is None:
        raise SystemExit("Dr. Rao (seed provider) not found")
    t1, t2, t3, t4 = free_slots(rao.id, 4)
    dob_a = date(1988, 5, 14)
    demo = Demo(args.api)

    # A. new patient, exact time with Dr. Rao, insurance in the email
    a = demo.email("avery", "New patient appointment",
                   f"Hello, I'm a new patient and would like to schedule a new patient visit with Dr. Rao on {phrase(t1)}.\n"
                   f"My date of birth is {dob_a:%m/%d/%Y}, cell 312-555-0181, email avery.brooks@example.com.\n"
                   "I have Aetna, member ID W100200300.\nThanks,\nAvery Brooks")
    draft = demo.get(a["id"])["draft_chart"]
    assert draft["matches_request"] and draft["proposed"]["start_at"] == t1.isoformat(), draft["proposed"]
    res = demo.approve(a, payer=payers.get("Aetna"), member="W100200300")
    demo.log("A. New patient asks for an exact time", a,
             f"draft approved -> chart {res['patient']['mrn']}, booked {res['appointment']['start_at']} (exactly as asked)")

    # B. attorney / case manager sends the client's details as a form
    b = demo.email("attorney", "RE: Carmen Reyes - new client",
                   f"Please schedule it for {phrase(t2)}.\n\nFirst & Last name: Carmen Reyes\nLast four of SSN 4417\n"
                   "DOB 09/23/1974\nGender Female\nPhone number 8015550164\nEmail address carmen.reyes@example.com\n"
                   "Home address\n\n221 N. Sample Ave.\nSalt Lake City, Utah 84101\n\nThank you,\n\nTaylor Quinn\n"
                   "Senior Paralegal\nTelephone: (310) 555-2800 | paralegal@lawfirm.example.com")
    draft = demo.get(b["id"])["draft_chart"]
    p = draft["patient"]
    assert (p["phone"], p["email"]) == ("8015550164", "carmen.reyes@example.com"), p  # client's, not the firm's
    assert not draft["requester"]["is_patient"]
    res = demo.approve(b)
    reply = [e for e in res["emails"] if e["direction"] == "outbound"][0]
    demo.log("B. Attorney sends client details (form)", b,
             f"chart {res['patient']['mrn']} with client's own phone/email/address, booked {res['appointment']['start_at']}; "
             f"confirmation to {reply['to_address']} ({reply['send_status']})")

    # C. parent for a child
    c = demo.email("jordan", "Appointment for my daughter",
                   "Hi, I'd like to book a check-up for my daughter Mia Park next week, mornings are best. "
                   "Her DOB is 02/11/2016. You can reach me at 312-555-0199.\nThanks,\nJordan Park")
    res = demo.approve(c)
    demo.log("C. Parent writes for a child", c, f"chart {res['patient']['mrn']} for Mia Park, booked {res['appointment']['start_at']}")

    # D. date of birth missing: staff add it (e.g. after a phone call), then approve
    d = demo.email("noah", "Need an appointment",
                   "Hello, I'm a new patient and need to book an appointment next week for a cough.\n"
                   "Phone 312-555-0142.\nThanks,\nNoah Kim")
    missing = demo.get(d["id"])["draft_chart"]["missing_required"]
    assert missing == ["date_of_birth"], missing
    res = demo.approve(d, patient={"date_of_birth": "1995-07-30"})
    demo.log("D. New patient without a date of birth", d,
             f"draft flagged missing DOB; staff added it -> chart {res['patient']['mrn']}, booked {res['appointment']['start_at']}")

    # E. now an existing patient: exact time is booked automatically (no staff step)
    e = demo.email("avery", "Follow-up visit",
                   f"Hi, please book a follow-up with Dr. Rao on {phrase(t3)}. DOB {dob_a:%m/%d/%Y}.\nThanks,\nAvery Brooks")
    assert e["status"] == "resolved" and e["appointment"]["start_at"] == t3.isoformat(), (e["status"], e["review_reasons"])
    demo.log("E. Existing patient asks for an exact time", e, f"AI booked {e['appointment']['start_at']} automatically")

    # F. the same time again: taken, so alternatives are offered and staff book one
    f = demo.email("avery", "One more visit",
                   f"Hi, could I also come in with Dr. Rao on {phrase(t3)}? DOB {dob_a:%m/%d/%Y}.\nThanks,\nAvery Brooks")
    codes = {x["code"] for x in f["review_reasons"]}
    assert f["status"] == "needs_review" and "REQUESTED_TIME_UNAVAILABLE" in codes, codes
    alt = demo.j(demo.c.get(f"/requests/{f['id']}/slots", params={"provider_id": rao.id}))["slots"][0]
    booked = demo.j(demo.c.post(f"/requests/{f['id']}/book", json={"provider_id": rao.id, "start_at": alt["start_at"]}))
    demo.log("F. Existing patient asks for a taken time", f,
             f"AI did not double-book; offered alternatives, staff booked {booked['appointment']['start_at']}")

    # G. cancellation of the first appointment
    g = demo.email("avery", "Cancel appointment",
                   f"Hi, please cancel my appointment on {t1.month}/{t1.day}. DOB {dob_a:%m/%d/%Y}.\nThanks,\nAvery Brooks")
    assert g["status"] == "resolved", g["review_reasons"]
    demo.log("G. Existing patient cancels", g, f"AI cancelled the {t1:%b %d} appointment automatically")

    print(f"\n{len(demo.rows)} scenarios completed. Unused open slot kept for reference: {t4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
