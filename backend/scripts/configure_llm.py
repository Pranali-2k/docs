"""Set up the AI provider(s) and prove they work, from your own terminal.

    python -m scripts.configure_llm            # prompts for Azure OpenAI and/or Hugging Face, tests, then saves
    python -m scripts.configure_llm --azure    # only Azure OpenAI
    python -m scripts.configure_llm --hf       # only Hugging Face

Keys are typed hidden, never printed or logged, and only written to backend/.env after a test request
succeeded (a backup of .env is kept as .env.bak). Press Enter at a prompt to keep the current value.
Azure OpenAI becomes the active provider (LLM_PROVIDER=azure_openai) when it works, because patient emails
should only go to a provider covered by a BAA; Hugging Face is saved for testing with synthetic data.
A one-line result per run (never the key) is appended to logs/llm_setup.log.
"""
from __future__ import annotations

import argparse
import getpass
import re
import sys
from datetime import datetime
from pathlib import Path

from app.config import Settings, get_settings
from app.llm.providers import LLMError, get_llm
from app.services.envfile import update_env

ENV = Path(__file__).resolve().parents[1] / ".env"
RUN_LOG = Path(__file__).resolve().parents[2] / "logs" / "llm_setup.log"


def run_log(line: str) -> None:
    try:
        RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with RUN_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:
        pass


def ask(label: str, current: str | None, *, secret: bool = False) -> str:
    shown = ("(set, hidden)" if current else "(empty)") if secret else (current or "(empty)")
    prompt = f"{label} [{shown}]: "
    value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
    return value or (current or "")


def test(name: str, overrides: dict) -> tuple[bool, str]:
    base = get_settings()
    settings = Settings(**{**base.model_dump(), **overrides})
    try:
        out = get_llm(name, settings).complete_json('Reply with the JSON object {"ok": true} and nothing else.', "ping")
        return True, f"replied {out}"
    except LLMError as exc:
        from scripts.check_llm import explain

        return False, explain(exc)


def configure_azure(s) -> dict | None:
    print("\nAzure OpenAI  (Azure portal -> your OpenAI resource -> Keys and Endpoint; deployment name from AI Foundry)")
    endpoint = ask("Endpoint, e.g. https://my-resource.openai.azure.com", None if "<" in s.azure_openai_endpoint else s.azure_openai_endpoint)
    endpoint = endpoint.rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(/.*)?", endpoint):
        print("  Endpoint must look like https://<name>.openai.azure.com. Skipping Azure.")
        return None
    deployment = ask("Deployment name", s.azure_openai_deployment)
    version = ask("API version", s.azure_openai_api_version)
    key = ask("API key", s.azure_openai_api_key, secret=True)
    if not key:
        print("  No key entered. Skipping Azure.")
        return None
    values = {"azure_openai_endpoint": endpoint, "azure_openai_deployment": deployment,
              "azure_openai_api_version": version, "azure_openai_api_key": key}
    ok, detail = test("azure_openai", values)
    print(f"  {'OK' if ok else 'FAILED'}: Azure OpenAI ({deployment}) {detail}")
    run_log(f"azure_openai endpoint_host={endpoint.split('/')[2]} deployment={deployment} ok={ok} {'' if ok else detail}")
    return values if ok else None


def configure_hf(s) -> dict | None:
    print("\nHugging Face  (huggingface.co -> Settings -> Access Tokens; needs 'Make calls to Inference Providers')")
    model = ask("Model", s.huggingface_model)
    key = ask("Access token (hf_...)", s.huggingface_api_key, secret=True)
    if not key:
        print("  No token entered. Skipping Hugging Face.")
        return None
    values = {"huggingface_model": model, "huggingface_api_key": key}
    ok, detail = test("huggingface", values)
    print(f"  {'OK' if ok else 'FAILED'}: Hugging Face ({model}) {detail}")
    run_log(f"huggingface model={model} ok={ok} {'' if ok else detail}")
    return values if ok else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--azure", action="store_true")
    ap.add_argument("--hf", action="store_true")
    args = ap.parse_args()
    both = not (args.azure or args.hf)
    s = get_settings()
    print(f"Settings file: {ENV}")
    saved: dict[str, str] = {}
    try:
        if both or args.azure:
            v = configure_azure(s)
            if v:
                saved.update({"AZURE_OPENAI_ENDPOINT": v["azure_openai_endpoint"],
                              "AZURE_OPENAI_DEPLOYMENT": v["azure_openai_deployment"],
                              "AZURE_OPENAI_API_VERSION": v["azure_openai_api_version"],
                              "AZURE_OPENAI_API_KEY": v["azure_openai_api_key"], "LLM_PROVIDER": "auto"})
        if both or args.hf:
            v = configure_hf(s)
            if v:
                saved.update({"HUGGINGFACE_MODEL": v["huggingface_model"], "HUGGINGFACE_API_KEY": v["huggingface_api_key"]})
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Nothing was saved.")
        return 1
    if not saved:
        print("\nNothing passed the test, so nothing was saved. backend/.env is unchanged.")
        return 1
    try:
        update_env(saved, ENV)  # atomic write, keeps .env.bak
    except (OSError, ValueError) as exc:
        print(f"\nCould not write {ENV}: {exc}. Nothing was saved.")
        return 1
    names = ", ".join(k for k in saved if not k.endswith("_KEY"))
    print(f"\nSaved to backend/.env: {names} (+ keys, hidden). Backup: .env.bak")
    if "LLM_PROVIDER" in saved:
        print("Active: fallback chain with Azure OpenAI first (LLM_PROVIDER=auto). Restart the API to apply.")
    else:
        print("The active provider is unchanged (Hugging Face is for testing with synthetic data only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
