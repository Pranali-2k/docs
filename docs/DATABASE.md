# Database: PostgreSQL schema, migrations, triggers and procedures

PostgreSQL is the only supported database. Schema changes are Alembic revisions in `backend/alembic/versions`. The API
never creates or alters tables: at start-up it checks that the database is at the latest revision and refuses to start
(with the command to run) if it is not.

## Setup

```powershell
cd backend
# 1. DATABASE_URL (and optionally MIGRATION_DATABASE_URL, an account allowed to create tables) in backend\.env
#    DATABASE_URL=postgresql+psycopg://docshealth_app:<password>@localhost:5432/docshealth
.venv\Scripts\python -m alembic upgrade head       # schema, constraints, triggers, procedures
.venv\Scripts\python -m scripts.init_db --demo     # reference data, demo users and fictional patients (optional)
```

| Command | Purpose |
|---|---|
| `python -m alembic upgrade head` / `downgrade -1` | Apply or undo revisions |
| `python -m alembic current` / `history` | Where the database is |
| `python -m alembic check` | Fails if the models and the migrations disagree (run it before every commit that touches `models.py`) |
| `python -m alembic revision --autogenerate -m "..."` | Draft a revision from model changes, then review it |
| `python -m scripts.check_db` | Connection, schema and read/write probe |
| `python -m scripts.export_schema` | Regenerate `docs/schema_postgresql.sql` (reference DDL) |

An existing database created before Alembic: `python -m alembic stamp 0001`, then `upgrade head`.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://localhost:5432/docshealth` | Runtime connection. Use a least-privilege role (`python -m scripts.create_app_role`). No password is ever stored in code. |
| `MIGRATION_DATABASE_URL` | empty | Used by Alembic instead of `DATABASE_URL` when set (owner role) |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 10 / 10 | Connection pool |
| `DB_POOL_RECYCLE_SECONDS` / `DB_POOL_TIMEOUT_SECONDS` | 1800 / 30 | Recycle idle connections; wait for a free one |
| `DB_STATEMENT_TIMEOUT_MS` | 30000 | Server-side limit per statement |

Every connection reports `application_name = docshealth-api`. Every transaction also carries the signed-in person's
email as the setting `app.actor`, which triggers read through `app_actor()` to fill `created_by` / `updated_by`.
Background jobs and direct SQL have no actor and read as `system`.

## Revisions

| Revision | Content |
|---|---|
| `0001` | Baseline: all 38 tables, indexes, foreign keys (generated from the models) |
| `0002` | Chart add-on triggers and procedures (`alembic/sql/chart_addons.sql`) |
| `0003` | `created_by`, `updated_by`, `updated_at` on the core tables; the `record_history` table |
| `0004` | Core database logic (`alembic/sql/core_logic.sql`): stamping and history triggers, integrity rules, scheduling and request procedures, dashboard and report functions |
| `0005` | Insurance policy rules |

## Relationships

```mermaid
erDiagram
    patients ||--o{ insurance_policies : has
    patients ||--o| guarantors : has
    patients ||--o{ phi_releases : authorizes
    patients ||--o{ appointments : books
    patients ||--o{ patient_documents : owns
    patients ||--o{ portal_requests : raises
    patients ||--o{ patient_links : links
    patients ||--o{ medications : takes
    payers ||--o{ insurance_policies : covers
    payers ||--o{ provider_networks : contracts
    providers ||--o{ provider_networks : in_network
    providers ||--o{ provider_availability : works
    providers ||--o{ provider_blocks : blocks
    providers ||--o{ appointments : sees
    portal_requests ||--o| email_messages : came_from
    portal_requests ||--o{ appointments : produces
    insurance_policies ||--o{ eligibility_checks : verified_by
    eligibility_checks ||--o{ eligibility_benefits : returns
    users ||--o{ tasks : assigned
    users ||--o{ intramail : sends
    integration_connections ||--o{ integration_mailboxes : reads
```

All patient-owned tables cascade on patient delete. The full DDL is in `docs/schema_postgresql.sql`; the chart add-on
tables are described in `docs/CHART_ADDONS.md`.

## Triggers (core tables)

Applied to `patients`, `insurance_policies`, `guarantors`, `phi_releases`, `appointments`, `portal_requests`, `providers`
and `provider_blocks` unless stated.

| Trigger | Event | Purpose |
|---|---|---|
| `trg_core_stamp` (`core_stamp()`) | BEFORE INSERT / UPDATE | Sets `created_by` (insert only, unless given), `updated_by` and `updated_at` from `app.actor` and `now()`. One function serves every table (`jsonb_populate_record` ignores columns a table lacks). |
| `trg_core_history` (`core_history()`) | AFTER INSERT / UPDATE / DELETE | Writes field-level changes to `record_history`: for updates only the columns that changed (`from` / `to`), for deletes the old row. Columns ending `_encrypted` are recorded as `{"changed": true}` with no value; `updated_*`, `extracted`, `match_details`, `draft_chart` are skipped. It writes to a table without triggers, so it cannot recurse, and an update that changes nothing visible writes no row. This is separate from `audit_log` (the readable action trail written by procedures and the application). |
| `trg_request_status` (`request_status_rules()`) | BEFORE UPDATE OF status on `portal_requests` | A resolved or closed request can only be reopened (to `needs_review`). Keeps `resolved_at` set for final states and empty otherwise. |
| `trg_policy_rules` (`policy_rules()`) | BEFORE INSERT / UPDATE on `insurance_policies` | `is_primary` follows `coverage_order`; an archived policy is never primary and gets `archived_at` / `archived_by`. |

