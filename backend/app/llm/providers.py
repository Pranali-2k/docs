"""LLM provider adapters.

Every provider implements `complete(system, user, json_mode)` and returns the
raw text. The task logic (extraction, reply drafting) lives in app.services and
is provider-agnostic. `MockProvider` performs no network I/O. The services
detect it with `is_offline` and use the deterministic rule engine instead.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from abc import ABC, abstractmethod

import httpx

from app.config import Settings, get_settings


log = logging.getLogger("docshealth.llm")


class LLMError(RuntimeError):
    pass


def _placeholder(value: str) -> bool:
    return not value or "<" in value or "your-resource" in value


class LLMProvider(ABC):
    name: str = "base"
    is_offline: bool = False

    def __init__(self, settings: Settings, model: str | None = None):
        self.settings = settings
        self._model = model  # override (fallback model of the same provider)

    @property
    def model(self) -> str:
        return ""

    def is_configured(self) -> bool:
        """Key present and no placeholder values: worth calling at all."""
        return True

    @abstractmethod
    def complete(self, system: str, user: str, json_mode: bool = False) -> str: ...

    def complete_json(self, system: str, user: str) -> dict:
        text = self.complete(system, user, json_mode=True)
        return parse_json_object(text)

    def describe(self) -> dict:
        return {"provider": self.name, "model": self.model, "offline": self.is_offline}


def parse_json_object(text: str) -> dict:
    """Parse a JSON object from model output, tolerating code fences / chatter."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("Model did not return JSON") from None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("Model JSON was not an object")
    return value


class MockProvider(LLMProvider):
    name = "mock"
    is_offline = True

    @property
    def model(self) -> str:
        return "rules-v1"

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:  # pragma: no cover
        raise LLMError("MockProvider has no free-text completion; services use the rule engine")


class _OpenAICompatible(LLMProvider):
    """Chat Completions wire format (OpenAI, Azure OpenAI, Hugging Face router)."""

    def _url(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict:
        raise NotImplementedError

    def _payload_extra(self) -> dict:
        return {"model": self.model}

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        payload = {
            **self._payload_extra(),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.settings.llm_temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = httpx.post(
                self._url(), headers=self._headers(), json=payload, timeout=self.settings.llm_timeout_seconds
            )
            if resp.status_code == 400 and json_mode:
                # Some hosted models reject response_format. Retry without it.
                payload.pop("response_format", None)
                resp = httpx.post(
                    self._url(), headers=self._headers(), json=payload, timeout=self.settings.llm_timeout_seconds
                )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"{self.name} request failed: {exc}") from exc


class OpenAIProvider(_OpenAICompatible):
    name = "openai"

    @property
    def model(self) -> str:
        return self._model or self.settings.openai_model

    def is_configured(self) -> bool:
        return not _placeholder(self.settings.openai_api_key)

    def _url(self) -> str:
        return self.settings.openai_base_url.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict:
        if not self.settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        return {"Authorization": f"Bearer {self.settings.openai_api_key}"}


class AzureOpenAIProvider(_OpenAICompatible):
    name = "azure_openai"

    @property
    def model(self) -> str:
        return self._model or self.settings.azure_openai_deployment

    def is_configured(self) -> bool:
        s = self.settings
        return not (_placeholder(s.azure_openai_api_key) or _placeholder(s.azure_openai_endpoint)
                    or not s.azure_openai_deployment)

    def _url(self) -> str:
        s = self.settings
        return (
            f"{s.azure_openai_endpoint.rstrip('/')}/openai/deployments/{self.model}"
            f"/chat/completions?api-version={s.azure_openai_api_version}"
        )

    def _headers(self) -> dict:
        if not self.settings.azure_openai_api_key:
            raise LLMError("AZURE_OPENAI_API_KEY is not set")
        return {"api-key": self.settings.azure_openai_api_key}

    def _payload_extra(self) -> dict:
        return {}  # deployment is in the URL


class HuggingFaceProvider(_OpenAICompatible):
    name = "huggingface"

    @property
    def model(self) -> str:
        return self._model or self.settings.huggingface_model

    def is_configured(self) -> bool:
        return not _placeholder(self.settings.huggingface_api_key)

    def _url(self) -> str:
        return self.settings.huggingface_base_url.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict:
        if not self.settings.huggingface_api_key:
            raise LLMError("HUGGINGFACE_API_KEY is not set")
        return {"Authorization": f"Bearer {self.settings.huggingface_api_key}"}


class GeminiProvider(LLMProvider):
    name = "gemini"

    @property
    def model(self) -> str:
        return self._model or self.settings.gemini_model

    def is_configured(self) -> bool:
        return not _placeholder(self.settings.gemini_api_key)

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        if not self.settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        config: dict = {"temperature": self.settings.llm_temperature}
        if json_mode:
            config["responseMimeType"] = "application/json"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        try:
            resp = httpx.post(
                url,
                headers={"x-goog-api-key": self.settings.gemini_api_key},
                json=payload,
                timeout=self.settings.llm_timeout_seconds,
            )
            resp.raise_for_status()
            parts = resp.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"gemini request failed: {exc}") from exc


PROVIDERS: dict[str, type[LLMProvider]] = {
    "mock": MockProvider,
    "openai": OpenAIProvider,
    "azure_openai": AzureOpenAIProvider,
    "azure": AzureOpenAIProvider,
    "gemini": GeminiProvider,
    "huggingface": HuggingFaceProvider,
    "hf": HuggingFaceProvider,
}


