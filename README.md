# Docs Health: AI Scheduling & Email Automation Platform

An end-to-end implementation of the *Docs Health Scheduling & Email Automation PRD*
(`docs/DocsHealth_PRD_Workflow.pdf`). The platform turns scheduling emails into portal
requests, matches them to patient charts, verifies insurance eligibility and network
participation, and then either books and replies automatically or routes the request to
scheduling staff with the reason and a drafted reply. Every step is audited.

```
Email → Request Intake → Patient Match → AI Analysis → Auto Response OR Manual Review → Resolution Tracking
```

| Layer | Technology |
|---|---|
| Backend logic | Python 3.11+, FastAPI, SQLAlchemy 2 |
| Database | PostgreSQL only, with Alembic migrations, triggers and stored procedures (see [Database](docs/DATABASE.md)) |
| LLM | Pluggable: **Azure OpenAI**, **OpenAI**, **Google Gemini**, **Hugging Face** (router / TGI), or `mock` (offline rule engine) |
| Frontend | Node.js: React 19 + Vite 6 + Tailwind CSS v4, using the Iyra HealthCare design system (warm linen canvas, EB Garamond / Inter, sage accents, lucide icons). Served in production by an Express server that proxies `/api` |
| Email | Microsoft Graph (Outlook / M365), IMAP/SMTP, or a safe outbox |

Further docs: [Architecture](docs/ARCHITECTURE.md) · [PRD traceability](docs/PRD_TRACEABILITY.md) · [Validation & demo plan](docs/VALIDATION_PLAN.md)

---

## Quick start (Windows)

Prerequisites: Python 3.11+ (or `uv`), Node.js 20+, PostgreSQL 14+.

```powershell
.\run.ps1 setup          # venv + deps + backend\.env + frontend\.env
```

### 1. Database

Create the role and database. The script prompts for the PostgreSQL superuser password,
so run it yourself in a terminal:

```powershell
cd backend
.venv\Scripts\python -m scripts.bootstrap_db --user docshealth --db docshealth
```

Put the printed URL in `backend\.env`:

```
DATABASE_URL=postgresql+psycopg://docshealth:<password>@localhost:5432/docshealth
```


```powershell
.\run.ps1 initdb         # tables + reference data + fictional demo patients
.\run.ps1 test           # 24 backend tests
.\run.ps1 demo           # prints expected vs actual outcome for all 11 PRD scenarios
```

### 2. Run

```powershell
.\run.ps1 api            # terminal 1 → http://127.0.0.1:8000  (OpenAPI docs at /docs)
.\run.ps1 web .\run.ps1 api            # terminal 1 → http://127.0.0.1:8000  (OpenAPI docs at /docs)
.\run.ps1 web            # terminal 2 → http://localhost:3000   (Node.js portal)
```

Sign in with the demo accounts from `backend\.env` (`SEED_ADMIN_*` / `SEED_SCHEDULER_*`,
defaults `scheduler@docshealth.local` / `DemoSched!2026`). Change them before any shared deployment.
Then open **Demo lab** and send the sample emails through the AI inbox.

For frontend development with hot reload, use `.\run.ps1 dev` (Vite on :5173, proxied to :8000).

### 3. Deploy (background services)

```powershell
.\run.ps1 deploy -ApiPort 8030 -WebPort 5181   # build + start API and portal detached, health-checked
.\run.ps1 status
.\run.ps1 stop
```

Logs go to `logs\api.log` and `logs\web.log`. On this machine the portal is deployed at
**http://localhost:5181** with the API on 8030, because :8000 and :5180 are used by other projects.

---

## Choosing an LLM

Set `LLM_PROVIDER` in `backend\.env`:

