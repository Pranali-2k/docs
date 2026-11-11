"""Reference and demo data: staff, payers, providers, network contracts, schedules, fictional patients."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Appointment,
    InsurancePolicy,
    Patient,
    Payer,
    PayerPlan,
    Provider,
    ProviderAvailability,
    ProviderNetwork,
    User,
)
from app.security import hash_password
from app.services.scheduling import clinic_now

PAYERS = [
    # name, internal code, aliases seen in emails
    ("Blue Cross Blue Shield", "BCBS01", ["BCBS", "Blue Cross", "Anthem"]),
    ("Aetna", "AETNA1", []),
    ("UnitedHealthcare", "UHC001", ["United Healthcare", "UHC", "United Health"]),
    ("Cigna", "CIGNA1", []),
    ("Humana", "HUMANA", []),
    ("Medicare", "MCARE1", ["Medicare Part B"]),
    ("Blue Shield of California", "BSCA01", ["Blue Shield of CA", "Blue Shield CA"]),
]

# Payer details for the insurance form. Electronic payer IDs and addresses are illustrative
# demo values; load your clearinghouse's payer list for production.
PAYER_DETAILS = {
    "BCBS01": ("00621", "Commercial", ["PO Box 805107, Chicago, IL 60680"],
               [("BCBS Blue Choice PPO", "PPO"), ("BCBS Blue Precision HMO", "HMO"), ("BCBS Blue Focus EPO", "EPO")]),
    "AETNA1": ("60054", "Commercial", ["PO Box 981106, El Paso, TX 79998"],
               [("Aetna Choice POS II", "PPO"), ("Aetna HMO", "HMO"), ("Aetna Select EPO", "EPO")]),
    "UHC001": ("87726", "Commercial", ["PO Box 740800, Atlanta, GA 30374"],
               [("UnitedHealthcare Choice Plus PPO", "PPO"), ("UnitedHealthcare Navigate HMO", "HMO"),
                ("UnitedHealthcare Core EPO", "EPO")]),
    "CIGNA1": ("62308", "Commercial", ["PO Box 188061, Chattanooga, TN 37422"],
               [("Cigna Open Access Plus PPO", "PPO"), ("Cigna HMO", "HMO"), ("Cigna LocalPlus EPO", "EPO")]),
    "HUMANA": ("61101", "Commercial", ["PO Box 14601, Lexington, KY 40512"],
               [("Humana ChoiceCare PPO", "PPO"), ("Humana Gold Plus HMO", "HMO"), ("Humana National EPO", "EPO")]),
    "MCARE1": ("06102", "Medicare", ["Medicare Part B, PO Box 6475, Indianapolis, IN 46206"],
               [("Original Medicare Part B", "PPO"), ("Medicare Advantage HMO", "HMO"), ("Medicare Select EPO", "EPO")]),
    "BSCA01": ("94036", "Commercial", ["PO Box 272540, Chico, CA 95927"],
               [("Blue Shield Full PPO", "PPO"), ("Blue Shield Access+ HMO", "HMO"),
                ("Blue Shield Minimum Coverage EPO", "EPO")]),
}

PROVIDERS = [
    # name, specialty, npi, in-network payer codes, weekdays
    ("Dr. Anita Rao", "Family Medicine", "1003000001", ["BCBS01", "AETNA1", "UHC001", "CIGNA1", "MCARE1"], [0, 1, 2, 3, 4]),
    ("Dr. Marcus Patel", "Internal Medicine", "1003000002", ["BCBS01", "AETNA1", "MCARE1"], [0, 1, 2, 3, 4]),
    ("Dr. Elena Torres", "Pediatrics", "1003000003", ["BCBS01", "AETNA1", "UHC001", "CIGNA1", "HUMANA"], [0, 2, 4]),
]

PATIENTS = [
    # first, last, dob, sex, phone, email, payer code, member id, preferred provider npi
    ("Maria", "Gonzalez", date(1985, 3, 14), "F", "3125550142", "maria.gonzalez@example.com", "BCBS01", "BCB123456789", "1003000001"),
    ("James", "Carter", date(1972, 11, 2), "M", "3125550178", "james.carter@example.com", "AETNA1", "AET555001234", "1003000001"),
    ("Priya", "Shah", date(1990, 7, 21), "F", "3125550131", "priya.shah@example.com", "UHC001", "UHC900112233", "1003000001"),
    ("Robert", "Kim", date(1958, 1, 30), "M", "3125550165", "robert.kim@example.com", "MCARE1", "1EG4TE5MK72", "1003000002"),
    ("Linda", "Brooks", date(1966, 5, 9), "F", "3125550120", "linda.brooks@example.com", "CIGNA1", "INV77001234", "1003000001"),
    ("Daniel", "Lee", date(1995, 9, 15), "M", "3125550110", "daniel.lee@example.com", "HUMANA", "HUM44556677", None),
    ("Ethan", "Walker", date(2015, 6, 1), "M", "3125550150", "walker.family@example.com", "BCBS01", "BCB880011001", "1003000003"),
    ("Evan", "Walker", date(2015, 6, 1), "M", "3125550150", "walker.family@example.com", "BCBS01", "BCB880011002", "1003000003"),
    ("Grace", "Okafor", date(1979, 12, 5), "F", "3125550188", "grace.okafor@example.com", "BCBS01", "BCB300400500", "1003000002"),
    ("Thomas", "Nguyen", date(1988, 4, 18), "M", "3125550191", "tom.nguyen@example.com", "UHC001", "UHC100200300", "1003000001"),
]

# Fictional patients for the eligibility-discrepancy demo (added to existing databases too).
DISCREPANCY_PATIENTS = [
    # first, last, dob, sex, phone, email, payer code, member id, policy type on file, plan on file
    ("Avery", "Morgan", date(1994, 8, 12), "F", "3125550203", "avery.morgan@example.com", "BSCA01",
     "BSC870022400", "PPO", "Blue Shield Full PPO"),     # payer reports EPO -> policy type discrepancy
    ("Jordan", "Ellis", date(1987, 2, 23), "M", "3125550207", "jordan.ellis@example.com", "BCBS01",
     "DSC55001000", "PPO", "BCBS Blue Choice PPO"),      # payer has a different DOB / name spelling
]


def ensure_reference_data(db: Session) -> None:
    """Idempotent upsert of payers, payer details and plans (safe on an existing database)."""
    existing = {p.payer_code: p for p in db.scalars(select(Payer))}
    for name, code, aliases in PAYERS:
        payer = existing.get(code) or db.scalar(select(Payer).where(Payer.name == name))
        if payer is None:
            payer = Payer(name=name, payer_code=code, aliases=aliases)
            db.add(payer)
            db.flush()
        epid, category, addresses, plans = PAYER_DETAILS[code]
        payer.electronic_payer_id = payer.electronic_payer_id or epid
        payer.financial_category = payer.financial_category or category
        payer.claims_addresses = payer.claims_addresses or addresses
        have = {pl.name for pl in payer.plans}
        for plan_name, plan_type in plans:
            if plan_name not in have:
                payer.plans.append(PayerPlan(name=plan_name, plan_type=plan_type))
    db.flush()


def ensure_demo_enrichment(db: Session) -> None:
    """Add the eligibility-discrepancy demo patients and network contracts if missing."""
    payers = {p.payer_code: p for p in db.scalars(select(Payer))}
    providers = {p.npi: p for p in db.scalars(select(Provider))}
    bsca = payers.get("BSCA01")
    if bsca is not None:
        for npi in ("1003000001", "1003000002"):
            prov = providers.get(npi)
            if prov and not any(n.payer_id == bsca.id for n in prov.networks):
                prov.networks.append(ProviderNetwork(payer_id=bsca.id, in_network=True))
    for i, (first, last, dob, sex, phone, email, code, member, ptype, plan) in enumerate(DISCREPANCY_PATIENTS, 1):
        if db.scalar(select(Patient.id).where(Patient.email == email)):
            continue
        mrn = f"DH{200000 + i}"
        while db.scalar(select(Patient.id).where(Patient.mrn == mrn)):
            mrn = f"DH{int(mrn[2:]) + 100}"
        p = Patient(mrn=mrn, first_name=first, last_name=last, date_of_birth=dob, sex=sex, phone=phone,
                    email=email, city="Chicago", state="IL", marital_status="single", is_test_patient=False,
                    preferred_provider_id=providers["1003000001"].id if "1003000001" in providers else None)
        p.policies = [InsurancePolicy(payer_id=payers[code].id, member_id=member, subscriber_name=f"{first} {last}",
                                      coverage_order="primary", is_primary=True, is_active=True, policy_type=ptype,
                                      plan_name=plan, requires_referral=False, requires_precert_inpatient=True,
                                      requires_preauth_outpatient=False)]
        db.add(p)
    db.flush()


def _next_weekday(start: date, weekday: int, min_days: int) -> date:
    d = start + timedelta(days=min_days)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def seed(db: Session, *, demo: bool = True) -> None:
    s = get_settings()
    if db.scalar(select(User.id).limit(1)) is None:
        db.add_all([
            User(email=s.seed_admin_email.lower(), full_name="Clinic Administrator", role="admin",
                 password_hash=hash_password(s.seed_admin_password)),
            User(email=s.seed_scheduler_email.lower(), full_name="Scheduling Staff", role="scheduler",
                 password_hash=hash_password(s.seed_scheduler_password)),
        ])
    ensure_reference_data(db)
    payers = {p.payer_code: p for p in db.scalars(select(Payer))}
    if db.scalar(select(Provider.id).limit(1)) is None:
        for name, specialty, npi, codes, weekdays in PROVIDERS:
            prov = Provider(full_name=name, specialty=specialty, npi=npi, location="Docs Health Main Clinic, Suite 200")
            prov.networks = [ProviderNetwork(payer_id=payers[c].id, in_network=True) for c in codes]
            prov.availability = [
                ProviderAvailability(weekday=w, start_time=st, end_time=et, slot_minutes=30)
                for w in weekdays for st, et in ((time(9), time(12)), (time(13), time(17)))
            ]
            db.add(prov)
        db.flush()
    if not demo:
        db.commit()
        return
    if db.scalar(select(Patient.id).limit(1)) is not None:
        ensure_demo_enrichment(db)
        db.commit()
        return

    providers = {p.npi: p for p in db.scalars(select(Provider))}
    for i, (first, last, dob, sex, phone, email, code, member, npi) in enumerate(PATIENTS, start=1):
        p = Patient(mrn=f"DH{100000 + i}", first_name=first, last_name=last, date_of_birth=dob, sex=sex,
                    phone=phone, email=email, city="Chicago", state="IL",
                    preferred_provider_id=providers[npi].id if npi else None)
        plan = next((pl for pl in payers[code].plans if pl.plan_type == "PPO"), None)
        p.policies = [InsurancePolicy(payer_id=payers[code].id, member_id=member, subscriber_name=f"{first} {last}",
                                      group_number=f"G{1000 + i}", is_primary=True, is_active=True,
                                      coverage_order="primary", plan_name=plan.name if plan else None,
                                      policy_type="PPO", requires_referral=False, requires_precert_inpatient=True,
                                      requires_preauth_outpatient=False)]
        db.add(p)
    db.flush()
    ensure_demo_enrichment(db)

    today = clinic_now().date()
    by_email = {p.email + p.first_name: p for p in db.scalars(select(Patient))}
    rao, patel = providers["1003000001"], providers["1003000002"]
    booked = [
        (by_email["james.carter@example.comJames"], rao, datetime.combine(_next_weekday(today, 4, 3), time(10))),
        (by_email["priya.shah@example.comPriya"], rao, datetime.combine(_next_weekday(today, 2, 3), time(14))),
        (by_email["grace.okafor@example.comGrace"], patel, datetime.combine(_next_weekday(today, 1, 1), time(9, 30))),
        (by_email["tom.nguyen@example.comThomas"], rao, datetime.combine(_next_weekday(today, 0, 1), time(11))),
    ]
    for patient, prov, start in booked:
        db.add(Appointment(patient_id=patient.id, provider_id=prov.id, start_at=start,
                           end_at=start + timedelta(minutes=30), visit_reason="Follow-up visit", booked_by="seed"))
    db.commit()