CANONICAL = {"azure": "azure_openai", "hf": "huggingface"}
FALLBACK_MODELS = {"azure_openai": "azure_openai_fallback_deployments", "openai": "openai_fallback_models",
                   "gemini": "gemini_fallback_models", "huggingface": "huggingface_fallback_models"}

_cooldown: dict[str, float] = {}  # "provider:model" -> monotonic time it may be tried again
_cooldown_lock = threading.Lock()


def _split(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _transient(exc: Exception) -> bool:
    cause = exc.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        return cause.response.status_code == 429 or cause.response.status_code >= 500
    return isinstance(cause, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError))


class FallbackLLM(LLMProvider):
    """Try models in LLM_PROVIDER_PRIORITY order (each provider's main model, then its fallback models).

    * Unconfigured providers are skipped silently; so are models cooling down after a failure.
    * Transient errors (429, 5xx, timeouts) are retried with backoff up to LLM_MAX_ATTEMPTS_PER_PROVIDER;
      anything else (bad key, unknown deployment, invalid JSON) moves straight on to the next model.
    * phi=True (the default: emails contain patient data) restricts the chain to LLM_PHI_ALLOWED_PROVIDERS.
    * When nothing is usable `is_offline` is True, so the services use the deterministic rules engine.
    `name` reports the model that actually answered, for the audit trail.
    """

    def __init__(self, settings: Settings, *, phi: bool = True):
        super().__init__(settings)
        self.phi = phi
        self.used: str | None = None
        allowed = {CANONICAL.get(p.lower(), p.lower()) for p in _split(settings.llm_phi_allowed_providers)}
        self.chain: list[dict] = []
        seen: set[str] = set()
        for raw in _split(settings.llm_provider_priority) or [settings.llm_provider]:
            key = CANONICAL.get(raw.lower(), raw.lower())
            if key in seen or key not in PROVIDERS or key in {"mock", "auto", "fallback"}:
                continue
            seen.add(key)
            main = PROVIDERS[key](settings)
            for model in dict.fromkeys([main.model, *_split(getattr(settings, FALLBACK_MODELS.get(key, ""), ""))]):
                prov = PROVIDERS[key](settings, model)
                self.chain.append({"provider": key, "model": model, "impl": prov, "configured": prov.is_configured(),
                                   "phi_allowed": key in allowed})

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.used or "fallback"

    @property
    def model(self) -> str:
        e = self._eligible()
        return e[0]["model"] if e else "rules-v1"

    def _eligible(self) -> list[dict]:
        return [e for e in self.chain if e["configured"] and (e["phi_allowed"] or not self.phi)]

    @property
    def is_offline(self) -> bool:  # type: ignore[override]
        return not self._eligible()

    def describe(self) -> dict:
        now = time.monotonic()
        return {"provider": "fallback", "model": self.model, "offline": self.is_offline, "patient_data": self.phi,
                "chain": [{"provider": e["provider"], "model": e["model"], "configured": e["configured"],
                           "phi_allowed": e["phi_allowed"],
                           "cooling_down": _cooldown.get(f"{e['provider']}:{e['model']}", 0) > now}
                          for e in self.chain]}

    def _run(self, call):
        errors: list[str] = []
        settings = self.settings
        for e in self._eligible():
            label = f"{e['provider']}:{e['model']}"
            with _cooldown_lock:
                if _cooldown.get(label, 0) > time.monotonic():
                    errors.append(f"{label} cooling down")
                    continue
            for attempt in range(max(1, settings.llm_max_attempts_per_provider)):
                try:
                    out = call(e["impl"])
                    self.used = label
                    return out
                except LLMError as exc:
                    if _transient(exc) and attempt + 1 < settings.llm_max_attempts_per_provider:
                        time.sleep(min(4.0, 0.5 * 2 ** attempt))
                        continue
                    errors.append(f"{label}: {str(exc)[:160]}")
                    log.warning("LLM %s failed, trying the next model: %s", label, str(exc)[:200])
                    with _cooldown_lock:
                        _cooldown[label] = time.monotonic() + settings.llm_cooldown_seconds
                    break
        raise LLMError("No AI model available (" + "; ".join(errors or ["none configured"]) + ")")

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        return self._run(lambda p: p.complete(system, user, json_mode))

    def complete_json(self, system: str, user: str) -> dict:
        return self._run(lambda p: p.complete_json(system, user))  # invalid JSON also moves to the next model


def reset_cooldowns() -> None:
    with _cooldown_lock:
        _cooldown.clear()


def get_llm(name: str | None = None, settings: Settings | None = None, *, phi: bool = True) -> LLMProvider:
    """`name` picks one provider (diagnostics). Otherwise LLM_PROVIDER, where "auto"/"fallback", or any
    LLM_PROVIDER_PRIORITY, gives the fallback chain."""
    settings = settings or get_settings()
    key = (name or settings.llm_provider or "mock").lower().strip()
    if name is None and (key in {"auto", "fallback"} or (settings.llm_provider_priority and key != "mock")):
        return FallbackLLM(settings, phi=phi)
    if key not in PROVIDERS:
        raise LLMError(f"Unknown LLM_PROVIDER '{key}'. Options: auto, {', '.join(sorted(PROVIDERS))}")
    return PROVIDERS[key](settings)