| Value | Required settings | Notes |
|---|---|---|
| `mock` (default) | none | Deterministic rule engine. Nothing leaves the server. Used by the tests and the demo |
| `azure_openai` | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` | Recommended for PHI: runs in your tenant under the Microsoft BAA |
| `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` | `OPENAI_BASE_URL` also allows any OpenAI-compatible gateway |
| `gemini` | `GEMINI_API_KEY`, `GEMINI_MODEL` | Google AI Studio / Gemini API |
| `huggingface` | `HUGGINGFACE_API_KEY`, `HUGGINGFACE_MODEL` | HF Inference router, or a self-hosted TGI/vLLM via `HUGGINGFACE_BASE_URL` |

The LLM only extracts structured data from the email and, optionally, polishes reply wording.
Patient identity, eligibility, network status and availability are always decided by
deterministic code. If the model fails, the rule engine takes over and the request goes to
manual review.

## Outlook integration

1. In Microsoft Entra ID, register an app with the **application** permissions `Mail.ReadWrite`
   and `Mail.Send`. Grant admin consent, then restrict it to the scheduling mailbox with an
   application access policy.
2. Set `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET` and `SCHEDULING_MAILBOX`.
3. Set `EMAIL_INBOUND_MODE=graph` and `MAIL_POLL_INTERVAL_SECONDS=60`, or schedule
   `python -m scripts.poll_mailbox`.
4. Set `EMAIL_OUTBOUND_MODE=graph` once validation is complete. Until then, `outbox` stores
   replies in the portal without sending them.

Alternatives: `imap` + `smtp`, or have Power Automate forward new mail to `POST /api/intake/email`.

## Database: connect, create tables, verify

```powershell
cd backend
# 1. In backend\.env set, for example (database can be an existing one such as scribe_docs):
#    DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/scribe_docs
.venv\Scripts\python -m scripts.check_db --create   # connect, create the 18 tables + reference data, probe
.venv\Scripts\python -m scripts.check_db            # re-check any time (never changes data)
# 2. Restart the API, then run the workflow examples against it:
.venv\Scripts\python -m scripts.smoke_test --api http://127.0.0.1:8030
```

- `check_db` shows the server version, database, user and schema. It compares the expected tables and columns
  with what exists and runs a write/read probe that is rolled back. Connection failures are explained
  (wrong password, database missing, server not reachable).
- `--create` only adds missing tables or columns and never drops or changes existing data. Other tables already
  in the database are listed and left alone.
- `docs/schema_postgresql.sql` is the full PostgreSQL DDL, regenerated with `python -m scripts.export_schema`.
  Use it if a DBA prefers to create the tables by hand.
- `smoke_test` runs 11 real examples through the API (register, update, eligibility, select benefit,
  guarantor/PHI, email auto-book, email manual review, cancel, error handling). After each one it reports
  which database rows were written.

### Least-privilege database user

`python -m scripts.create_app_role` creates `docshealth_app` and gives it a random password. The role can:
- read and write the Docs Health tables;
- add rows to `audit_log`, but never update or delete them.

It has no CREATE, DROP or superuser rights. The script verifies this by logging in as the role, then writes
`DATABASE_URL` for the role and `MIGRATION_DATABASE_URL` for the admin account (used only for schema changes)
into `.env`. Re-running it rotates the password.

## Sending email (SMTP)

`app/services/mailer.py::send_mail()` never raises. It returns a `MailResult` with `ok`, `accepted`, `refused`,
`attempts`, `error_kind` and the server reply. It handles:
- address and header-injection validation;
- STARTTLS or SSL with certificate verification;
- retries with backoff for temporary failures only;
- clear diagnostics for Microsoft 365, such as "SMTP AUTH disabled" or "app password needed".

```powershell
.venv\Scripts\python -m scripts.configure_smtp            # prompts (password hidden), sends a test mail, saves only on success
.venv\Scripts\python -m scripts.configure_smtp --enable   # same, and switches EMAIL_OUTBOUND_MODE to smtp
.venv\Scripts\python -m scripts.configure_smtp --test-only --to someone@example.com
```

For Microsoft 365 mailboxes, a tenant admin must enable **Authenticated SMTP** for the sending mailbox.
With MFA turned on, use an app password.

## Microsoft 365 / Entra ID integration (Settings → Integrations)

Reads scheduling mailboxes and replies to patients **from the same mailbox, in the same mail thread** through
Microsoft Graph. Graph's `createReply` keeps the patient's subject, In-Reply-To/References and conversation.

1. Entra admin center → App registrations → New registration. Platform **Web**, redirect URI
   `{PORTAL_BASE_URL}/api/integrations/microsoft/callback`; locally that's
   `http://localhost:5181/api/integrations/microsoft/callback`. Create a client secret.
