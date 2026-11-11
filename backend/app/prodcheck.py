"""Fail fast when ENVIRONMENT=production is misconfigured (called at API startup)."""
from __future__ import annotations

from app.config import Settings

WEAK_SEEDS = {"DemoAdmin!2026", "DemoSched!2026"}


def problems(s: Settings) -> list[str]:
    out = []
    if len(s.jwt_secret) < 32 or "change-me" in s.jwt_secret or "insecure" in s.jwt_secret:
        out.append("JWT_SECRET must be a random value of at least 32 characters")
    if not s.field_encryption_key:
        out.append("FIELD_ENCRYPTION_KEY must be set (python -m scripts.rotate_field_key)")
    if not s.database_url.startswith("postgresql"):
        out.append("DATABASE_URL must point at PostgreSQL")
    if not s.portal_base_url.startswith("https://"):
        out.append("PORTAL_BASE_URL must be https:// (also required by Microsoft for non-localhost redirect URIs)")
    if s.seed_admin_password in WEAK_SEEDS or s.seed_scheduler_password in WEAK_SEEDS:
        out.append("Change SEED_ADMIN_PASSWORD / SEED_SCHEDULER_PASSWORD from the demo defaults")
    if any(o.startswith("http://") and "localhost" not in o for o in s.cors_origin_list):
        out.append("CORS_ORIGINS must only contain https:// origins")
    return out


def check(s: Settings) -> None:
    if not s.is_production:
        return
    found = problems(s)
    if found:
        raise RuntimeError("Refusing to start in production:\n - " + "\n - ".join(found))
