/** Presentation helpers. Nothing here touches the DOM or the design system. */

/**
 * Parses a date-only ISO string (`2026-09-04`) in *local* time.
 *
 * `new Date('2026-09-04')` is parsed as UTC, which shifts the day backwards for
 * anyone west of Greenwich — an appointment booked for the 4th would render as
 * the 3rd. Splitting the parts avoids that.
 */
export function parseIsoDate(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function formatIsoDate(iso, options) {
  if (!iso) return ''
  return parseIsoDate(iso).toLocaleDateString(
    'en-US',
    options ?? { weekday: 'short', month: 'short', day: 'numeric' },
  )
}

/** "Thu, Sep 4" — used wherever an appointment date is shown. */
export const formatApptDate = (iso) => formatIsoDate(iso)

/** "Sep 2" — short stamp for a completion timestamp. */
export function formatShortDate(isoDateTime) {
  if (!isoDateTime) return ''
  return new Date(isoDateTime).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/** Builds a local-time `YYYY-MM-DD` key for calendar lookups. */
export function toIsoKey(year, month, day) {
  return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

export function todayIsoKey(now = new Date()) {
  return toIsoKey(now.getFullYear(), now.getMonth(), now.getDate())
}

/** Sort key for "9:15 AM" style times, so 9:15 AM precedes 10:00 AM. */
export function timeSortKey(time) {
  const match = /(\d+):(\d+)\s*(AM|PM)/i.exec(time || '')
  if (!match) return 0
  let hours = Number(match[1]) % 12
  if (/pm/i.test(match[3])) hours += 12
  return hours * 60 + Number(match[2])
}

/** 0.96 -> "96%" */
export function formatPercent(value, digits = 0) {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

/** "no_answer" -> "No answer" */
export function humanize(value) {
  if (!value) return '—'
  const spaced = value.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/* ---- Docs Health additions ------------------------------------------------ */

/** Timestamp from the API (UTC ISO) -> "Sep 30, 9:04 PM" in the viewer's zone. */
export function formatDateTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** Date-only ISO (or the date part of one) -> "Mar 14, 1985". */
export function formatDate(iso) {
  if (!iso) return '—'
  return formatIsoDate(iso.slice(0, 10), { year: 'numeric', month: 'short', day: 'numeric' })
}

/**
 * Appointment times are clinic-local naive datetimes ("2026-10-06T09:00:00"),
 * so they are rendered without any timezone conversion.
 */
export function formatSlot(iso, options) {
  if (!iso) return '—'
  const [date, time = '00:00'] = iso.split('T')
  const [y, m, d] = date.split('-').map(Number)
  const [hh, mm] = time.split(':').map(Number)
  return new Date(y, m - 1, d, hh, mm).toLocaleString(
    'en-US',
    options ?? { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' },
  )
}

export const formatTime = (iso) => formatSlot(iso, { hour: 'numeric', minute: '2-digit' })

export function formatPhone(v) {
  const d = (v || '').replace(/\D/g, '')
  return d.length === 10 ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : v || null
}

export function initials(name) {
  return (name || '?')
    .split(/\s+/)
    .filter(Boolean)
    .map((p) => p[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()
}

/** Local YYYY-MM-DD shifted by `days`. */
export function shiftIso(iso, days) {
  const d = parseIsoDate(iso)
  d.setDate(d.getDate() + days)
  return toIsoKey(d.getFullYear(), d.getMonth(), d.getDate())
}

/** 1500 -> "$1,500.00"; null -> "—" */
export function formatMoney(v) {
  if (v === null || v === undefined || v === '') return '—'
  return Number(v).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

/** Whole years between a date-only ISO string and today. */
export function ageFrom(iso) {
  if (!iso) return null
  const dob = parseIsoDate(iso.slice(0, 10))
  const now = new Date()
  let age = now.getFullYear() - dob.getFullYear()
  if (now.getMonth() < dob.getMonth() || (now.getMonth() === dob.getMonth() && now.getDate() < dob.getDate())) age -= 1
  return age
}
