"""Check the configured LLM providers and compare their extraction accuracy with the rules engine.

    python -m scripts.check_llm                              # every provider that has a key, plus the rules engine
    python -m scripts.check_llm --providers azure_openai     # just one

Only SYNTHETIC emails (fake names, 555 numbers, example.com) are sent, so no patient data leaves the machine
while testing. Keys are never printed. For each provider:
  1. configuration (key present, endpoint not a placeholder),
  2. connectivity (one tiny JSON request; 401/403/404/429/timeouts are explained),
  3. field accuracy on cases that include the hard ones: law-firm emails where the signature phone/email
     belong to the firm, a parent writing for a child, exact times and cancellations.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.llm.providers import LLMError, get_llm
from app.models import Payer, PayerPlan, Provider
from app.services.extraction import ExtractionContext, extract_request

REF = date(2026, 10, 8)

CASES = [
    {"name": "new patient, exact time, insurance",
     "sender": ("avery.brooks@example.com", "Avery Brooks"), "subject": "New patient appointment",
     "body": "Hello, I'm a new patient and would like to schedule a new patient visit with Dr. Rao on 10/19 at 1:30 pm.\n"
             "My date of birth is 05/14/1988, cell 312-555-0181, email avery.brooks@example.com.\n"
             "I have Aetna, member ID W100200300.\nThanks,\nAvery Brooks",
     "expect": {"intent": "new_appointment", "first_name": "Avery", "last_name": "Brooks", "date_of_birth": "1988-05-14",
                "phone": "3125550181", "email": "avery.brooks@example.com", "preferred_date": "2026-10-19",
                "preferred_time": "13:30", "requester_is_patient": True, "member_id": "W100200300"}},
    {"name": "attorney form (client details)",
     "sender": ("paralegal@lawfirm.example.com", "Taylor Quinn"), "subject": "RE: Carmen Reyes - new client",
     "body": "Please schedule it for 10/20 at 10 am.\n\nFirst & Last name: Carmen Reyes\nLast four of SSN 4417\n"
             "DOB 09/23/1974\nGender Female\nPhone number 8015550164\nEmail address carmen.reyes@example.com\n"
             "Home address\n\n221 N. Sample Ave.\nSalt Lake City, Utah 84101\n\nThank you,\n\nTaylor Quinn\n"
             "Senior Paralegal\nTelephone: (310) 555-2800 | paralegal@lawfirm.example.com",
     "expect": {"intent": "new_appointment", "first_name": "Carmen", "last_name": "Reyes", "date_of_birth": "1974-09-23",
                "phone": "8015550164", "email": "carmen.reyes@example.com", "sex": "female", "ssn_last4": "4417",
                "postal_code": "84101", "state": "UT", "preferred_date": "2026-10-20", "preferred_time": "10:00",
                "requester_is_patient": False}},
    {"name": "attorney, no form: firm signature only",
     "sender": ("intake@injurylaw.example.com", "Morgan Ellis"), "subject": "RE: 55501 - LANE, ROBERT",
     "body": "Good afternoon,\n\nOur client Robert Lane needs an initial evaluation. Next Monday morning works best. "
             "His date of birth is 03/02/1970 and his cell is 801-555-0177.\n\nPlease confirm.\n\nMorgan Ellis\n"
             "Case Manager | Injury Law Group\nPhone: (310) 555-2900 | Fax: (310) 555-2901 | intake@injurylaw.example.com",
     "expect": {"intent": "new_appointment", "first_name": "Robert", "last_name": "Lane", "date_of_birth": "1970-03-02",
                "phone": "8015550177", "email": None, "requester_is_patient": False}},
    {"name": "parent for a child",
     "sender": ("jordan.park@example.com", "Jordan Park"), "subject": "Appointment for my daughter",
     "body": "Hi, I'd like to book a check-up for my daughter Mia Park next week, mornings are best. "
             "Her DOB is 02/11/2016. You can reach me at 312-555-0199.\nThanks,\nJordan Park",
     "expect": {"intent": "new_appointment", "first_name": "Mia", "last_name": "Park", "date_of_birth": "2016-02-11",
                "requester_is_patient": False, "time_of_day": "morning"}},
    {"name": "missing date of birth",
     "sender": ("noah.kim@example.com", "Noah Kim"), "subject": "Need an appointment",
     "body": "Hello, I'm a new patient and need to book an appointment next week for a cough.\nPhone 312-555-0142.\n"
             "Thanks,\nNoah Kim",
     "expect": {"intent": "new_appointment", "first_name": "Noah", "last_name": "Kim", "date_of_birth": None,
                "phone": "3125550142", "email": "noah.kim@example.com"}},
    {"name": "cancellation",
     "sender": ("avery.brooks@example.com", "Avery Brooks"), "subject": "Cancel appointment",
     "body": "Hi, please cancel my appointment on 10/12. DOB 05/14/1988.\nThanks,\nAvery Brooks",
     "expect": {"intent": "cancel", "current_appointment_date": "2026-10-12", "date_of_birth": "1988-05-14"}},
    {"name": "reschedule",
     "sender": ("carmen.reyes@example.com", "Carmen Reyes"), "subject": "Change my appointment",
     "body": "Hello, I need to reschedule my appointment on October 13 to the following Friday afternoon. "
             "DOB 9/23/1974.\nCarmen Reyes",
     "expect": {"intent": "reschedule", "current_appointment_date": "2026-10-13", "time_of_day": "afternoon"}},
]


def actual(x, field):
    p, a, i = x.patient, x.appointment, x.insurance
    return {"intent": x.intent, "preferred_date": a.preferred_dates[0] if a.preferred_dates else None,
            "preferred_time": a.preferred_time, "time_of_day": a.preferred_time_of_day,
            "current_appointment_date": a.current_appointment_date, "member_id": i.member_id,
            }.get(field, getattr(p, field, None))


def context(sender):
    with SessionLocal() as db:
        payers = [(p.name, [pl.name for pl in db.scalars(select(PayerPlan).where(PayerPlan.payer_id == p.id))])
                  for p in db.scalars(select(Payer))]
        providers = [p.full_name for p in db.scalars(select(Provider).where(Provider.is_active))]
    return ExtractionContext(reference_date=REF, sender_email=sender[0], sender_name=sender[1], payers=payers,
                             providers=providers)


def config_problem(name, s) -> str | None:
    need = {"azure_openai": [("azure_openai_api_key", "AZURE_OPENAI_API_KEY"), ("azure_openai_endpoint", "AZURE_OPENAI_ENDPOINT"),
                             ("azure_openai_deployment", "AZURE_OPENAI_DEPLOYMENT")],
            "huggingface": [("huggingface_api_key", "HUGGINGFACE_API_KEY")],
            "openai": [("openai_api_key", "OPENAI_API_KEY")], "gemini": [("gemini_api_key", "GEMINI_API_KEY")]}.get(name, [])
    for attr, env in need:
        v = getattr(s, attr, "") or ""
        if not v:
            return f"{env} is empty in backend/.env"
        if "<" in v:
            return f"{env} is still the placeholder ({v})"
    return None


def explain(exc: Exception) -> str:
    msg = str(exc)
    cause = exc.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        code = cause.response.status_code
        hint = {401: "key rejected (wrong or expired key, or key for another resource)",
                402: "account has no credits left (buy credits or a subscription with the provider)",
                403: "access denied (key lacks permission, or network/firewall rules on the resource)",
                404: "not found: check the endpoint URL and the deployment/model name",
                429: "rate limited or out of quota", 400: "request rejected (model may not support this API version)"}
        return f"HTTP {code}: {hint.get(code, cause.response.reason_phrase)}"
    if isinstance(cause, httpx.TimeoutException):
        return "timed out (endpoint unreachable or very slow)"
    if isinstance(cause, httpx.ConnectError):
        return "could not connect (wrong endpoint host, or no network access)"
    return msg[:200]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", help="comma-separated, e.g. azure_openai,huggingface")
    args = ap.parse_args()
    s = get_settings()
    names = args.providers.split(",") if args.providers else [n for n in ("azure_openai", "openai", "gemini", "huggingface") if not config_problem(n, s)]
    print(f"LLM_PROVIDER in .env: {s.llm_provider}   priority: {s.llm_provider_priority or '(single provider)'}")
    print(f"Approved for patient data: {s.llm_phi_allowed_providers or '(none)'}")
    from app.llm.providers import FallbackLLM

    chain = FallbackLLM(s, phi=True)
    for c in chain.describe()["chain"]:
        print(f"   {c['provider']:<13} {c['model']:<40} {'key set' if c['configured'] else 'no key':<8} "
              f"{'patient data OK' if c['phi_allowed'] else 'test data only'}")
    print(f"=> real patient emails use: {'rules engine' if chain.is_offline else chain.model}\n")
    results, usable = {}, []
    for name in ["mock", *names]:
        llm = get_llm(name, s)
        label = "rules engine" if name == "mock" else f"{name} ({llm.model})"
        if name != "mock":
            problem = config_problem(name, s)
            if problem:
                print(f"[--] {label}: not configured: {problem}")
                continue
            t0 = time.perf_counter()
            try:
                out = llm.complete_json('Reply with the JSON object {"ok": true} and nothing else.', "ping")
                print(f"[ok] {label}: reachable ({(time.perf_counter() - t0) * 1000:.0f} ms), replied {out}")
            except LLMError as exc:
                print(f"[!!] {label}: {explain(exc)}")
                continue
        score, total, misses = 0, 0, []
        t0 = time.perf_counter()
        for case in CASES:
            x = extract_request(llm, case["subject"], case["body"], context(case["sender"]))
            if name != "mock" and x.engine_error:
                misses.append(f"{case['name']}: engine error, fell back to rules ({x.engine_error[:80]})")
            for field, want in case["expect"].items():
                total += 1
                got = actual(x, field)
                if got == want:
                    score += 1
                else:
                    misses.append(f"{case['name']}: {field} = {got!r}, expected {want!r}")
        secs = time.perf_counter() - t0
        results[label] = (score, total, secs, misses)
        if name != "mock":
            usable.append(name)

    print("\nExtraction accuracy on synthetic test emails")
    for label, (score, total, secs, misses) in results.items():
        print(f"\n  {label}: {score}/{total} fields correct ({score / total:.0%}), {secs:.1f}s for {len(CASES)} emails")
        for m in misses:
            print(f"     - {m}")
    if not usable:
        print("\nNo LLM provider is usable yet; the app keeps using the rules engine.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
