# Chart add-ons: header data, alerts, tasks, medications, orders, consents, intramail

Backend, database logic and UI for the patient-chart functions shown in the reference screenshot that the project did
not yet have. Interactive API docs (OpenAPI) are served at `/docs` on the API port.

## Setup

```powershell
cd backend
.venv\Scripts\python -m alembic upgrade head        # tables, triggers and procedures (see docs/DATABASE.md)
```

* New tables come from `app/models_chart.py` and Alembic revision `0001`. Revision `0002` applies
  `alembic/sql/chart_addons.sql` (triggers and stored procedures). To change a trigger, add a new revision (`CREATE OR REPLACE`).
* The triggers and procedures need PostgreSQL, which is the only supported database.
* The API connects with `application_name = docshealth-api`. That is how `chart_audit_direct` tells API writes (audited by the
  application) from direct SQL writes (audited by the trigger).
* No new environment variables. Credentials stay in `DATABASE_URL` / `MIGRATION_DATABASE_URL` in `backend/.env`.

## Schema

All patient-owned tables cascade on patient delete. `created_at` / `updated_at` have database defaults, so direct SQL
inserts work.

| Table | Purpose | Key constraints |
|---|---|---|
| `patients` (+3 columns) | `pcp_name`, `pms_id`, `portal_status` (inactive / invited / active) | portal status validated by the API |
| `patient_alerts` | Header "Alerts" | severity in info / warning / critical |
| `patient_allergies` | Header "Allergies" | severity mild / moderate / severe; status active / inactive |
| `patient_notes` | Notes and sticky notes | kind sticky / general / clinical |
| `patient_reminders` | Add Reminder | status open / done / cancelled |
| `patient_flags` | Add a Patient Flag | |
| `tasks` | Create Task (patient-linked or general) | status, priority checks; assignee FK to `users` (SET NULL) |
| `patient_links` | Linked Accounts, one row per direction | unique pair; `patient_id <> linked_patient_id` |
| `medications` | Patient Rx (a medication list, not e-prescribing) | status active / stopped / completed; quantity, refills >= 0 |
| `drug_interaction_rules` | Practice-maintained rules for drug warnings | unique pair; severity minor / moderate / major |
| `clinical_orders` | New Non-Visit Order | type lab / imaging / referral / other; status draft / ordered / completed / cancelled |
| `procedure_logs` | Procedure Logs | CPT 5 chars; units >= 1; optional appointment and provider FKs |
| `consent_templates` | Consent forms (versioned) | unique name |
| `patient_consents` | Create Consents | status pending / signed / revoked; template FK RESTRICT; keeps `template_version` |
| `intramail` | Send Intramail | sender / recipient FKs; per-side soft delete |

## Triggers (alembic/sql/chart_addons.sql)

| Trigger | On | Fires | What it does |
|---|---|---|---|
| `trg_touch` | 12 chart tables | BEFORE UPDATE | Sets `updated_at = now()` |
| `trg_audit_direct` | 11 chart tables | AFTER INSERT / UPDATE / DELETE | Writes an `audit_log` row for writes that did **not** come from the API (`application_name <> docshealth-api`) |
| `trg_task_rules` | `tasks` | BEFORE INSERT / UPDATE | Allows open to in_progress to done, open or in_progress to cancelled, in_progress to open; done and cancelled are final. Sets `completed_at` when done, clears it otherwise |
| `trg_reminder_rules` | `patient_reminders` | BEFORE INSERT / UPDATE | open to done or cancelled only; maintains `completed_at` |
| `trg_order_rules` | `clinical_orders` | BEFORE INSERT / UPDATE | draft to ordered or cancelled; ordered to completed or cancelled. Defaults `ordered_on` / `completed_on` to today |
| `trg_medication_rules` | `medications` | BEFORE INSERT / UPDATE | active to stopped or completed; stopped to active; completed is final. Sets `stop_on` when leaving active, clears it on restart. Rejects stop before start |
| `trg_consent_rules` | `patient_consents` | BEFORE INSERT / UPDATE | On insert copies the template's version. pending to signed or revoked, signed to revoked, revoked is final. Signing needs a signer name and defaults `signed_on`; revoking sets `revoked_at` |
| `trg_rule_normalize` | `drug_interaction_rules` | BEFORE INSERT / UPDATE | Trims and lower-cases both drugs, orders the pair alphabetically, rejects two identical drugs |
| `trg_link_mirror` | `patient_links` | AFTER INSERT / DELETE | Inserts or deletes the opposite-direction row |

A refused change raises `check_violation` (SQLSTATE 23514). The API turns that into a `409` with the trigger's message.

## Stored procedures and functions

| Name | Kind | Used by | Purpose |
|---|---|---|---|
| `link_patients(patient, other, relationship, actor)` | procedure | `POST /patients/{id}/links` | Validates both charts, rejects self-links, inserts both directions, updates the label if the link exists |
| `unlink_patients(patient, other)` | procedure | `DELETE /patients/{id}/links/{other}` | Removes both directions |
| `patient_clipboard(patient)` | function (jsonb) | `GET /patients/{id}/clipboard` | Alerts, allergies, flags, sticky notes, active medications and open-work counts in one call |
| `overdue_work_report(as_of)` | function (table) | `GET /reports/overdue-work` | Overdue tasks per assignee plus unassigned overdue reminders |