2. Grant API permissions on Microsoft Graph for one of the two modes:
   - **Delegated**: `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Mail.ReadWrite.Shared`, `Mail.Send.Shared`, plus
     `openid profile email offline_access`. Then **Connect with Microsoft** and sign in as the mailbox user, or as a
     user with access to the shared mailboxes.
   - **Application**: `Mail.ReadWrite`, `Mail.Send`. Then **Grant admin consent**, and restrict the app to the
     scheduling mailboxes with an Exchange application access policy.
3. Enter the tenant ID, client ID and secret on the page, connect, then add the **incoming mailboxes**. Each
   mailbox can be enabled or paused and has these settings: folder, process with AI, reply in-thread, mark as read,
   and default sender. Mailboxes are checked every N seconds (set on the page), or straight away with **Check now**.

The client secret and refresh token are stored encrypted in the database (`FIELD_ENCRYPTION_KEY`). Sign-in uses a
one-time state and PKCE. If Microsoft revokes the sign-in, the page shows "Sign-in expired: reconnect". A failed
in-thread reply is marked on the request and can be retried.

## Patient chart (EHR-style)

Open a patient to get a banner (name, MRN, DOB/age, primary payer, eligibility), an action toolbar
(Create Visit, Edit Patient Data, Insurance, Check Eligibility, Manage Attachments, Release of PHI, Email,
Face Sheet, Chart History) and a section menu:

| Section | What it holds |
|---|---|
| Patient Home | Face sheet summary (printable), primary insurance with the AI benefits summary, upcoming visits |
| Contact Information | Patient Data form: prefix/suffix/middle/preferred/previous name, test-patient flag, marital status, driver's license and SSN (masked; the eye icon reveals and **audits** the view), DOB, phones, email, address. "Show Amendments History" lists every field change |
| Guarantor | Financially responsible party (self or another person) |
| Patient Identity | Photo ID / insurance card images and attachments |
| Appointment History | All visits; cancel with confirmation |
| Release Of PHI | Authorized contacts with scope (appointments / billing / clinical), signed and expiry dates, revoke |
| Insurance | Primary / secondary / tertiary policies and archived ones |
| Portal Requests, Chart History | Email requests for this patient and the full audit history |

**Edit Primary Insurance** covers:
- Payer look-up by name or electronic payer ID, with financial category and plan-name look-up.
- Policy and group numbers, policy type, claims submission address, and notes (500 characters).
- Authorization requirements, and Archive / Archive & Create New Primary (the old policy is kept, read-only).
- Policy holder, and the editable eligibility values: co-pay, co-insurance, deductibles, out-of-pocket, and dates.

**Check Eligibility** sends a 270 by individual NPI (the chosen provider) or the practice group NPI
(`PRACTICE_GROUP_NPI`) with a date of service. The 271 is stored as:
- **Detailed View**: benefit lines by category, in/out of network, with a service-type filter and auth/cert flags.
- **Original Response**: the X12 271 text.
- **Discrepancies**: DOB, name, policy number, policy type, plan, dates and status compared with the chart.

**Select** next to each eligibility field copies a value from the latest response onto the policy, with an audit
entry. An **AI summary** gives front-desk staff the key facts in plain language. It is only accepted when every
dollar and percent figure matches the 271; otherwise a template is used.

SSN and driver's license numbers are encrypted at rest with `FIELD_ENCRYPTION_KEY`. Generate a key once and keep it
safe, because losing or rotating it makes stored values unreadable:

