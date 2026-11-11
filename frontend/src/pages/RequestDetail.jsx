import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  AlertTriangle,
  CalendarCheck,
  CheckCircle2,
  Clock,
  Mail,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  UserCheck,
  UserPlus,
} from 'lucide-react'
import { DraftChartPanel } from '@/components/DraftChartPanel'
import { EmailBody } from '@/components/EmailBody'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { CompactInput, CompactSelect, CompactTextArea } from '@/components/ui/Field'
import { ConfirmDialog, Toast } from '@/components/ui/Overlay'
import { Confidence, Detail, ErrorNote, PageHeader, Section, Spinner, Timeline } from '@/components/ui/Page'
import { api, post, useAction, useApi } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/cn'
import { formatDate, formatDateTime, formatPhone, formatSlot } from '@/lib/format'
import { coverageVariant, intentLabel, reasonLabel, reasonVariant, requestStatus } from '@/lib/status'

/* ------------------------------------------------------------------ pipeline */

const STEPS = [
  { key: 'intake', label: 'Email intake', icon: Mail },
  { key: 'ai', label: 'AI analysis', icon: Sparkles },
  { key: 'match', label: 'Patient match', icon: UserCheck },
  { key: 'eligibility', label: 'Eligibility', icon: ShieldCheck },
  { key: 'decision', label: 'Decision', icon: Send },
  { key: 'resolution', label: 'Resolution', icon: CheckCircle2 },
]

function Pipeline({ r }) {
  const acts = new Set(r.timeline.map((t) => t.action))
  const matched = ['patient_matched', 'patient_match_kept', 'patient_linked'].some((a) => acts.has(a))
  const elig = r.eligibility_checks[0]
  const state = {
    intake: 'done',
    ai: acts.has('ai_extracted') ? 'done' : 'todo',
    match: matched ? 'done' : acts.has('patient_match_failed') ? 'fail' : 'todo',
    eligibility: elig ? (elig.coverage_status === 'active' && elig.in_network !== false ? 'done' : 'fail') : 'skip',
    decision: r.decision === 'auto_respond' ? 'done' : r.decision === 'manual_review' ? 'warn' : 'todo',
    resolution: ['resolved', 'closed'].includes(r.status) ? 'done' : 'todo',
  }
  const tone = {
    done: 'border-sage-border bg-sage-soft text-sage',
    fail: 'border-[#f7d4d4] bg-[#fdf2f2] text-error',
    warn: 'border-warm-amber-border bg-warm-amber-soft text-warm-amber',
    todo: 'border-hairline bg-card text-muted',
    skip: 'border-hairline bg-surface-subtle text-muted-soft',
  }
  return (
    <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
      {STEPS.map(({ key, label, icon: Icon }) => (
        <li key={key} className={cn('flex items-center gap-2 rounded-lg border px-3 py-2.5 font-body text-xs font-semibold', tone[state[key]])}>
          <Icon size={14} strokeWidth={1.75} className="shrink-0" />
          <span className="truncate">
            {key === 'decision' && r.decision ? (r.decision === 'auto_respond' ? 'Auto response' : 'Manual review') : label}
          </span>
        </li>
      ))}
    </ol>
  )
}

/* ------------------------------------------------------------------ panels */

