"""Application settings, loaded from environment variables / .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "dev"
    database_url: str = "sqlite:///./data/docshealth.db"
    # Admin/owner connection used only for schema changes (create tables, add columns).
    # The API itself runs as the least-privilege DATABASE_URL role.
    migration_database_url: str = ""
    jwt_secret: str = "dev-insecure-secret-change-me"
    jwt_expire_minutes: int = 720
    # Healthcare Ecosystem gateway sign-in; off while the secret is empty.
    gateway_secret: str = ""
    # A request from someone who already has a chart is booked straight away when the doctor has a free
    # slot, even with no insurance on file; staff are told to add the insurance before the visit.
    book_without_insurance: bool = True
    # Message classification (services/email_classifier.py): auto = use Hugging Face when a key is set, off = rules only.
    email_classifier: str = "auto"
    email_classifier_model: str = "facebook/bart-large-mnli"
    gateway_tenant: str = "docs-health"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    seed_admin_email: str = "admin@docshealth.local"
    seed_admin_password: str = "DemoAdmin!2026"
    seed_scheduler_email: str = "scheduler@docshealth.local"
    seed_scheduler_password: str = "DemoSched!2026"

    # LLM
    llm_provider: str = "mock"  # a single provider, or "auto" to use LLM_PROVIDER_PRIORITY
    llm_timeout_seconds: float = 45
    llm_temperature: float = 0.1
    # Fallback chain: providers tried in this order (unconfigured ones are skipped), then the rules engine.
    llm_provider_priority: str = ""
    # Patient emails may ONLY go to these providers (BAA in place). Others are used for synthetic tests only.
    llm_phi_allowed_providers: str = "azure_openai"
    llm_max_attempts_per_provider: int = 2  # retries on 429 / 5xx / timeouts
    llm_cooldown_seconds: float = 60  # skip a failing model for this long
    # extra models/deployments tried after the main one of the same provider (comma-separated)
    azure_openai_fallback_deployments: str = ""
    openai_fallback_models: str = ""
    gemini_fallback_models: str = ""
    huggingface_fallback_models: str = "meta-llama/Llama-3.3-70B-Instruct,Qwen/Qwen2.5-72B-Instruct"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o-mini"
    azure_openai_api_version: str = "2024-10-21"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    huggingface_api_key: str = ""
    huggingface_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    huggingface_base_url: str = "https://router.huggingface.co/v1"

    # Automation policy
    ai_confidence_threshold: float = 0.80
    match_confidence_threshold: float = 0.80
    auto_book_appointments: bool = True
    # A scheduling email that names a patient exactly (first and last name) and clashes with nothing, but gives no
    # date of birth, may still be booked when the chart is at least this sure (and 20 points ahead of any other).
    # The reply tells the sender which chart it was booked under. Set to 1 to always ask for a second identifier.
    schedule_match_threshold: float = 0.60
    # When the asked-for time is taken, reply at once with the doctor's nearest openings instead of holding it for review.
    offer_slots_when_unavailable: bool = True
    auto_send_responses: bool = True
    clinic_name: str = "Docs Health"
    clinic_timezone: str = "America/Los_Angeles"

    # Email
    email_inbound_mode: str = "none"
    email_outbound_mode: str = "outbox"
    scheduling_mailbox: str = "scheduling@docshealth.example"
    mail_poll_interval_seconds: int = 0
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    imap_host: str = "outlook.office365.com"
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    smtp_host: str = "smtp.office365.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "scheduling@docshealth.example"
    smtp_from_name: str = "Docs Health Scheduling"
    smtp_security: str = "starttls"  # starttls (587) | ssl (465) | none (localhost test servers only)
    smtp_timeout_seconds: float = 20
    smtp_max_attempts: int = 3
    smtp_retry_base_seconds: float = 2
    # HTTPS email APIs (EMAIL_OUTBOUND_MODE=sendgrid | mailgun)
    sendgrid_api_key: str = ""
    mailgun_api_key: str = ""
    mailgun_domain: str = ""
    mailgun_region: str = "us"  # us | eu

    eligibility_adapter: str = "mock"
    upload_dir: str = "./data/uploads"
    # Outbox mode also writes each outgoing email as an .eml file here.
    mail_drop_dir: str = str(Path(__file__).resolve().parents[1] / "data" / "maildrop")

    # Password reset: the emailed link points at the portal (never taken from request headers).
    portal_base_url: str = "http://localhost:5181"
    password_reset_ttl_minutes: int = 30
    password_reset_max_per_window: int = 3  # requests per user per 15 minutes

    # Sensitive identifiers (SSN, driver's license) are encrypted with this Fernet key.
    field_encryption_key: str = ""
    field_encryption_keys_previous: str = ""  # comma-separated older keys (decrypt only)

    # Practice identity used on eligibility (270) requests when checking by group NPI.
    practice_name: str = "Docs Health Medical Group"
    practice_group_npi: str = "1912345678"

    # Demo lab / sample emails. Off automatically when ENVIRONMENT=production.
    demo_enabled: bool | None = None

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}

    @property
    def demo_on(self) -> bool:
        return (not self.is_production) if self.demo_enabled is None else bool(self.demo_enabled)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
