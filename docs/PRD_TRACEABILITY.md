# PRD → Implementation traceability

Source: *Docs Health Scheduling & Email Automation – PRD Workflow* (`docs/DocsHealth_PRD_Workflow.pdf`).

| PRD section | Requirement | Where it is implemented |
|---|---|---|
| Objective | AI-enabled scheduling integrated with email, patient records, insurance verification and scheduling | Whole system; orchestration in `backend/app/services/pipeline.py` |
| Current workflow | Staff work in **Patient Charts** and **Schedule** modules | UI: `Patient charts` (`PatientChart.tsx`), `Schedule` (`Schedule.tsx`) |
| Scheduling workflow 1 | Receive scheduling request | `email_gateway.ingest_email`, `POST /api/intake/email`, Graph/IMAP polling |
| 2 | Identify / create patient | `matching.match_patient`, `POST /api/requests/{id}/link-patient`, `…/create-patient`, `POST /api/patients` |
| 3 | Enter demographics | Registration form, `PATCH /api/patients/{id}` (audited field-level changes) |
| 4 | Capture insurance | `POST /api/patients/{id}/insurance`, insurance section of the registration form |
| 5 | Run eligibility verification | `eligibility.run_eligibility`, `POST /api/patients/{id}/eligibility`, and automatically in the pipeline |
| 6 | Confirm network eligibility | `provider_networks` table, `eligibility.is_in_network`, `OUT_OF_NETWORK` trigger |
| 7 | Schedule appointment | `scheduling.find_slots` / `book` (double-booking guards), auto-booking or staff "Book & confirm" |
| 8 | Update patient chart | Appointments, eligibility history and `chart_updated` audit events on the chart |
| Patient registration | Contact info, demographics, insurance, photo/ID collection, chart maintenance | `registration.py`; document upload (photo ID, card front/back, PDF/images ≤10 MB) |
| Insurance verification | Store primary payer, run checks, review responses, validate doctor-network compatibility | `insurance_policies` (primary/active), `eligibility_checks` (raw 271-style response kept), network check per provider |
| AI email automation | Attach emails to AI app; convert to portal requests; match patient automatically; generate AI responses when confidence and matching criteria are met | Intake → `PortalRequest`; `extraction.py` (LLM); `matching.py`; `responder.py`; thresholds `AI_CONFIDENCE_THRESHOLD`, `MATCH_CONFIDENCE_THRESHOLD` |
| Manual review triggers | Unmatched patient | `UNMATCHED_PATIENT` |
| | Incomplete data | `INCOMPLETE_DATA` (name plus a second identifier), `NO_INSURANCE_ON_FILE`, `PROVIDER_NOT_FOUND`, `APPOINTMENT_NOT_FOUND` |
| | Conflicting information | `CONFLICTING_INFO` (DOB, name, payer or member ID vs chart), `AMBIGUOUS_MATCH`, `INSURANCE_CHANGE` |
| | Failed eligibility verification | `ELIGIBILITY_FAILED`, `OUT_OF_NETWORK` |
| | Low-confidence AI decisions | `LOW_CONFIDENCE`, `AI_ENGINE_ERROR`, `UNSUPPORTED_INTENT` |
| | *(added for patient safety)* | `CLINICAL_RED_FLAG`, `NO_AVAILABILITY`, `SCHEDULING_ERROR` |
| Portal workflow | Email → Intake → Patient Match → AI Analysis → Auto Response **or** Manual Review → Resolution Tracking | Pipeline stepper on the request page; statuses `auto_responded`/`needs_review`/`in_review`/`resolved`/`closed`; `resolved_by`, `resolved_at`, `first_response_at` |
| Key features | Centralized request management | Request inbox (tabs, search, trigger filter), assignment, reprocess, reopen |
| | Outlook integration | `EMAIL_INBOUND_MODE=graph` (Microsoft Graph) / `imap`; `EMAIL_OUTBOUND_MODE=graph` / `smtp` |
| | Automated responses | Auto-send for clean requests; editable AI drafts for reviewed ones |
| | Insurance validation assistance | Eligibility results and network status inline on the request and the chart |
| | Audit trail | `audit_log` table, Audit trail page, per-request timeline, per-chart history |
| | Dashboard reporting | `GET /api/dashboard/summary`: volume, auto-response rate, review triggers, response and resolution times |
| Future validation | Demos with scheduling staff; validate response quality, workflow accuracy and adoption | Demo lab (11 scripted scenarios with expected outcomes), `scripts/run_demo.py`, `docs/VALIDATION_PLAN.md` |