function MatchPanel({ r, busy, onLink }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState(null)
  const m = r.match_details || {}
  const search = async () => setResults(await api(`/patients?q=${encodeURIComponent(q)}`))
  return (
    <Section title="Patient match" actions={<Confidence value={r.match_confidence} />}>
      {r.patient ? (
        <div className="flex items-center gap-3 rounded-lg border border-sage-border bg-sage-soft/60 px-4 py-3">
          <UserCheck size={18} className="shrink-0 text-sage" />
          <div className="min-w-0">
            <Link to={`/patients/${r.patient.id}`} className="font-body text-sm font-semibold text-ink">
              {r.patient.full_name}
            </Link>
            <span className="ml-2 font-body text-xs text-muted">MRN {r.patient.mrn}</span>
            <p className="font-body text-xs text-muted">
              DOB {formatDate(r.patient.date_of_birth)} · {r.patient.primary_payer || 'No insurance'} {r.patient.member_id}
            </p>
          </div>
          {m.status === 'linked_by_staff' && (
            <Badge variant="info" className="ml-auto">
              Linked by staff
            </Badge>
          )}
        </div>
      ) : (
        <p className="font-body text-sm text-muted">
          {m.candidates?.length > 0
            ? `No chart linked yet. Closest: ${m.candidates[0].name} at ${Math.round(m.candidates[0].score * 100)}% — ` +
              (m.status === 'ambiguous' ? 'more than one chart fits, so please choose.' : 'please confirm before linking.')
            : m.status && m.status !== 'pending'
              ? 'No similar chart found in the database — treated as a new or unknown patient.'
              : 'Not matched yet.'}
        </p>
      )}

      {m.candidates?.length > 0 && (
        <div className="flex flex-col divide-y divide-hairline rounded-lg border border-hairline">
          {m.candidates.map((c) => (
            <div key={c.patient_id} className="flex items-start justify-between gap-3 px-3.5 py-2.5">
              <div className="min-w-0">
                <Link to={`/patients/${c.patient_id}`} className="font-body text-sm font-medium text-ink">
                  {c.name}
                </Link>
                <span className="ml-2 font-body text-xs text-muted">
                  {c.mrn} · score {Math.round(c.score * 100)}%
                </span>
                <p className="font-body text-xs text-muted">Evidence: {c.evidence.join(', ') || 'none'}</p>
                {c.conflicts.length > 0 && <p className="font-body text-xs text-error">{c.conflicts.join('; ')}</p>}
              </div>
              {r.patient?.id !== c.patient_id && (
                <Button variant="outline" className="h-8 px-3 text-xs" disabled={busy} onClick={() => onLink(c.patient_id)}>
                  Link
                </Button>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <CompactInput
          className="flex-1"
          placeholder="Search charts to link…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()}
        />
        <Button variant="outline" className="h-10 px-4 text-sm" onClick={search}>
          Search
        </Button>
      </div>
      {results &&
        (results.length ? (
          <div className="flex flex-col divide-y divide-hairline">
            {results.map((p) => (
              <div key={p.id} className="flex items-center justify-between gap-2 py-2">
                <span className="font-body text-sm">
                  {p.full_name} <span className="text-muted">· {formatDate(p.date_of_birth)} · {p.mrn}</span>
                </span>
                <Button variant="outline" className="h-8 px-3 text-xs" disabled={busy} onClick={() => onLink(p.id)}>
                  Link & re-run
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="font-body text-xs text-muted">No charts found.</p>
        ))}
    </Section>
  )
}

function RegisterFromRequest({ r, onDone }) {
  const { data: payers } = useApi('/payers')
  const ext = r.extracted || {}
  const [payerId, setPayerId] = useState('')
  const act = useAction()
  const guess = payers?.find((p) => p.name === ext.insurance?.payer_name)?.id
  useEffect(() => {
    if (guess) setPayerId(String(guess))
  }, [guess])
  const submit = () =>
    act.run(async () => onDone(await post(`/requests/${r.id}/create-patient`, { payer_id: payerId ? Number(payerId) : null })))
  return (
    <Section title="Register new patient from this request" className="border-sage-border">
      <p className="font-body text-sm text-muted">
        A chart is created from the AI-extracted details. Verify them against the email first. Photo ID and insurance
        card images can be added on the chart afterwards.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <Detail label="Name">{[ext.patient?.first_name, ext.patient?.last_name].filter(Boolean).join(' ')}</Detail>
        <Detail label="Date of birth">{ext.patient?.date_of_birth && formatDate(ext.patient.date_of_birth)}</Detail>
        <Detail label="Phone">{formatPhone(ext.patient?.phone)}</Detail>
        <Detail label="Email">{ext.patient?.email}</Detail>
        <Detail label="Member ID">{ext.insurance?.member_id}</Detail>
        <Detail label="Group">{ext.insurance?.group_number}</Detail>
      </div>
      <div className="flex flex-wrap gap-2">
        <CompactSelect value={payerId} onChange={(e) => setPayerId(e.target.value)} className="min-w-[200px] flex-1">
          <option value="">Primary payer…</option>
          {payers?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </CompactSelect>
        <Button className="gap-2" disabled={act.busy} onClick={submit}>
          <UserPlus size={15} />
          Register & re-run AI
        </Button>
      </div>
      <ErrorNote error={act.error} />
    </Section>
  )
}

function BookPanel({ r, onDone }) {
  const { data, loading, reload } = useApi(`/requests/${r.id}/slots`)
  const { data: chart } = useApi(r.patient ? `/patients/${r.patient.id}` : null)
  const [cancelId, setCancelId] = useState('')
  const act = useAction()
  const upcoming = (chart?.appointments || []).filter((a) => a.status === 'booked')
  const book = (s) =>
    act.run(async () =>
      onDone(
        await post(`/requests/${r.id}/book`, {
          provider_id: s.provider_id,
          start_at: s.start_at,
          cancel_appointment_id: cancelId ? Number(cancelId) : null,
        }),
      ),
    )
  return (
    <Section
      title="Book appointment"
      actions={
        <button onClick={reload} className="flex cursor-pointer items-center gap-1 font-body text-xs text-muted hover:text-ink">
          <RefreshCw size={12} /> Refresh
        </button>
      }
    >
      {!r.patient ? (
        <p className="font-body text-sm text-muted">Link or register the patient first.</p>
      ) : loading && !data ? (
        <Spinner className="py-4" />
      ) : (
        <>
          {r.intent === 'reschedule' && upcoming.length > 0 && (
            <label className="flex flex-col gap-1 font-body text-xs font-semibold text-muted">
              Cancel existing appointment (reschedule)
              <CompactSelect value={cancelId} onChange={(e) => setCancelId(e.target.value)}>
                <option value="">— none —</option>
                {upcoming.map((a) => (
                  <option key={a.id} value={a.id}>
                    {formatSlot(a.start_at)} · {a.provider_name}
                  </option>
                ))}
              </CompactSelect>
            </label>
          )}
          <p className="font-body text-xs text-muted">
            {data?.matches_preference
              ? "Openings on the patient's preferred dates:"
              : 'No openings on the preferred dates, so these are the next available:'}
          </p>
          <div className="flex flex-col divide-y divide-hairline">
            {data?.slots.map((s) => (
              <div key={s.provider_id + s.start_at} className="flex items-center justify-between gap-2 py-2">
                <span className="flex items-center gap-2 font-body text-sm">
                  <Clock size={14} className="text-muted" />
                  {formatSlot(s.start_at)} <span className="text-muted">· {s.provider_name}</span>
                </span>
                <Button className="h-8 px-3 text-xs" disabled={act.busy} onClick={() => book(s)}>
                  Book & confirm
                </Button>
              </div>
            ))}
          </div>
          <ErrorNote error={act.error} />
        </>
      )}
    </Section>
  )
}

const METHOD_LABEL = { smtp: 'SMTP', graph: 'Microsoft 365 (Graph)', sendgrid: 'SendGrid', mailgun: 'Mailgun', outbox: 'outbox' }

/** Tell staff exactly what happened to an outgoing email. */
export function deliveryMessage(e) {
  if (!e) return ''
  if (e.send_status === 'sent') return `Emailed to ${e.to_address} via ${METHOD_LABEL[e.delivery_method] || e.delivery_method}.`
  if (e.send_status === 'test_sent') return `Test mode: sent to ${e.redirect_to} only. ${e.to_address} did NOT receive it.`
  if (e.send_status === 'awaiting_approval') return e.send_error || `Test mode: waiting for approval (goes to ${e.redirect_to} only).`
  if (e.send_status === 'held') return `Held, NOT emailed to ${e.to_address}: outbound email is turned off (Settings → Integrations).`
  if (e.send_status === 'stored') return `Saved but NOT emailed to ${e.to_address}: email delivery isn't set up (an admin can set it up in Settings → Email delivery).`
  return `NOT delivered to ${e.to_address}: ${e.send_error || 'unknown error'}`
}

function ReplyPanel({ r, onDone, setConfirm, notify }) {
  const [body, setBody] = useState(r.draft_response || '')
  const [note, setNote] = useState('')
  const [last, setLast] = useState(null)
  const act = useAction()
  const { data: info } = useApi('/system/info')
  useEffect(() => setBody(r.draft_response || ''), [r.draft_response])
  const open = !['resolved', 'closed'].includes(r.status)
  const mode = info?.email_outbound_mode

  const send = (resolve) =>
    act.run(async () => {
      const res = await post(`/requests/${r.id}/send-response`, { body, resolve })
      onDone(res.request)
      setLast(res.email)
      notify(deliveryMessage(res.email) + (resolve ? ' Request resolved.' : ''))
    })
  const resolve = (close) =>
    setConfirm({
      title: close ? 'Close this request?' : 'Resolve this request?',
      body: close
        ? 'Use Close when no action is needed (spam, duplicate, handled elsewhere). The patient is not emailed.'
        : 'The request is marked resolved with your note. The patient is not emailed unless you send a reply.',
      cta: close ? 'Close request' : 'Resolve',
      onYes: () =>
        act.run(async () => {
          setConfirm(null)
          onDone(await post(`/requests/${r.id}/resolve`, { note: note || (close ? 'Closed, no action needed' : 'Resolved'), close }))
          notify(close ? 'Request closed.' : 'Request resolved.')
        }),
    })

  return (
    <Section title="Response & resolution">
      {info && info.outbound_email_enabled === false && (
        <div className="rounded-lg border border-warm-amber-border bg-warm-amber-soft px-3.5 py-2.5 font-body text-xs text-warm-amber">
          <b>Outbound email is turned off.</b> Replies are saved on this request as “held” and are <b>not emailed</b>.
          An admin can turn it on in Settings → Integrations, then use “Retry sending”.
        </div>
      )}
      {info?.outbound_email_enabled !== false && mode === 'outbox' && (
        <div className="rounded-lg border border-warm-amber-border bg-warm-amber-soft px-3.5 py-2.5 font-body text-xs text-warm-amber">
          Email delivery isn't set up, so replies are saved in the portal but <b>not emailed</b> to the patient.
          An admin can turn it on in Settings → Email delivery.
        </div>
      )}
      {info?.outbound_email_enabled !== false && mode && mode !== 'outbox' && (
        <p className="font-body text-xs text-muted">Replies are emailed to the patient via {METHOD_LABEL[mode] || mode} (encrypted in transit).</p>
      )}
      <label className="flex flex-col gap-1.5 font-body text-xs font-semibold text-muted">
        Reply to {r.from_address}. The AI drafted this; edit it before sending.
        <CompactTextArea rows={10} value={body} onChange={(e) => setBody(e.target.value)} className="font-normal" />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button className="gap-2" disabled={act.busy || !body.trim()} onClick={() => send(true)}>
          <Send size={15} />
          Send & resolve
        </Button>
        <Button variant="outline" disabled={act.busy || !body.trim()} onClick={() => send(false)}>
          Send only
        </Button>
      </div>
      {open ? (
        <div className="flex flex-wrap gap-2 border-t border-hairline pt-3.5">
          <CompactInput
            className="min-w-[220px] flex-1"
            placeholder="Resolution note (e.g. called patient, booked by phone)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <Button variant="outline" disabled={act.busy} onClick={() => resolve(false)}>
            Resolve
          </Button>
          <Button variant="text" disabled={act.busy} onClick={() => resolve(true)}>
            Close (no action)
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-hairline pt-3.5 font-body text-sm text-muted">
          <span>
            Resolved by {r.resolved_by} · {formatDateTime(r.resolved_at)} · {r.resolution_note}
          </span>
          <Button variant="outline" onClick={() => act.run(async () => onDone(await post(`/requests/${r.id}/reopen`)))}>
            Reopen
          </Button>
        </div>
      )}
      {last && (
        <div className={cn('rounded-lg border px-3.5 py-2.5 font-body text-sm',
          last.send_status === 'sent' ? 'border-sage-border bg-sage-soft text-sage'
            : ['stored', 'held'].includes(last.send_status) ? 'border-warm-amber-border bg-warm-amber-soft text-warm-amber'
              : 'border-[#f7d4d4] bg-[#fdf2f2] text-error')}>
          {deliveryMessage(last)}
        </div>
      )}
      <ErrorNote error={act.error} />
    </Section>
  )
}

const STATUS_LABEL = { stored: 'not emailed', held: 'held (outbound off)', awaiting_approval: 'test: awaiting approval', test_sent: 'test: sent to tester' }

function SentReply({ e, onResent, notify, testMode }) {
  const act = useAction()
  const retry = () => act.run(async () => {
    const updated = await post(`/emails/${e.id}/resend`)
    onResent()
    notify(deliveryMessage(updated))
  })
  const approve = () => act.run(async () => {
    const updated = await post(`/emails/${e.id}/approve-test`)
    onResent()
    notify(deliveryMessage(updated))
  })
  const canApprove = testMode && ['awaiting_approval', 'held', 'stored', 'failed'].includes(e.send_status)
  const variant = ['sent', 'test_sent'].includes(e.send_status) ? 'success'
    : ['stored', 'held', 'awaiting_approval'].includes(e.send_status) ? 'warning' : 'error'
  return (
    <details className="group rounded-lg border border-hairline px-4 py-2.5">
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 font-body text-sm">
        <Badge variant={variant}>{STATUS_LABEL[e.send_status] || e.send_status}</Badge>
        <span className="font-medium text-ink">{e.subject}</span>
        <span className="text-xs text-muted">
          to {e.to_address} · {e.sent_by} · {formatDateTime(e.received_at)}
          {e.delivery_method && e.delivery_method !== 'outbox' ? ` · ${METHOD_LABEL[e.delivery_method] || e.delivery_method}` : ''}
        </span>
        {canApprove ? (
          <Button className="ml-auto h-7 px-3 text-xs" disabled={act.busy} onClick={(ev) => { ev.preventDefault(); approve() }}
            title={`Sends this reply to ${testMode.recipient} only. ${e.to_address} will not receive it.`}>
            {act.busy ? 'Sending…' : `Approve & send to ${testMode.recipient}`}
          </Button>
        ) : !testMode && !['sent', 'test_sent'].includes(e.send_status) && (
          <Button variant="outline" className="ml-auto h-7 px-3 text-xs" disabled={act.busy} onClick={(ev) => { ev.preventDefault(); retry() }}>
            {act.busy ? 'Sending…' : 'Retry sending'}
          </Button>
        )}
      </summary>
      {e.redirect_to && e.send_status === 'test_sent' && (
        <p className="mt-2 font-body text-xs text-muted">Delivered to {e.redirect_to} (test mode). The real recipient, {e.to_address}, did not receive it.</p>
      )}
      <pre className="mt-2.5 font-body text-sm leading-relaxed whitespace-pre-wrap text-body">{e.body_text}</pre>
      {e.send_error && <p className="font-body text-xs text-error">{e.send_error}</p>}
      <ErrorNote error={act.error} />
    </details>
  )
}

/* ------------------------------------------------------------------ page */

export default function RequestDetail() {
  const { id } = useParams()
  const { user } = useAuth()
  const { data: r, setData, error } = useApi(`/requests/${id}`, [id])
  const { data: sysInfo } = useApi('/system/info')
  const act = useAction()
  const [confirm, setConfirm] = useState(null)
  const [toast, setToast] = useState('')
  const notify = (msg) => {
    setToast(msg)
    setTimeout(() => setToast(''), 2600)
  }

  if (error) return <ErrorNote error={error} />
  if (!r) return <Spinner />

  const ext = r.extracted || {}
  const source = r.emails.find((e) => e.direction === 'inbound')
  const replies = r.emails.filter((e) => e.direction === 'outbound')
  const open = !['resolved', 'closed'].includes(r.status)
  const status = requestStatus(r.status)
  const redFlag = r.review_reasons.some((x) => x.code === 'CLINICAL_RED_FLAG')
  const run = (fn, msg) =>
    act.run(async () => {
      setData(await fn())
      if (msg) notify(msg)
    })

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        back={{ to: '/requests', label: 'Back to request inbox' }}
        title={
          <>
            {r.reference}
            <Badge variant={status.variant}>{status.label}</Badge>
            {r.priority === 'high' && <Badge variant="error">High priority</Badge>}
          </>
        }
        subtitle={r.summary}
        actions={
          <>
            {open && r.assigned_to !== user?.email && (
              <Button variant="outline" disabled={act.busy} onClick={() => run(() => post(`/requests/${r.id}/assign`, {}), 'Assigned to you.')}>
                Assign to me
              </Button>
            )}
            {open && (
              <Button className="gap-2" disabled={act.busy} onClick={() => run(() => post(`/requests/${r.id}/reprocess`), 'AI analysis re-run.')}>
                <RefreshCw size={15} />
                Re-run AI analysis
              </Button>
            )}
          </>
        }
      />

      <Pipeline r={r} />
      <ErrorNote error={act.error} />

      {r.review_reasons.length > 0 && (
        <div
          className={cn(
            'flex flex-col gap-2 rounded-xl border px-5 py-4',
            redFlag ? 'border-[#f7d4d4] bg-[#fdf2f2]' : 'border-warm-amber-border bg-warm-amber-soft',
          )}
        >
          <p className={cn('flex items-center gap-2 font-body text-sm font-semibold', redFlag ? 'text-error' : 'text-warm-amber')}>
            <AlertTriangle size={16} />
            Manual review required
            {r.assigned_to && <span className="font-normal text-muted">· assigned to {r.assigned_to}</span>}
          </p>
          <ul className="flex flex-col gap-1.5">
            {r.review_reasons.map((x, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-2 font-body text-sm text-body">
                <Badge variant={reasonVariant(x.code)}>{reasonLabel(x.code)}</Badge>
                {x.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {r.appointment && (
        <div className="flex items-center gap-2.5 rounded-xl border border-sage-border bg-sage-soft px-5 py-3.5 font-body text-sm text-ink">
          <CalendarCheck size={16} className="text-sage" />
          Appointment <strong className="font-semibold">{r.appointment.status}</strong>: {formatSlot(r.appointment.start_at)} with{' '}
          {r.appointment.provider_name}
        </div>
      )}

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-6">
          <Section title="Original email">
            {source ? (
              <>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-body text-sm font-semibold text-ink">{source.from_name || source.from_address}</p>
                    <p className="font-body text-xs text-muted">
                      {source.from_address} · {formatDateTime(source.received_at)} · via {source.source}
                    </p>
                  </div>
                </div>
                <p className="font-display text-lg text-ink">{source.subject || '(no subject)'}</p>
                <EmailBody text={source.body_text} className="rounded-lg border border-hairline/70 bg-surface-subtle/60 px-4 py-3" />
              </>
            ) : (
              <p className="font-body text-sm text-muted">No source email.</p>
            )}
          </Section>

          <Section
            title="AI analysis"
            actions={
              <>
                <Badge variant="info">{r.llm_provider}</Badge>
                <Confidence value={r.ai_confidence} />
              </>
            }
          >
            <div className="grid grid-cols-1 gap-x-5 gap-y-3.5 sm:grid-cols-2">
              <Detail label="Intent">{intentLabel(r.intent)}</Detail>
              <Detail label="New patient?">{ext.is_new_patient == null ? 'Unknown' : ext.is_new_patient ? 'Yes' : 'No'}</Detail>
              <Detail label="Patient name">{[ext.patient?.first_name, ext.patient?.last_name].filter(Boolean).join(' ')}</Detail>
              <Detail label="Date of birth">{ext.patient?.date_of_birth && formatDate(ext.patient.date_of_birth)}</Detail>
              <Detail label="Phone">{formatPhone(ext.patient?.phone)}</Detail>
              <Detail label="Email">{ext.patient?.email}</Detail>
              <Detail label="Requester">
                {ext.patient?.requester_is_patient ? 'Patient' : `On behalf (${ext.patient?.requester_relationship || '?'})`}
              </Detail>
              <Detail label="Payer">{ext.insurance?.payer_name}</Detail>
              <Detail label="Member ID">{ext.insurance?.member_id}</Detail>
              <Detail label="Provider requested">{ext.appointment?.provider_name}</Detail>
              <Detail label="Visit reason">{ext.appointment?.visit_reason}</Detail>
              <Detail label="Time of day">{ext.appointment?.preferred_time ? `${ext.appointment.preferred_time} (exact)` : ext.appointment?.preferred_time_of_day}</Detail>
              <Detail label="Sex">{ext.patient?.sex}</Detail>
              <Detail label="Address">{[ext.patient?.address_line1, ext.patient?.city, ext.patient?.state, ext.patient?.postal_code].filter(Boolean).join(', ')}</Detail>
              <Detail label="Preferred dates" className="sm:col-span-2">
                {ext.appointment?.preferred_dates?.length ? ext.appointment.preferred_dates.map(formatDate).join(', ') : null}
              </Detail>
              <Detail label="Current appointment">
                {ext.appointment?.current_appointment_date && formatDate(ext.appointment.current_appointment_date)}
              </Detail>
            </div>
            {ext.engine_error && <p className="font-body text-xs text-error">Engine error: {ext.engine_error}</p>}
          </Section>

          <Section title="Insurance verification">
            {r.eligibility_checks.length ? (
              <div className="flex flex-col divide-y divide-hairline">
                {r.eligibility_checks.map((c) => (
                  <div key={c.id} className="flex flex-col gap-1 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={coverageVariant(c.coverage_status)}>{c.coverage_status}</Badge>
                      {c.in_network != null && (
                        <Badge variant={c.in_network ? 'success' : 'error'}>{c.in_network ? 'In network' : 'Out of network'}</Badge>
                      )}
                      <span className="font-body text-xs text-muted">{formatDateTime(c.checked_at)}</span>
                    </div>
                    <p className="font-body text-sm text-body">
                      {c.plan_name || '—'}
                      {c.copay && ` · copay ${c.copay}`}
                    </p>
                    <p className="font-body text-xs text-muted">{c.message}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="font-body text-sm text-muted">Not run for this request.</p>
            )}
          </Section>
        </div>

        <div className="flex flex-col gap-6">
          <MatchPanel
            r={r}
            busy={act.busy}
            onLink={(pid) => run(() => post(`/requests/${r.id}/link-patient`, { patient_id: pid }), 'Chart linked and AI re-run.')}
          />
          {open && !r.patient && r.draft_chart?.status === 'pending' && (
            <DraftChartPanel r={r} onDone={setData} notify={notify}
              onLink={(pid) => run(() => post(`/requests/${r.id}/link-patient`, { patient_id: pid }), 'Existing chart linked and AI re-run.')} />
          )}
          {open && !r.patient && r.draft_chart?.status !== 'pending' && <RegisterFromRequest r={r} onDone={setData} />}
          {open && r.patient && !['cancel', 'general_question', 'other'].includes(r.intent) && !r.appointment && (
            <BookPanel r={r} onDone={setData} />
          )}
          <ReplyPanel r={r} onDone={setData} setConfirm={setConfirm} notify={notify} />
          {replies.length > 0 && (
            <Section title={`Sent replies (${replies.length})`}>
              {replies.map((e) => (
                <SentReply key={e.id} e={e} notify={notify} testMode={sysInfo?.outbound_test_mode}
                  onResent={async () => setData(await api(`/requests/${r.id}`))} />
              ))}
            </Section>
          )}
        </div>
      </div>

      <Section title="Audit trail">
        <Timeline events={r.timeline} />
      </Section>

      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          body={confirm.body}
          cta={confirm.cta}
          onConfirm={confirm.onYes}
          onCancel={() => setConfirm(null)}
        />
      )}
      <Toast message={toast} />
    </div>
  )
}