## Roles

Two roles exist: `admin` and `scheduler`. Reads need any signed-in account. Writes:

| Resource | Who may write | Delete |
|---|---|---|
| Alerts, notes, reminders, flags, tasks, linked accounts, consents | admin, scheduler | alerts, notes, reminders, flags, tasks, links only |
| Allergies, medications, orders, procedure logs | admin | never (change the status instead) |
| Drug rules, consent templates, overdue report | admin | not offered |
| Tasks | any user may create; only the assignee, creator or an admin may change or delete | |
| Intramail | sender and recipient only; others get 404 | per-side |

Medications, orders, consents and procedure logs cannot be deleted because they are part of the clinical record.

## Endpoints

Lists accept `limit` (1-200, default 50), `offset`, `sort` (`field` or `-field`), `status` and `q` (text search).
List responses: `{"items": [...], "total": 12, "limit": 50, "offset": 0}`.

| Method and path | Purpose |
|---|---|
| `GET/PATCH /api/patients/{id}/header` | PCP, PMS id, portal status, alerts, allergies, flags (shows "Unspecified" when empty) |
| `GET /api/patients/{id}/clipboard` | Patient Clipboard summary |
| `GET /api/patients/{id}/drug-warnings` | Allergy and interaction warnings for active medications |
| `GET/POST /api/patients/{id}/{alerts,allergies,notes,reminders,flags,medications,orders,procedures,consents}` | List and create |
| `PATCH/DELETE /api/patients/{id}/{resource}/{item}` | Update (including status moves) and delete where allowed |
| `GET/POST /api/tasks`, `PATCH/DELETE /api/tasks/{id}` | Tasks, with `mine`, `patient_id`, `overdue` filters |
| `GET/POST /api/patients/{id}/links`, `DELETE .../links/{other}` | Linked accounts |
| `GET/POST/PUT /api/drug-rules` | Interaction rules (admin writes) |
| `GET/POST/PUT /api/consent-templates` | Consent forms (admin writes) |
| `GET/POST /api/intramail`, `GET/DELETE /api/intramail/{id}`, `GET /api/intramail/recipients` | Internal messages |
| `GET /api/reports/overdue-work` | Overdue work per person (admin) |

Status codes: `201` created, `204` deleted, `401` not signed in, `403` wrong role, `404` not found (also for another
person's message), `409` invalid status move or retained record, `422` invalid input.

### Samples

```http
POST /api/patients/2/medications            (admin)
{"drug_name": "Penicillin VK", "strength": "500 mg", "frequency": "4x daily"}
-> 201 {"id": 2, "status": "active", "stop_on": null, "warnings": [
     {"type": "allergy", "severity": "major", "drug": "Penicillin VK",
      "message": "Patient has an active allergy to Penicillin (Hives)"}]}

PATCH /api/tasks/7   {"status": "done"}      -> 200 {"status": "done", "completed_at": "2026-11-11T17:02:11Z", ...}
PATCH /api/tasks/7   {"status": "open"}      -> 409 {"detail": "Cannot change status from done to open"}

POST /api/patients/2/orders  {"order_type": "lab", "description": "CBC"}
PATCH /api/patients/2/orders/1 {"status": "completed"}
                                             -> 409 {"detail": "Cannot change status from draft to completed"}

GET /api/patients/2/header
-> {"pcp_name": null, "pms_id": null, "portal_status": "inactive",
    "alerts": ["Needs interpreter"], "allergies": ["Penicillin"], "flags": [], "sticky_notes": 0}
```

## Drug warnings: what they are and are not

No clinical data ships with the app. Warnings come from (a) the patient's own active allergies matched against active
medication names, and (b) rules an administrator enters under **Settings > Clinical reference** (or loads from a licensed
drug database). The warnings panel says how many rules are loaded; with none, only allergy matches are checked. This is
a practice aid, not a substitute for a pharmacist's or a licensed database's check.

## Tests

| File | Covers |
|---|---|
| `tests/test_chart_extras.py` | 23 API and business-rule tests: roles, validation, status moves, pagination and sorting, links, warnings, consents, intramail privacy, audit text, cascade delete |
| `tests/test_chart_pg.py` | 19 PostgreSQL tests of every trigger and procedure with raw SQL |

Run them with `DOCSHEALTH_TEST_DATABASE_URL` set to a disposable PostgreSQL database (see `docs/DATABASE.md`):

```powershell
.venv\Scripts\python -m pytest tests/test_chart_extras.py tests/test_chart_pg.py
```

## Not built (needs your input or an outside service)

The screenshot also shows these. They are not implemented because they need a definition from you or a third-party
service, and nothing was mocked:

* **Fax, Send DirectMail** (fax and Direct secure-messaging vendors), **real e-prescribing** (a vendor such as Surescripts),
  a **licensed drug database** feed.
* **Financials, Case Management, Practice Data, Portal Settings, Patient Representative Portal, synApps, CCD,
  Call Buttons, Select Room, Enable Kiosk, EMA Photos, PDF Manager**: no specification yet.
  (Photos and PDFs can already be stored under Manage Attachments.)