```bash
.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Demo: Avery Morgan (payer reports EPO but the chart says PPO) and Jordan Ellis (payer DOB differs) show discrepancies.
Linda Brooks shows inactive coverage.

## Password reset

**Forgot password?** on the sign-in page emails a single-use identity-verification link to the
account's registered address. The link expires after `PASSWORD_RESET_TTL_MINUTES` (default 30) and
opens a page where the user sets a new password. The policy is 10+ characters with upper- and
lower-case letters, a number and a symbol. The user then signs in with the new password.

- The response is identical whether or not the email has an account, so the page can't be used to discover accounts.
- Only a SHA-256 hash of the token is stored. A new request invalidates older links, and requests are
  limited to `PASSWORD_RESET_MAX_PER_WINDOW` per 15 minutes.
- A successful reset signs the user out of every other session and emails a "password changed" notice.
- The link points at `PORTAL_BASE_URL` (default `http://localhost:5181`). Set it to the public portal address.
- Delivery uses the same outbound mode as patient replies. In `outbox` mode nothing is sent. The email is
  saved in the Email log and as an `.eml` file in `backend\data\maildrop\` that opens in Outlook, so a reset
  can be completed locally. Set `EMAIL_OUTBOUND_MODE=graph` or `smtp` so real inboxes receive the link.

## Error handling

- API: database outages return 503, conflicts return 409, and validation errors return 422. Any unexpected
  error returns a 500 with a reference ID that is logged with the full traceback, and internal details are
  never shown. Email-delivery failures are recorded on the message (`send_status=failed`) instead of
  failing the request, and one bad email never stops mailbox polling.
- Portal: network failures show a clear message, and a signed-out or revoked session returns to sign-in.
  A screen that crashes shows a recoverable panel instead of a blank page.

## Automation policy (`backend\.env`)

| Setting | Default | Effect |
|---|---|---|
| `AI_CONFIDENCE_THRESHOLD` | 0.80 | Below this, the request goes to review (`LOW_CONFIDENCE`) |
| `MATCH_CONFIDENCE_THRESHOLD` | 0.80 | Minimum patient-match score, which must also lead the runner-up by 0.2 |
| `AUTO_BOOK_APPOINTMENTS` | true | If false, the AI offers slots instead of booking |
| `AUTO_SEND_RESPONSES` | true | If false, replies are drafted but not sent |

## Project structure

```
backend/
  app/
    api/            REST routes (requests, patients, scheduling, system)
    llm/            provider adapters (mock, openai, azure_openai, gemini, huggingface)
    services/       extraction, matching, eligibility, scheduling, responder,
                    email_gateway, pipeline, registration, audit
    models.py       PostgreSQL schema (SQLAlchemy)
    seed.py         payers, providers, network contracts, schedules, demo patients
    demo_samples.py 11 sample emails, one per PRD path
  scripts/          bootstrap_db, init_db, run_demo, poll_mailbox
  tests/            extraction unit tests + end-to-end API workflow tests
frontend/
  server.js         Node.js/Express production server + /api proxy
  src/styles/       design tokens (colors, typography, spacing, shape) from the Iyra UI kit
  src/components/   ui/ (Button, Card, Badge, Field, Overlay, DataViz, Page) + layout/PortalShell
  src/lib/          api client + hooks, auth, status/format helpers
  src/pages/        Login, Dashboard, Requests, RequestDetail, Patients, PatientChart,
                    RegisterPatient, Schedule, EmailLog, AuditTrail, DemoLab, Settings
docs/               PRD, architecture, traceability, validation plan
```

## API overview

All endpoints except `/api/auth/login` and `/api/health` need `Authorization: Bearer <token>`.
Full interactive docs are at `http://127.0.0.1:8000/docs`.

| Method & path | Purpose |
|---|---|
| `POST /api/intake/email` | Attach an email to the AI app (creates and processes a portal request) |
| `POST /api/intake/poll` | Poll the Outlook/IMAP mailbox now |
| `GET /api/requests?status=&q=&reason=` | Request inbox |
| `GET /api/requests/{id}` | Full request: email, extraction, match, eligibility, replies, timeline |
| `POST /api/requests/{id}/reprocess` · `/assign` · `/link-patient` · `/create-patient` · `/book` · `/send-response` · `/resolve` · `/reopen` | Review actions |
| `GET /api/requests/{id}/slots` | Suggested openings for the request |
| `GET/POST /api/patients`, `GET/PATCH /api/patients/{id}` | Search, register, view and update a chart |
| `POST /api/patients/{id}/insurance` · `/eligibility` · `/documents` | Insurance, eligibility check, photo ID / card upload |
| `GET /api/providers`, `GET /api/providers/{id}/slots?day=` , `GET /api/payers` | Reference data and availability |
| `GET/POST /api/appointments`, `POST /api/appointments/{id}/cancel` | Schedule module |
| `GET /api/dashboard/summary` · `GET /api/audit` · `GET /api/emails` | Reporting, audit trail, email log |

## Demo data

All demo patients are fictional and use `example.com` addresses. The mock clearinghouse
returns *inactive* for member IDs starting with `INV` and a payer error for IDs starting
with `ERR`. Dr. Patel is not in network with Humana, so the out-of-network path can be shown.
