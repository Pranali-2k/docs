# Architecture

```
 Outlook / M365 mailbox ──(Graph poll | IMAP | webhook relay)──┐
                                                               ▼
┌───────────────────────── Python backend (FastAPI, :8000) ─────────────────────────┐
│ email_gateway.ingest_email ─► PortalRequest (status=received)                      │
│        │                                                                           │
│        ▼  pipeline.process_request                                                 │
│  1. AI analysis      extraction.extract_request ── LLM provider (mock/OpenAI/Azure/  │
│                                                    Gemini/HuggingFace) + rule engine │
│  2. Patient match    matching.match_patient (deterministic scoring, never the LLM)  │
│  3. Chart checks     insurance on file? email vs chart conflicts? provider?         │
│  4. Eligibility      eligibility.run_eligibility (clearinghouse adapter, 270/271)   │
│  5. Network          ProviderNetwork (provider × payer)                              │
│  6. Availability     scheduling.find_slots                                           │
│  7. Decision         any review trigger? ── yes ─► NEEDS_REVIEW + AI draft reply    │
│                                        └─ no ──► book/cancel ► templated reply ►    │
│                                                  send ► RESOLVED / AUTO_RESPONDED   │
│  Every step ► audit_log                                                              │
└──────────────────────────────────────┬──────────────────────────────────────────────┘
                                       │ PostgreSQL (SQLAlchemy 2)
┌──────────── Node.js frontend (Express :3000 serving React/Vite build) ─────────────┐
│ Dashboard · Request inbox · Review workspace · Patient charts · Registration ·      │
│ Schedule · Email log · Audit trail · Demo lab · Settings      (/api proxied)        │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

## Backend layout (`backend/app`)

| Module | Responsibility |
|---|---|
| `config.py` | All settings from env / `.env` (LLM, email, automation thresholds) |
| `db.py`, `models.py` | SQLAlchemy engine (pooled PostgreSQL), ORM models and Alembic helpers. Business rules that must hold for every caller live in PostgreSQL procedures and triggers: see `docs/DATABASE.md` |
| `llm/providers.py` | `LLMProvider` interface with `mock`, `openai`, `azure_openai`, `gemini` and `huggingface` adapters (plain HTTPS, no vendor SDK lock-in) |
| `services/extraction.py` | Email to `ExtractedRequest` (strict JSON contract, normalization, rule-engine fallback) |
| `services/matching.py` | Candidate search and weighted evidence scoring (DOB, name, email, phone, member ID); conflict detection |
| `services/eligibility.py` | Clearinghouse adapter (`mock` deterministic; `x12` integration point) and network check |
| `services/scheduling.py` | Slot generation from weekly templates, double-booking guards, book/cancel |
| `services/responder.py` | Vetted reply templates; optional LLM polish accepted only if every fact survives |
| `services/email_gateway.py` | Intake (idempotent on Message-ID), Graph/IMAP polling, Graph/SMTP/outbox sending |
| `services/pipeline.py` | Orchestrates the PRD workflow and the manual-review rules |
| `services/registration.py` | Patient registration, duplicate guard, insurance policies, document/image storage |
| `services/audit.py` | Append-only audit trail |
| `api/*` | REST endpoints (JWT-protected) |

## Data model

`users` · `payers` · `providers` · `provider_networks` (provider × payer in-network) ·
`provider_availability` (weekly template) · `patients` · `insurance_policies` ·
`eligibility_checks` · `patient_documents` · `appointments` · `email_messages`
(inbound + outbound) · `portal_requests` · `audit_log`.

`portal_requests` stores the whole AI decision record: `extracted` (JSON),
`match_details` (candidates, scores, evidence, conflicts), `ai_confidence`,
`match_confidence`, `decision`, `review_reasons`, `draft_response`, the linked
`appointment_id`, and the resolution fields (`resolved_by`, `resolved_at`, `resolution_note`).

## Safety design

* **The model never decides identity, coverage or availability.** It only structures the email.
  Matching, eligibility, network and slot checks are deterministic code.
* **Any doubt goes to a person.** Low confidence, a missing field, a conflict, a failed check,
  a model error or an emergency keyword each stop the automation before it books or sends anything.
* **Replies can't hallucinate.** Auto replies are templates filled from database facts. An LLM
  rewrite is used only if all dates, times and names appear verbatim in it.
* **Emergency keywords** (chest pain, trouble breathing, stroke signs, suicidal ideation, and so on)
  mark the request high priority and draft a "call 911" safety reply for staff.
* **PHI:** the default `mock` engine keeps everything on-premises. For a hosted LLM, use a vendor
  that signs a BAA (for example Azure OpenAI in your tenant).
* **Outbox mode** stores replies without sending them, which is safe for demos and staff validation.
* **Auth:** JWT bearer tokens, PBKDF2-SHA256 password hashes, and admin/scheduler roles.

## Request status lifecycle

`received → processing → auto_responded | needs_review → in_review → resolved | closed`
(`reopen` returns a resolved request to `needs_review`).

## Integrations to finish for production

| Area | Current | Production step |
|---|---|---|
| Outlook inbound | Graph client-credentials polling / IMAP / webhook endpoint | Register an Entra app with `Mail.ReadWrite` + `Mail.Send` (application), scoped with an application access policy to the scheduling mailbox |
| Eligibility | Deterministic mock clearinghouse | Implement `X12ClearinghouseAdapter.check` for your vendor (Availity, Waystar, Change/Optum) |
| EHR/PM | Built-in patient chart and schedule | Sync to the practice-management system via FHIR `Patient`/`Coverage`/`Appointment` if needed |
| Migrations | `create_all` | Add Alembic before the first schema change in production |
