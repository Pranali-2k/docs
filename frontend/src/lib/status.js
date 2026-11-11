/**
 * Presentation rules for Docs Health backend values (request status, review
 * triggers, coverage, confidence). Kept in one place so every screen agrees.
 * Variants map onto the UI kit Badge: neutral | success | warning | error | info.
 */

export const REQUEST_STATUS = {
  received: { label: 'Received', variant: 'neutral' },
  processing: { label: 'Processing', variant: 'info' },
  auto_responded: { label: 'Auto-responded', variant: 'info' },
  needs_review: { label: 'Needs review', variant: 'warning' },
  in_review: { label: 'In review', variant: 'info' },
  resolved: { label: 'Resolved', variant: 'success' },
  closed: { label: 'Closed', variant: 'neutral' },
}

export const requestStatus = (s) => REQUEST_STATUS[s] || { label: s || '—', variant: 'neutral' }

/** Manual review triggers raised by `backend/app/services/pipeline.py`. */
export const REASONS = {
  CLINICAL_RED_FLAG: 'Clinical red flag',
  AI_ENGINE_ERROR: 'AI engine error',
  LOW_CONFIDENCE: 'Low AI confidence',
  UNSUPPORTED_INTENT: 'Not a scheduling action',
  INCOMPLETE_DATA: 'Incomplete data',
  UNMATCHED_PATIENT: 'Unmatched patient',
  AMBIGUOUS_MATCH: 'Ambiguous match',
  CONFLICTING_INFO: 'Conflicting information',
  INSURANCE_CHANGE: 'Insurance change',
  NO_INSURANCE_ON_FILE: 'No insurance on file',
  ELIGIBILITY_FAILED: 'Eligibility failed',
  OUT_OF_NETWORK: 'Out of network',
  PROVIDER_NOT_FOUND: 'Provider not found',
  APPOINTMENT_NOT_FOUND: 'Appointment not found',
  NO_AVAILABILITY: 'No availability',
  SCHEDULING_ERROR: 'Scheduling error',
}

export const reasonLabel = (code) => REASONS[code] || code
export const reasonVariant = (code) => (code === 'CLINICAL_RED_FLAG' ? 'error' : 'warning')

export const coverageVariant = (s) => (s === 'active' ? 'success' : s === 'error' ? 'warning' : 'error')

export const appointmentVariant = (s) =>
  s === 'booked' ? 'success' : s === 'cancelled' ? 'neutral' : s === 'no_show' ? 'error' : 'info'

/** Confidence colour buckets matching the backend thresholds (0.80 automation). */
export function confidenceMeta(conf) {
  if (conf === null || conf === undefined) return { pct: '0%', label: '—', color: 'var(--hairline-strong)' }
  const pct = `${Math.round(conf * 100)}%`
  if (conf >= 0.8) return { pct, label: pct, color: 'var(--semantic-success)' }
  if (conf >= 0.6) return { pct, label: pct, color: '#b8860b' }
  return { pct, label: pct, color: 'var(--semantic-error)' }
}

export const intentLabel = (i) => {
  if (!i) return 'Unknown'
  const s = i.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Distinct trigger codes for compact badge rows (a code can be raised with several details). */
export const uniqueReasons = (reasons) => [...new Map((reasons || []).map((x) => [x.code, x])).values()]
