"""AI fallback chain: order, retries, cooldowns, bad output, and patient-data routing."""
import json

import httpx
import pytest

from app.config import get_settings
from app.llm import providers as P
from app.llm.providers import FallbackLLM, LLMError, get_llm


@pytest.fixture
def settings(monkeypatch):
    s = get_settings().model_copy(update={
        "llm_provider": "auto", "llm_provider_priority": "azure_openai,openai,gemini,huggingface",
        "llm_phi_allowed_providers": "azure_openai", "llm_max_attempts_per_provider": 2, "llm_cooldown_seconds": 60,
        "azure_openai_endpoint": "https://<resource>.openai.azure.com", "azure_openai_api_key": "",
        "azure_openai_deployment": "gpt-4o-mini", "azure_openai_fallback_deployments": "",
        "openai_api_key": "", "gemini_api_key": "", "huggingface_api_key": "",
        "huggingface_model": "small-model", "huggingface_fallback_models": "big-model,other-model"})
    P.reset_cooldowns()
    monkeypatch.setattr(P.time, "sleep", lambda _: None)
    yield s
    P.reset_cooldowns()


class Wire:
    """Fake HTTP: script per (host-ish, model) -> list of responses (status, content)."""

    def __init__(self, monkeypatch, script):
        self.calls, self.script = [], {k: list(v) for k, v in script.items()}
        monkeypatch.setattr(P.httpx, "post", self.post)

    def post(self, url, headers=None, json=None, timeout=None):  # noqa: A002
        model = (json or {}).get("model") or url.split("/deployments/")[1].split("/")[0]
        key = ("azure" if "openai.azure.com" in url else "hf" if "huggingface" in url else "other", model)
        self.calls.append(key)
        status, content = self.script.get(key, [(404, None)]).pop(0) if self.script.get(key) else (404, None)
        req = httpx.Request("POST", url)
        body = {"choices": [{"message": {"content": content}}]} if status == 200 else {"error": "x"}
        return httpx.Response(status, json=body, request=req)


OK = json.dumps({"ok": True})


def test_no_keys_means_rules_engine(settings):
    llm = get_llm(settings=settings)
    assert isinstance(llm, FallbackLLM) and llm.is_offline and llm.model == "rules-v1"
    assert [c["configured"] for c in llm.describe()["chain"]] == [False] * len(llm.chain)


def test_patient_data_never_reaches_unapproved_providers(settings, monkeypatch):
    s = settings.model_copy(update={"huggingface_api_key": "hf_test"})
    wire = Wire(monkeypatch, {("hf", "small-model"): [(200, OK)]})
    phi = FallbackLLM(s, phi=True)
    assert phi.is_offline  # Hugging Face is configured but not approved for patient data
    with pytest.raises(LLMError):
        phi.complete_json("sys", "patient email")
    assert wire.calls == []  # nothing was sent anywhere
    test_only = FallbackLLM(s, phi=False)  # synthetic test data may use it
    assert test_only.complete_json("sys", "fake email") == {"ok": True} and test_only.name == "huggingface:small-model"


def test_order_retries_and_fallback_models(settings, monkeypatch):
    s = settings.model_copy(update={"azure_openai_endpoint": "https://demo.openai.azure.com", "azure_openai_api_key": "k",
                                    "huggingface_api_key": "hf_test"})
    wire = Wire(monkeypatch, {
        ("azure", "gpt-4o-mini"): [(429, None), (429, None)],     # rate limited twice -> give up on Azure
        ("hf", "small-model"): [(200, "not json at all")],        # bad output -> next model
        ("hf", "big-model"): [(503, None), (200, OK)],            # transient, retried, then answers
    })
    llm = FallbackLLM(s, phi=False)
    assert llm.complete_json("sys", "fake") == {"ok": True}
    assert llm.name == "huggingface:big-model"
    assert wire.calls == [("azure", "gpt-4o-mini"), ("azure", "gpt-4o-mini"), ("hf", "small-model"),
                          ("hf", "big-model"), ("hf", "big-model")]
    # failed models cool down: the next request goes straight to the model that works
    wire.calls.clear()
    wire.script[("hf", "big-model")] = [(200, OK)]
    assert llm.complete_json("sys", "fake") == {"ok": True}
    assert wire.calls == [("hf", "big-model")]


def test_permanent_error_is_not_retried(settings, monkeypatch):
    s = settings.model_copy(update={"azure_openai_endpoint": "https://demo.openai.azure.com", "azure_openai_api_key": "bad"})
    wire = Wire(monkeypatch, {("azure", "gpt-4o-mini"): [(401, None)]})
    with pytest.raises(LLMError, match="No AI model available"):
        FallbackLLM(s).complete_json("sys", "x")
    assert wire.calls == [("azure", "gpt-4o-mini")]  # 401: one call, no retry


def test_extraction_falls_back_to_rules_and_says_so(settings, monkeypatch):
    from datetime import date

    from app.services.extraction import ExtractionContext, extract_request

    s = settings.model_copy(update={"azure_openai_endpoint": "https://demo.openai.azure.com", "azure_openai_api_key": "k"})
    Wire(monkeypatch, {("azure", "gpt-4o-mini"): [(500, None), (500, None)]})
    ctx = ExtractionContext(reference_date=date(2026, 10, 8), sender_email="a@example.com", sender_name="Ann Lee",
                            payers=[], providers=[])
    x = extract_request(FallbackLLM(s), "Appt", "Please book a check-up next week. DOB 01/02/1990.\nThanks,\nAnn Lee", ctx)
    assert x.engine.startswith("rules (fallback") and "azure_openai:gpt-4o-mini" in x.engine_error
    assert x.patient.first_name == "Ann" and x.intent == "new_appointment"
