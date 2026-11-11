"""What kind of message is this? A Hugging Face zero-shot classifier, with the rules as the fallback.

The scheduling assistant used to treat any email as a scheduling request if it mentioned a visit or an
appointment anywhere (a legal footer was enough). Two things now decide what an email is about:

1. The *latest message* only: the quoted thread and the legal footer are cut off first (`latest_message`),
   so boilerplate cannot make a bill look like an appointment request.
2. When a Hugging Face key is configured, a zero-shot classification model (default
   facebook/bart-large-mnli, through Inference Providers) reads that latest message and may correct the
   rules' answer. It never *invents* a scheduling request: it can only move a message to or from the
   non-scheduling labels when it is confident.

The model is optional. With no key, no credits (HTTP 402), an error or a timeout the rules decide, and the
result says so ("classifier": "rules (huggingface unavailable: HTTP 402)"). After a failure the model is not
tried again for a few minutes, so a missing quota does not slow every email.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

import httpx

from app.config import get_settings

log = logging.getLogger("docshealth.classifier")

# The label shown to the model, and the intent it stands for.
LABELS: dict[str, str] = {
    "a request to book a new appointment or see a doctor": "new_appointment",
    "a request to reschedule an existing appointment": "reschedule",
    "a request to cancel an appointment": "cancel",
    "a bill, invoice, payment, medical records or legal case document": "billing_records",
    "a change to insurance information": "insurance_update",
    "a general question": "general_question",
    "an automated notification, advertisement or message that needs no reply": "other",
}
NON_SCHEDULING = {"billing_records", "general_question", "other"}

_QUOTE = re.compile(
    r"\n\s*(On .{5,140} wrote:|-{2,}\s*Original Message|Begin forwarded message|From:\s.+\n\s*(?:Sent|Date):|_{5,})", re.I
)
_FOOTER = re.compile(
    r"\n\s*(CONFIDENTIAL|DISCLAIMER|PRIVILEGED|IMPORTANT WARNING|NOTICE:|This (?:e-?mail|message) (?:and any|contains|is intended))"
)


def latest_message(body: str) -> str:
    """The newest message of a thread, without the quoted history or the legal footer."""
    text = (body or "").replace("\r\n", "\n")
    footer = _FOOTER.search(text)
    if footer and footer.start() > 20:
        text = text[: footer.start()]
    quote = _QUOTE.search(text)
    if quote and quote.start() > 2:
        text = text[: quote.start()]
    return text.strip()


@dataclass(frozen=True)
class Classification:
    intent: str | None  # None when the model did not answer
    score: float
    note: str  # which engine answered, or why it did not


_lock = threading.Lock()
_down_until = 0.0
RETRY_AFTER_SECONDS = 300


def _unavailable(reason: str) -> Classification:
    global _down_until
    with _lock:
        _down_until = time.monotonic() + RETRY_AFTER_SECONDS
    return Classification(None, 0.0, f"rules (huggingface unavailable: {reason})")


def classify_with_huggingface(subject: str, text: str, *, client: httpx.Client | None = None) -> Classification:
    """Zero-shot classify one message. Never raises."""
    s = get_settings()
    key = (s.huggingface_api_key or "").strip()
    if getattr(s, "email_classifier", "auto") == "off" or not key:
        return Classification(None, 0.0, "rules")
    if time.monotonic() < _down_until:
        return Classification(None, 0.0, "rules (huggingface unavailable)")
    model = getattr(s, "email_classifier_model", "facebook/bart-large-mnli")
    url = f"https://router.huggingface.co/hf-inference/models/{model}"
    payload = {
        "inputs": f"{subject}\n{text}"[:1200],
        "parameters": {"candidate_labels": list(LABELS), "hypothesis_template": "This email is {}."},
    }
    own = client is None
    client = client or httpx.Client(timeout=8.0)
    try:
        r = client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload)
    except httpx.HTTPError as exc:
        return _unavailable(type(exc).__name__)
    finally:
        if own:
            client.close()
    if r.status_code != 200:
        return _unavailable(f"HTTP {r.status_code}")
    try:
        data = r.json()
        # Either [{"label","score"},...] (hf-inference) or {"labels":[...],"scores":[...]}.
        if isinstance(data, list):
            top = max(data, key=lambda d: d["score"])
            label, score = top["label"], float(top["score"])
        else:
            label, score = data["labels"][0], float(data["scores"][0])
        return Classification(LABELS[label], round(score, 3), f"huggingface:{model}")
    except (KeyError, ValueError, TypeError, IndexError):
        return _unavailable("unreadable answer")


def refine(rules_intent: str, rules_confidence: float, subject: str, body: str) -> tuple[str, float, str]:
    """Combine the rules' answer with the model's. Returns (intent, confidence, note).

    The model can move a message to a non-scheduling label (a bill, a notification) or rescue an "other" into
    a scheduling label, but only when it is confident, and it never overrides an explicit cancel / reschedule.
    """
    got = classify_with_huggingface(subject, latest_message(body))
    if got.intent is None:
        return rules_intent, rules_confidence, got.note
    if got.intent == rules_intent:
        return rules_intent, max(rules_confidence, got.score), got.note
    if rules_intent in {"cancel", "reschedule"}:
        return rules_intent, rules_confidence, f"{got.note} (kept the explicit {rules_intent})"
    if got.intent in NON_SCHEDULING and got.score >= 0.75 and rules_intent in {"new_appointment", "other", "general_question"}:
        return got.intent, got.score, got.note
    if rules_intent in {"other", "general_question"} and got.intent in {"new_appointment", "reschedule", "cancel"} \
            and got.score >= 0.85:
        return got.intent, got.score, got.note
    return rules_intent, rules_confidence, f"{got.note} (disagreed at {got.score}; rules kept)"
