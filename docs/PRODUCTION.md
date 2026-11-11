# Production readiness

## Built in

| Area | What's in place |
|---|---|
| Database | PostgreSQL (`scribe_docs`). The API runs as least-privilege `docshealth_app`, with the audit log append-only; schema changes use `MIGRATION_DATABASE_URL` (owner) |
| Secrets at rest | SSN, licence numbers, Entra client secret, OAuth refresh tokens and PKCE verifiers are encrypted with `FIELD_ENCRYPTION_KEY` (rotatable: `scripts/rotate_field_key.py`) |
| Auth | PBKDF2 passwords, JWT sessions revoked on password change, email password reset, account lockout (5 failures / 15 min), per-IP throttling |
| HTTP | Security headers (nosniff, DENY framing, no-referrer, no-store on API, HSTS in production), `X-Request-ID` on every response, JSON errors with reference IDs, API docs hidden in production |
| Email | Outbound kill switch (Settings → Integrations); SMTP / Graph / SendGrid / Mailgun with TLS, retries and classification; Microsoft 365 in-thread replies; loop protection; every message logged with status |
| Integrations | Entra ID delegated (PKCE) or application (admin consent) modes; refresh tokens rotated; `reauth_required` surfaced in the UI |
| Safety | Production refuses to start with weak JWT secret, missing encryption key, SQLite, non-HTTPS portal URL, demo seed passwords or HTTP CORS origins (`app/prodcheck.py`). Demo lab is disabled in production |
| Tests | 130 automated tests, plus `scripts/smoke_test.py` (11 live examples) and `scripts/check_db.py` |

## Before go-live

1. **Hosting:** run behind HTTPS (IIS/ARR, nginx or Azure App Service), set `PORTAL_BASE_URL=https://…`, and register
   `https://…/api/integrations/microsoft/callback` as a Web redirect URI in Entra.
2. **Environment:** set `ENVIRONMENT=production`. Set unique `SEED_ADMIN_PASSWORD` / `SEED_SCHEDULER_PASSWORD`,
   or remove the demo users and create real staff accounts. Set `CORS_ORIGINS` to the HTTPS portal origin only.
3. **Secrets:** move `.env` values (`DATABASE_URL`, `MIGRATION_DATABASE_URL`, `JWT_SECRET`, `FIELD_ENCRYPTION_KEY`)
   into a secret store (e.g. Azure Key Vault) and back up `FIELD_ENCRYPTION_KEY`, because losing it makes
   encrypted data unreadable. Change the `postgres` password, since it was shared in chat during setup.
4. **Data:** remove demo and smoke-test patients, and load real providers, payers and schedules.
5. **Email:** connect Microsoft 365 on Settings → Integrations, verify the mailbox, then turn outbound email on.
   Use only BAA-covered services for PHI.
6. **Eligibility and LLM:** replace the mock clearinghouse with your vendor (`X12ClearinghouseAdapter`), and set
   `LLM_PROVIDER=azure_openai` (BAA-covered) if AI extraction should use an LLM.
7. **Operations:** run the API as a Windows service or container with several workers. Schedule PostgreSQL
   backups, and forward logs (they carry request IDs) to your monitoring.
8. **Migrations:** the app adds new tables and columns automatically. Adopt Alembic before the first change
   that needs to drop or rename a column.