Constraints that act as database rules: no overlapping booked appointments per provider and per patient
(`ex_appt_provider_overlap`, `ex_appt_patient_overlap`, exclusion constraints on `tsrange`, which need `btree_gist`),
`ck_appt_range` (end after start), `ck_appt_status`, `ck_request_status`, and the unique index `uq_policy_active_order`
(one active policy per patient and coverage order). The chart add-on triggers are listed in `docs/CHART_ADDONS.md`.

## Procedures and functions (core)

Rule violations raise `RAISE EXCEPTION` (SQLSTATE P0001) with a message fit to show staff. The Python layer calls them
inside a savepoint (`app/services/db_procs.py`) and turns a violation into a 409 or a route-to-review.

| Name | Used by | What it does |
|---|---|---|
| `book_appointment(patient, provider, start, reason, request, actor, minutes)` | `scheduling.book`, `POST /api/appointments`, the intake pipeline, staff booking | Validates patient and provider, takes the slot length from the provider's weekly template (30 min default), refuses blocked time, provider conflicts and patient conflicts, inserts the visit and writes the audit rows. Returns the new id. |
| `cancel_appointment(appointment, actor, request)` | `scheduling.cancel`, `POST /api/appointments/{id}/cancel` | Locks the visit, refuses anything not `booked`, cancels, audits. |
| `reschedule_appointment(old, provider, start, reason, request, actor)` | pipeline (email reschedules), staff booking with `cancel_appointment_id` | Cancels the old visit and books the new one in one transaction. If the new time is unavailable nothing changes. Returns the new id. |
| `assign_request(request, assignee, actor)` | `POST /api/requests/{id}/assign` | Sets the assignee (default: the actor); `needs_review` becomes `in_review`; audits. |
| `resolve_request(request, note, close, actor)` | resolve, send-and-resolve, book-and-resolve, bulk actions | Requires a note; sets `resolved` or `closed`, who and when; audits. |
| `reopen_request(request, actor)` | `POST /api/requests/{id}/reopen` | Back to `needs_review`, clears `resolved_at`; audits. |
| `dashboard_summary(days, clinic_today)` | `GET /api/dashboard/summary` | All dashboard figures for the window as one JSON value. |
| `request_turnaround_report(days)` | `GET /api/reports/turnaround` | Per request type: volume, automated, in review, resolved, average first-response and resolution minutes. |

## New endpoints in this layer

| Method and path | Role | Purpose |
|---|---|---|
| `GET /api/reports/turnaround?days=30` | admin | Turnaround report |
| `GET /api/history/{table}/{id}` | admin | Field-level change history of a core record (tables listed above) |

```http
GET /api/history/patients/2
-> [{"id": 14, "operation": "update", "changed_by": "scheduler@docshealth.local",
     "changed_at": "2026-11-12T08:41:07+00:00",
     "changes": {"city": {"from": null, "to": "Fresno"}, "ssn_encrypted": {"changed": true}}}]

GET /api/reports/turnaround?days=30
-> [{"intent": "new_appointment", "total": 5, "auto_responded": 1, "needs_review": 4, "resolved": 1,
     "avg_minutes_to_first_response": 12.5, "avg_minutes_to_resolve": 45.0}]
```

## What stays in Python, and why

Extraction (LLM calls), patient matching, eligibility calls, Microsoft Graph / SMTP email, OCR and the field encryption of
SSN and driver's licence numbers need outside services or Python libraries, so they are not in the database. Patient
registration and policy editing stay in Python for the same reason (encryption, validation messages); the database
backs them with the triggers and the unique-policy rule above.

## Tests

The tests need a disposable PostgreSQL database, named with "test" or "scratch" because the schema is dropped:

```powershell
set DOCSHEALTH_TEST_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1:5432/docshealth_test
.venv\Scripts\python -m pytest                       # whole suite (about 20 minutes)
.venv\Scripts\python -m pytest tests/test_core_db.py # triggers and procedures with raw SQL (26 tests, seconds)
```

The schema is built once per run from the Alembic revisions, so the tests exercise exactly what production gets; each test
then starts from empty tables plus the demo seed. `tests/test_core_db.py` covers every core trigger, constraint, procedure
and function; `tests/test_core_api.py` covers them through the API, including role limits and two people booking the same
slot at once; `tests/test_chart_pg.py` and `tests/test_chart_extras.py` cover the chart add-ons.
