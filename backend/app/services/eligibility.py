"""Insurance eligibility verification (X12 270/271) and provider-network validation.

`EligibilityAdapter` stands in for a clearinghouse 270/271 transaction (Availity,
Change Healthcare / Optum, Waystar, ...). A check is submitted with a date of
service and either the rendering provider's individual NPI or the practice's
group NPI. The response is normalized into:

  * coverage status, plan name/type, plan and eligibility dates,
  * the payer's view of the member (name, DOB, member ID),
  * benefit lines (EB segments): co-payment, co-insurance, deductible,
    remaining deductible, out-of-pocket and remaining out-of-pocket, per
    service type, in/out of network and individual/family level,
  * the original X12 271 text,
  * discrepancies between the payer's data and the patient chart.

The mock clearinghouse is deterministic so demos and tests are reproducible:
  member ID starting with "INV" -> coverage inactive
  member ID starting with "ERR" -> payer system error
  member ID starting with "DSC" -> payer has a different DOB / name spelling (discrepancy demo)
  payer unknown to the practice -> not_found
  otherwise                     -> active, with plan tier derived from the member ID
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    EligibilityBenefit,
    EligibilityCheck,
    InsurancePolicy,
    Patient,
    Payer,
    Provider,
    ProviderNetwork,
)
from app.services import audit

log = logging.getLogger("docshealth.eligibility")

CATEGORY_LABELS = {
    "co_payment": "Co-Payment",
    "co_insurance": "Co-Insurance",
    "deductible": "Deductible",
    "remaining_deductible": "Remaining Deductible",
    "out_of_pocket": "Out of Pocket",
    "remaining_out_of_pocket": "Remaining Out of Pocket",
}
# X12 EB01 codes for each category (remaining amounts are EB01 C/G with time qualifier 29)
EB01 = {"co_payment": "B", "co_insurance": "A", "deductible": "C", "remaining_deductible": "C",
        "out_of_pocket": "G", "remaining_out_of_pocket": "G"}

# Which policy fields a benefit category may populate ("Select" on the insurance form).
POLICY_FIELD_FOR_CATEGORY = {
    ("co_payment", "individual"): "copay_amount",
    ("co_insurance", "individual"): "coinsurance_pct",
    ("deductible", "individual"): "deductible",
    ("remaining_deductible", "individual"): "remaining_deductible",
    ("out_of_pocket", "individual"): "out_of_pocket",
    ("remaining_out_of_pocket", "individual"): "remaining_out_of_pocket",
}


class EligibilityError(ValueError):
    """Invalid eligibility request (e.g. individual NPI without a provider)."""


@dataclass
class Benefit:
    network: str
    category: str
    service_type_code: str
    service_type: str
    coverage_level: str = "individual"
    amount: Decimal | None = None
    percent: Decimal | None = None
    time_period: str | None = None
    auth_required: bool | None = None
    message: str | None = None


@dataclass
class EligibilityResponse:
    coverage_status: str  # active | inactive | not_found | error
    plan_name: str | None = None
    plan_type: str | None = None
    copay: str | None = None
    message: str = ""
    raw: dict = field(default_factory=dict)
    member_name: str | None = None
    member_dob: date | None = None
    member_id: str | None = None
    group_number: str | None = None
    plan_start: date | None = None
    plan_end: date | None = None
    eligibility_start: date | None = None
    eligibility_end: date | None = None
    benefits: list[Benefit] = field(default_factory=list)
    x12: str | None = None


class EligibilityAdapter(ABC):
    name = "base"

    @abstractmethod
    def check(
        self, payer: Payer | None, member_id: str, patient: Patient, service_date: date,
        *, npi: str | None = None, provider_name: str | None = None, policy: InsurancePolicy | None = None,
    ) -> EligibilityResponse: ...


# --------------------------------------------------------------------------- mock clearinghouse

_TIERS = [
    # plan type, office copay, specialist copay, urgent, ER, coinsurance in/out, deductible, OOP max
    ("PPO", 25, 50, 75, 250, 20, 40, 1500, 5000),
    ("HMO", 15, 35, 50, 200, 10, 100, 500, 3000),
    ("EPO", 30, 60, 75, 300, 20, 100, 1000, 4000),
]

_OFFICE_LINES = [
    ("PHYSICIAN SERVICES IN OFFICE PRIMARY CARE OFFICE VISIT", "office"),
    ("PHYSICIAN SPECIALIST OFFICE VISIT", "specialist"),
    ("PHYSICIAN BENEFIT PREVENTIVE CARE PREVENTIVE HEALTH CARE SERVICES", "preventive"),
    ("PHYSICIAN BENEFIT DIABETES CARE DIABETIC COUNSELING", "preventive"),
    ("PHYSICIAN SERVICES IN OFFICE VISIT CONSULTATION", "specialist"),
    ("PHYSICIAN SERVICES IN OFFICE SECOND MEDICAL OPINION GENERALIST", "office"),
    ("PHYSICIAN SERVICES IN OFFICE SECOND MEDICAL OPINION SPECIALIST", "specialist"),
    ("TELEHEALTH VIRTUAL VISIT", "office"),
]


def _h(*parts) -> int:
    return int(hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest(), 16)


def _money(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


class MockClearinghouse(EligibilityAdapter):
    name = "mock"

    def check(self, payer, member_id, patient, service_date, *, npi=None, provider_name=None, policy=None):
        trace = hashlib.sha1(f"{member_id}{service_date}{npi}".encode()).hexdigest()[:10].upper()
        raw = {"transaction": "271", "trace_number": trace, "service_date": service_date.isoformat(), "npi": npi}
        mid = (member_id or "").upper()
        if payer is None:
            return EligibilityResponse("not_found", message="AAA*N**72 - Payer not recognized by clearinghouse", raw=raw)
        raw["payer_code"] = payer.payer_code
        if mid.startswith("ERR"):
            return EligibilityResponse("error", message="AAA*Y**42 - Payer system unavailable, retry later", raw=raw)

        plan_type, office, specialist, urgent, er, coins_in, coins_out, ded, oop = _TIERS[_h(mid) % len(_TIERS)]
        plans = sorted(payer.plans, key=lambda p: p.id) if payer.plans else []
        matching = [p for p in plans if p.plan_type == plan_type]
        plan_name = (matching[0].name if matching else f"{payer.name} {plan_type}").upper()
        year_start, year_end = date(service_date.year, 1, 1), date(service_date.year, 12, 31)

        member_name = f"{patient.last_name}, {patient.first_name}".upper()
        member_dob = patient.date_of_birth
        if mid.startswith("DSC"):  # payer holds slightly different demographics
            member_name = f"{patient.last_name}, {patient.first_name[:-1] or patient.first_name}".upper()
            member_dob = (patient.date_of_birth + timedelta(days=1)) if patient.date_of_birth else None

        base = dict(raw=raw, member_name=member_name, member_dob=member_dob, member_id=mid,
                    group_number=(policy.group_number if policy else None), plan_name=plan_name, plan_type=plan_type,
                    plan_start=year_start, plan_end=year_end)
        if mid.startswith("INV"):
            ended = service_date.replace(day=1) - timedelta(days=1)
            resp = EligibilityResponse("inactive", message="EB*6 - Coverage terminated for this member",
                                       eligibility_start=year_start.replace(year=year_start.year - 1),
                                       eligibility_end=ended, **base)
            resp.raw = {**raw, "eb01": "6"}
            resp.x12 = _x12(resp, payer, patient, npi, provider_name, service_date)
            return resp

        used = _h(mid, service_date.year) % 100 / 100  # share of deductible already met this year
        ded_used = _money(ded * used)
        oop_used = _money(min(oop, float(ded_used) + oop * used * 0.3))
        b: list[Benefit] = []

        copay = {"office": office, "specialist": specialist, "preventive": 0}
        for message, kind in _OFFICE_LINES:
            b.append(Benefit("in", "co_payment", "98", "Professional (Physician) Visit - Office",
                             amount=_money(copay[kind]), time_period="visit", auth_required=False, message=message))
        b.append(Benefit("in", "co_payment", "UC", "Urgent Care", amount=_money(urgent), time_period="visit",
                         auth_required=False, message="URGENT CARE FACILITY VISIT"))
        b.append(Benefit("in", "co_payment", "86", "Emergency Services", amount=_money(er), time_period="visit",
                         auth_required=False, message="EMERGENCY ROOM COPAY WAIVED IF ADMITTED"))
        b.append(Benefit("in", "co_insurance", "62", "MRI/CAT Scan", percent=_money(coins_in),
                         auth_required=True, message="ADVANCED IMAGING REQUIRES PRIOR AUTHORIZATION"))
        b.append(Benefit("in", "co_insurance", "30", "Health Benefit Plan Coverage", percent=_money(coins_in),
                         auth_required=False, message="AFTER DEDUCTIBLE"))
        for level, mult in (("individual", 1), ("family", 2)):
            b += [
                Benefit("in", "deductible", "30", "Health Benefit Plan Coverage", level, amount=_money(ded * mult),
                        time_period="calendar_year"),
                Benefit("in", "remaining_deductible", "30", "Health Benefit Plan Coverage", level,
                        amount=_money(ded * mult - float(ded_used) * mult), time_period="remaining"),
                Benefit("in", "out_of_pocket", "30", "Health Benefit Plan Coverage", level, amount=_money(oop * mult),
                        time_period="calendar_year"),
                Benefit("in", "remaining_out_of_pocket", "30", "Health Benefit Plan Coverage", level,
                        amount=_money(oop * mult - float(oop_used) * mult), time_period="remaining"),
            ]
        if plan_type == "PPO":
            b.append(Benefit("out", "co_insurance", "98", "Professional (Physician) Visit - Office",
                             percent=_money(coins_out), message="OUT-OF-NETWORK PHYSICIAN SERVICES AFTER DEDUCTIBLE"))
            b.append(Benefit("out", "co_insurance", "30", "Health Benefit Plan Coverage", percent=_money(coins_out),
                             message="BALANCE BILLING MAY APPLY"))
            for level, mult in (("individual", 1), ("family", 2)):
                b += [
                    Benefit("out", "deductible", "30", "Health Benefit Plan Coverage", level,
                            amount=_money(ded * 2 * mult), time_period="calendar_year"),
                    Benefit("out", "out_of_pocket", "30", "Health Benefit Plan Coverage", level,
                            amount=_money(oop * 2 * mult), time_period="calendar_year"),
                ]
        else:
            b.append(Benefit("out", "co_insurance", "30", "Health Benefit Plan Coverage", percent=_money(100),
                             message=f"{plan_type}: OUT-OF-NETWORK SERVICES NOT COVERED EXCEPT EMERGENCIES"))

        resp = EligibilityResponse(
            "active", copay=f"${office}", message="EB*1 - Active coverage", eligibility_start=year_start,
            eligibility_end=None, benefits=b, **base,
        )
        resp.raw = {**raw, "eb01": "1", "plan": plan_type}
        resp.x12 = _x12(resp, payer, patient, npi, provider_name, service_date)
        return resp


def _x12(resp: EligibilityResponse, payer: Payer, patient: Patient, npi, provider_name, service_date: date) -> str:
    """Render the response as an X12 005010X279A1 271 transaction (the 'Original Response')."""
    d8 = lambda d: d.strftime("%Y%m%d") if d else ""  # noqa: E731
    last, _, first = (resp.member_name or "").partition(", ")
    prov_last = (provider_name or get_settings().practice_name).upper().replace("DR. ", "")
    segs = [
        f"ISA*00*          *00*          *ZZ*{payer.electronic_payer_id or payer.payer_code:<15}*ZZ*DOCSHEALTH     "
        f"*{datetime.now():%y%m%d}*{datetime.now():%H%M}*^*00501*{resp.raw.get('trace_number', '0')[:9]:0>9}*0*P*:",
        "GS*HB*PAYER*DOCSHEALTH*" + datetime.now().strftime("%Y%m%d*%H%M") + "*1*X*005010X279A1",
        "ST*271*0001*005010X279A1",
        f"BHT*0022*11*{resp.raw.get('trace_number', '')}*{datetime.now():%Y%m%d*%H%M}",
        "HL*1**20*1",
        f"NM1*PR*2*{payer.name.upper()}*****PI*{payer.electronic_payer_id or payer.payer_code}",
        "HL*2*1*21*1",
        f"NM1*1P*1*{prov_last}*****XX*{npi or ''}",
        "HL*3*2*22*0",
        f"TRN*2*{resp.raw.get('trace_number', '')}*9DOCSHEALTH",
        f"NM1*IL*1*{last}*{first}****MI*{resp.member_id or ''}",
        f"DMG*D8*{d8(resp.member_dob)}*{(patient.sex or 'U')[:1]}",
    ]
    if resp.plan_start:
        segs.append(f"DTP*291*RD8*{d8(resp.plan_start)}-{d8(resp.plan_end)}")
    if resp.coverage_status == "inactive":
        segs.append(f"EB*6**30**{resp.plan_name or ''}")
    else:
        segs.append(f"EB*1**30*{resp.plan_type or ''}*{resp.plan_name or ''}")
    for b in resp.benefits:
        level = "IND" if b.coverage_level == "individual" else "FAM"
        period = {"visit": "27", "calendar_year": "23", "remaining": "29"}.get(b.time_period or "", "")
        amt = f"{b.amount}" if b.amount is not None else ""
        pct = f"{(b.percent or 0) / 100:.2f}" if b.percent is not None else ""
        net = "Y" if b.network == "in" else "N"
        auth = "Y" if b.auth_required else ("N" if b.auth_required is False else "")
        segs.append(f"EB*{EB01[b.category]}*{level}*{b.service_type_code}**{period}*{amt}*{pct}****{auth}*{net}")
        if b.message:
            segs.append(f"MSG*{b.message}")
    segs.append(f"DTP*472*D8*{d8(service_date)}")
    segs.append(f"SE*{len(segs) - 1}*0001")
    segs += ["GE*1*1", "IEA*1*000000001"]
    return "~\n".join(segs) + "~"


class X12ClearinghouseAdapter(EligibilityAdapter):  # pragma: no cover - integration point
    """Placeholder for a real 270/271 integration; configure credentials per vendor."""

    name = "x12"

    def check(self, payer, member_id, patient, service_date, *, npi=None, provider_name=None, policy=None):
        raise NotImplementedError("Wire your clearinghouse API here (ELIGIBILITY_ADAPTER=x12)")


def get_adapter() -> EligibilityAdapter:
    return {"mock": MockClearinghouse, "x12": X12ClearinghouseAdapter}.get(
        get_settings().eligibility_adapter, MockClearinghouse
    )()


# --------------------------------------------------------------------------- network & discrepancies

def is_in_network(db: Session, provider_id: int | None, payer_id: int | None) -> bool | None:
    if provider_id is None or payer_id is None:
        return None
    row = db.scalar(
        select(ProviderNetwork).where(ProviderNetwork.provider_id == provider_id, ProviderNetwork.payer_id == payer_id)
    )
    return bool(row and row.in_network)


def find_discrepancies(resp: EligibilityResponse, patient: Patient, policy: InsurancePolicy | None,
                       service_date: date) -> list[dict]:
    """Compare what the payer returned against the chart and the policy on file."""
    out: list[dict] = []

    def add(field_name: str, chart, payer_value, severity: str = "warning", note: str = ""):
        out.append({"field": field_name, "chart": chart, "payer": payer_value, "severity": severity, "note": note})

    if resp.coverage_status not in {"active", "inactive"}:
        return out
    if resp.member_dob and patient.date_of_birth and resp.member_dob != patient.date_of_birth:
        add("Date of birth", patient.date_of_birth.isoformat(), resp.member_dob.isoformat(), "error",
            "Claims will deny on a DOB mismatch. Verify with the patient's ID.")
    if resp.member_name:
        last, _, first = resp.member_name.partition(", ")
        if last.strip().lower() != patient.last_name.lower() or first.strip().lower() != patient.first_name.lower():
            add("Patient name", f"{patient.last_name}, {patient.first_name}".upper(), resp.member_name, "warning",
                "Name on the insurance card may differ from the chart.")
    if policy is not None:
        if resp.member_id and policy.member_id and resp.member_id.upper() != policy.member_id.upper():
            add("Policy number", policy.member_id, resp.member_id, "error")
        if resp.plan_type and policy.policy_type and resp.plan_type != policy.policy_type:
            add("Policy type", policy.policy_type, resp.plan_type, "warning",
                f"Payer reports a {resp.plan_type} plan; update the policy type on file.")
        if resp.plan_name and policy.plan_name and resp.plan_name.lower() != policy.plan_name.lower():
            add("Plan name", policy.plan_name, resp.plan_name, "info")
        if policy.end_date and policy.end_date < service_date:
            add("Policy end date", policy.end_date.isoformat(), "active" if resp.coverage_status == "active" else "",
                "warning", "Policy end date on file is before the date of service.")
        if resp.coverage_status == "inactive" and (policy.eligibility_status or "active") == "active":
            add("Eligibility status", policy.eligibility_status or "active", "inactive", "error",
                "Payer reports this coverage is no longer active.")
    return out


# --------------------------------------------------------------------------- run

def run_eligibility(
    db: Session,
    patient: Patient,
    policy: InsurancePolicy | None,
    provider: Provider | None,
    *,
    request_id: int | None = None,
    actor: str = "system",
    service_date: date | None = None,
    npi_type: str = "individual",
) -> EligibilityCheck:
    """Submit a 270, store the normalized 271 and its benefits. Adapter failures are
    recorded as coverage_status='error' (a manual-review trigger), never raised."""
    s = get_settings()
    service_date = service_date or datetime.now(timezone.utc).date()
    if npi_type not in {"individual", "group"}:
        raise EligibilityError("npi_type must be 'individual' or 'group'")
    if npi_type == "individual" and provider is None and request_id is None and policy is not None:
        raise EligibilityError("Choose the rendering provider to check by individual NPI")
    npi = provider.npi if (npi_type == "individual" and provider) else s.practice_group_npi
    adapter = get_adapter()

    if policy is None:
        resp = EligibilityResponse("not_found", message="No active insurance policy on the patient chart")
    else:
        try:
            resp = adapter.check(policy.payer, policy.member_id, patient, service_date, npi=npi,
                                 provider_name=provider.full_name if provider else None, policy=policy)
        except Exception as exc:  # adapter/network failure is a review trigger, not a crash
            log.warning("eligibility adapter %s failed for patient %s: %s", adapter.name, patient.id, exc)
            resp = EligibilityResponse("error", message=f"Eligibility adapter error: {exc}")

    in_network = is_in_network(db, provider.id if provider else None, policy.payer_id if policy else None)
    try:
        discrepancies = find_discrepancies(resp, patient, policy, service_date)
    except Exception:  # never lose the eligibility result because a comparison failed
        log.exception("discrepancy check failed for patient %s", patient.id)
        discrepancies = []

    check = EligibilityCheck(
        patient_id=patient.id,
        policy_id=policy.id if policy else None,
        provider_id=provider.id if provider else None,
        request_id=request_id,
        coverage_status=resp.coverage_status,
        in_network=in_network,
        plan_name=resp.plan_name,
        copay=resp.copay,
        message=resp.message,
        raw_response={**resp.raw, "adapter": adapter.name},
        checked_by=actor,
        date_of_service=service_date,
        npi_type=npi_type,
        npi=npi,
        payer_member_name=resp.member_name,
        payer_member_dob=resp.member_dob,
        payer_member_id=resp.member_id,
        plan_type=resp.plan_type,
        plan_start=resp.plan_start,
        plan_end=resp.plan_end,
        eligibility_start=resp.eligibility_start,
        eligibility_end=resp.eligibility_end,
        raw_x12=resp.x12,
        discrepancies=discrepancies,
    )
    check.benefits = [
        EligibilityBenefit(
            network=b.network, category=b.category, coverage_level=b.coverage_level,
            service_type_code=b.service_type_code, service_type=b.service_type, amount=b.amount, percent=b.percent,
            time_period=b.time_period, auth_required=b.auth_required, message=b.message,
        )
        for b in resp.benefits
    ]
    db.add(check)
    db.flush()

    if policy is not None:
        policy.last_eligibility_check_id = check.id
        if resp.coverage_status in {"active", "inactive"}:
            policy.eligibility_status = resp.coverage_status
    try:
        from app.services.benefits_summary import summarize

        check.ai_summary = summarize(check, patient)
    except Exception:  # the summary is a convenience; the check itself already succeeded
        log.exception("benefit summary failed for check %s", check.id)

    audit.record(
        db, "patient", patient.id, "eligibility_checked", actor,
        coverage_status=resp.coverage_status, in_network=in_network, npi_type=npi_type, npi=npi,
        provider_id=provider.id if provider else None, policy_id=policy.id if policy else None,
        date_of_service=service_date.isoformat(), discrepancies=len(discrepancies), request_id=request_id,
    )
    return check


def apply_benefit_to_policy(db: Session, policy: InsurancePolicy, benefit: EligibilityBenefit, *, actor: str) -> str:
    """'Select' a value from an eligibility response into the policy's eligibility fields."""
    field_name = POLICY_FIELD_FOR_CATEGORY.get((benefit.category, benefit.coverage_level))
    if field_name is None:
        raise EligibilityError(f"{CATEGORY_LABELS.get(benefit.category, benefit.category)} "
                               f"({benefit.coverage_level}) can't be copied to the policy")
    check = db.get(EligibilityCheck, benefit.check_id)
    if check is None or check.policy_id != policy.id:
        raise EligibilityError("That benefit belongs to a different policy's eligibility response")
    value = benefit.percent if benefit.category == "co_insurance" else benefit.amount
    old = getattr(policy, field_name)
    setattr(policy, field_name, value)
    audit.record(db, "policy", policy.id, "eligibility_value_selected", actor, field=field_name,
                 old=str(old) if old is not None else None, new=str(value), benefit_id=benefit.id,
                 check_id=benefit.check_id)
    audit.record(db, "patient", policy.patient_id, "insurance_updated", actor, policy_id=policy.id,
                 changes={field_name: {"from": str(old), "to": str(value)}})
    return field_name
