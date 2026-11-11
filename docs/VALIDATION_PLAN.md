# Validation & demo plan (pre-production)

This plan covers the PRD's "Future Validation" step: demonstrate the platform to
scheduling staff before rollout, then validate response quality, workflow
accuracy and user adoption.

## Phase 1: Scripted demo (1 session, about 60 min)

Setup: `EMAIL_OUTBOUND_MODE=outbox` (nothing is emailed), demo seed data, and the **Demo lab** page.

| # | Scenario (Demo lab card) | Expected outcome | Staff task |
|---|---|---|---|
| 1 | Existing patient books a physical | Auto-booked, confirmation stored, resolved | Open the request; check the stepper, eligibility and reply |
| 2 | Reschedule an existing appointment | Old one cancelled, new one booked | Verify on the Schedule and the chart |
| 3 | Cancel an appointment | Cancelled, resolved | Verify the chart history |
| 4 | New patient (no chart) | Review: *Unmatched patient* | Click **Register & re-run AI**; confirm it books |
| 5 | Insurance eligibility fails | Review: *Eligibility failed* | Call the patient (simulated); edit the draft; resolve |
| 6 | Provider out of network | Review: *Out of network* | Book an in-network provider with **Book & confirm** |
| 7 | Insurance conflicts with chart | Review: *Conflicting information* | Update the policy on the chart, then **Re-run AI analysis** |
| 8 | Twins on one family account | Review: *Ambiguous match* | Link the right chart |
| 9 | Emergency symptoms | Review: *Clinical red flag*, high priority | Follow the clinical escalation SOP |
| 10 | General question | Review: *Not a scheduling action* | Answer using the draft |
| 11 | Incomplete request | Review: *Incomplete data* | Reply asking for the missing details |

Run `python -m scripts.run_demo` beforehand to confirm every scenario matches its expected outcome.

## Phase 2: Shadow mode (2 to 4 weeks)

* Connect the real scheduling mailbox with `EMAIL_INBOUND_MODE=graph`, but keep
  `AUTO_SEND_RESPONSES=false` and `AUTO_BOOK_APPOINTMENTS=false`.
* Staff keep working as today. Each day, reviewers compare the AI's intent, match,
  eligibility result and draft reply against what staff actually did.
* Record disagreements with a resolution note. The dashboard's trigger breakdown shows where
  thresholds need tuning.

## Phase 3: Graduated automation

1. Turn on auto-send for **cancellations** only.
2. Then turn on **reschedules** and **existing-patient bookings**.
3. Keep new patients, insurance changes and every review trigger with staff.

## Acceptance metrics

| Metric | Target before go-live | Source |
|---|---|---|
| Intent accuracy | ≥ 95% on shadow sample | Reviewer spot checks |
| Patient match precision (auto-matched correct chart) | 100% (zero wrong-patient actions) | Reviewer spot checks, audit trail |
| Auto-response factual accuracy | 100% (template facts) | Outbox review |
| Review-trigger recall on unsafe requests | 100% of red-flag / eligibility-failed cases routed to staff | Dashboard + spot checks |
| Median time to first response | < 15 min for auto, < 4 business hours for review | Dashboard |
| Staff adoption | ≥ 80% of scheduling emails worked in the portal | Dashboard volume vs mailbox volume |
| Staff satisfaction | ≥ 4/5 in post-pilot survey | Survey |

## Sign-off

Scheduling lead · Practice manager · Compliance / privacy officer (BAA with any hosted LLM vendor, PHI handling) · IT (Entra app registration and mailbox access policy).
