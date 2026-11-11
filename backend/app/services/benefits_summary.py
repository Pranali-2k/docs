"""Plain-language eligibility summary for front-desk staff.

Facts come only from the stored 271 benefits. With LLM_PROVIDER=mock (or on any
model failure) a deterministic template is used. With a remote model
(Azure OpenAI / OpenAI / Gemini / Hugging Face) the model rewrites the facts, and
the rewrite is accepted only if every money/percent figure it mentions appears in
the facts. A model can therefore never invent a copay or a deductible.
"""
from __future__ import annotations

import logging
import re

from app.llm.providers import LLMError, get_llm
from app.models import EligibilityCheck, Patient

log = logging.getLogger("docshealth.benefits")


def _fmt_money(v) -> str:
    return f"${float(v):,.2f}".replace(".00", "")


def _facts(check: EligibilityCheck) -> list[str]:
    if check.coverage_status != "active":
        status = {"inactive": "Coverage is INACTIVE", "not_found": "Member/payer not found",
                  "error": "Payer could not be reached"}.get(check.coverage_status, check.coverage_status)
        ended = f" (ended {check.eligibility_end:%b %d, %Y})" if check.eligibility_end else ""
        return [f"{status}{ended}.", check.message or ""]

    facts = [f"Coverage active: {check.plan_name or 'plan'} ({check.plan_type or 'type unknown'})."]
    if check.in_network is True:
        facts.append("The selected provider is in network.")
    elif check.in_network is False:
        facts.append("The selected provider is OUT of network.")

    def pick(category, level="individual", network="in", stc=None, message_part=None):
        for b in check.benefits:
            if (b.category == category and b.coverage_level == level and b.network == network
                    and (stc is None or b.service_type_code == stc)
                    and (message_part is None or message_part in (b.message or ""))):
                return b
        return None

    office = pick("co_payment", stc="98", message_part="PRIMARY CARE")
    spec = pick("co_payment", stc="98", message_part="SPECIALIST")
    prev = pick("co_payment", stc="98", message_part="PREVENTIVE")
    if office:
        facts.append(f"Office visit copay {_fmt_money(office.amount)}"
                     + (f", specialist {_fmt_money(spec.amount)}" if spec else "")
                     + (f", preventive {_fmt_money(prev.amount)}" if prev else "") + ".")
    coins = pick("co_insurance", stc="30")
    if coins:
        facts.append(f"Coinsurance {float(coins.percent):g}% after deductible.")
    ded, rem = pick("deductible", stc="30"), pick("remaining_deductible", stc="30")
    if ded:
        facts.append(f"Individual deductible {_fmt_money(ded.amount)}"
                     + (f", {_fmt_money(rem.amount)} remaining" if rem else "") + ".")
    oop, oop_rem = pick("out_of_pocket", stc="30"), pick("remaining_out_of_pocket", stc="30")
    if oop:
        facts.append(f"Out-of-pocket max {_fmt_money(oop.amount)}"
                     + (f", {_fmt_money(oop_rem.amount)} remaining" if oop_rem else "") + ".")
    auth = [b for b in check.benefits if b.auth_required]
    if auth:
        facts.append("Prior authorization required for: " + ", ".join(sorted({b.service_type for b in auth})) + ".")
    if check.discrepancies:
        facts.append(f"{len(check.discrepancies)} discrepancy(ies) between payer and chart: "
                     + "; ".join(d["field"] for d in check.discrepancies) + ".")
    return facts


_NUM = re.compile(r"\$[\d,]+(?:\.\d{2})?|\d+(?:\.\d+)?%")


def summarize(check: EligibilityCheck, patient: Patient) -> str:
    facts = [f for f in _facts(check) if f]
    template = " ".join(facts)
    llm = get_llm()
    if llm.is_offline:
        return template
    allowed = set(_NUM.findall(template))
    system = ("You summarize insurance eligibility for medical front-desk staff in 2-3 short sentences. "
              "Use ONLY the facts given. Do not add numbers, plans or advice. Keep every dollar and percent exactly.")
    try:
        text = llm.complete(system, "\n".join(facts)).strip()
    except LLMError as exc:
        log.warning("LLM summary failed, using template: %s", exc)
        return template
    if text and set(_NUM.findall(text)) <= allowed and len(text) < 900:
        return text
    log.info("LLM summary rejected (numbers not grounded in the 271); using template")
    return template
