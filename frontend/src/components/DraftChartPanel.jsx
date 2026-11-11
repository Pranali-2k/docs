import { useEffect, useState } from 'react'
import { AlertTriangle, CalendarClock, FilePlus2, Sparkles, Trash2, UserCheck } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { CompactInput, CompactSelect } from '@/components/ui/Field'
import { ErrorNote, Section } from '@/components/ui/Page'
import { api, post, useAction, useApi } from '@/lib/api'
import { cn } from '@/lib/cn'
import { formatDate, formatSlot } from '@/lib/format'
import { US_STATES } from '@/pages/chart/shared'

const FIELDS = [
  ['first_name', 'First name', { required: true }],
  ['last_name', 'Last name', { required: true }],
  ['date_of_birth', 'Date of birth', { required: true, type: 'date' }],
  ['sex', 'Sex', { select: [['', '—'], ['female', 'Female'], ['male', 'Male'], ['other', 'Other']] }],
  ['phone', 'Phone', { type: 'tel' }],
  ['email', 'Email', { type: 'email' }],
  ['address_line1', 'Street address', { wide: true }],
  ['city', 'City'],
  ['state', 'State', { select: [['', '—'], ...US_STATES.map((s) => [s, s])] }],
  ['postal_code', 'ZIP'],
  ['ssn_last4', 'SSN (last 4)', { inputMode: 'numeric', maxLength: 4 }],
]

function slotKey(s) {
  return s ? `${s.provider_id}|${s.start_at}` : 'none'
}

/**
 * AI-drafted chart + proposed appointment for an unmatched new patient. Staff check/edit the details, choose a slot
 * (or none) and approve: the chart is created, linked and the slot booked in one step. Nothing exists until then.
 */
export function DraftChartPanel({ r, onDone, onLink, notify }) {
  const draft = r.draft_chart
  const act = useAction()
  const { data: payers } = useApi('/payers')
  const { data: info } = useApi('/system/info')
  const [form, setForm] = useState(() => ({ ...draft.patient }))
  const [slots, setSlots] = useState(() => [draft.proposed, ...(draft.alternatives || [])].filter(Boolean))
  const [choice, setChoice] = useState(slotKey(draft.proposed))
  const [more, setMore] = useState(false)
  const [payerId, setPayerId] = useState('')
  const [memberId, setMemberId] = useState(draft.insurance?.member_id || '')
  const [groupNo, setGroupNo] = useState(draft.insurance?.group_number || '')
  const [allowDup, setAllowDup] = useState(false)
  const [discarding, setDiscarding] = useState(false)
  const [reason, setReason] = useState('')

  const guess = payers?.find((p) => p.name === draft.insurance?.payer_name)?.id
  useEffect(() => {
    if (guess) setPayerId(String(guess))
  }, [guess])

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))
  const missing = FIELDS.filter(([k, , o]) => o?.required && !String(form[k] || '').trim()).map(([, l]) => l)
  const chosen = slots.find((s) => slotKey(s) === choice) || null
  const outboundOff = info && info.outbound_email_enabled === false

  const loadMore = () =>
    act.run(async () => {
      const res = await api(`/requests/${r.id}/draft-chart/slots`)
      const all = [res.proposed, ...(res.alternatives || [])].filter(Boolean)
      setSlots((cur) => [...cur, ...all.filter((s) => !cur.some((c) => slotKey(c) === slotKey(s)))])
      setMore(true)
    })

  const approve = () =>
    act.run(async () => {
      const res = await post(`/requests/${r.id}/draft-chart/approve`, {
        patient: form,
        allow_duplicate: allowDup,
        book: chosen ? { provider_id: chosen.provider_id, start_at: chosen.start_at } : null,
        send_confirmation: true,
        payer_id: payerId ? Number(payerId) : null,
        member_id: memberId || null,
        group_number: groupNo || null,
      })
      onDone(res)
      notify(chosen ? `Chart ${res.patient?.mrn} created and appointment booked.` : `Chart ${res.patient?.mrn} created.`)
    })

  const discard = () =>
    act.run(async () => {
      onDone(await post(`/requests/${r.id}/draft-chart/discard`, { reason: reason || null }))
      notify('Draft chart discarded.')
    })

  const requested = draft.requested || {}
  return (
    <Section
      title={<span className="flex items-center gap-2"><Sparkles size={15} className="text-warm-amber" /> Draft chart from this email</span>}
      actions={<Badge variant="warning">Needs approval</Badge>}
      className="border-warm-amber-border"
    >
      <p className="font-body text-sm text-muted">
        The AI prepared a chart and an appointment from the email. Check the details against the email: nothing is created or
        booked until you approve.
      </p>

      {draft.notes?.length > 0 && (
        <ul className="flex flex-col gap-1 rounded-lg bg-warm-amber-soft px-3.5 py-2.5">
          {draft.notes.map((n) => (
            <li key={n} className="flex gap-2 font-body text-xs text-ink"><AlertTriangle size={13} className="mt-0.5 shrink-0 text-warm-amber" />{n}</li>
          ))}
        </ul>
      )}

      {draft.duplicates?.length > 0 && (
        <div className="flex flex-col gap-2 rounded-lg border border-[#f7d4d4] bg-[#fdf2f2] px-3.5 py-2.5">
          <p className="font-body text-xs font-semibold text-error">Possible existing chart: same name and date of birth</p>
          {draft.duplicates.map((d) => (
            <div key={d.id} className="flex flex-wrap items-center justify-between gap-2 font-body text-sm">
              <span>{d.name} · MRN {d.mrn}{d.date_of_birth ? ` · DOB ${formatDate(d.date_of_birth)}` : ''}</span>
              <Button variant="outline" className="h-7 gap-1.5 px-2.5 text-xs" disabled={act.busy} onClick={() => onLink(d.id)}>
                <UserCheck size={13} /> Link this chart instead
              </Button>
            </div>
          ))}
          <label className="flex items-center gap-2 font-body text-xs text-ink">
            <input type="checkbox" checked={allowDup} onChange={(e) => setAllowDup(e.target.checked)} /> It's a different person: create a new chart anyway
          </label>
        </div>
      )}

      <div className="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
        {FIELDS.map(([k, label, o = {}]) => {
          const empty = !String(form[k] || '').trim()
          return (
            <label key={k} className={cn('flex flex-col gap-1', o.wide && 'sm:col-span-2')}>
              <span className="font-body text-xs font-medium text-ink">
                {label}{o.required && <span className="text-error"> *</span>}
                {!o.required && empty && <span className="font-normal text-muted"> (not in email)</span>}
              </span>
              {o.select ? (
                <CompactSelect value={form[k] || ''} onChange={(e) => set(k, e.target.value)}>
                  {o.select.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </CompactSelect>
              ) : (
                <CompactInput type={o.type || 'text'} inputMode={o.inputMode} maxLength={o.maxLength} value={form[k] || ''}
                  onChange={(e) => set(k, e.target.value)} aria-invalid={o.required && empty}
                  className={cn(o.required && empty && 'border-error')} />
              )}
            </label>
          )
        })}
      </div>

      <div className="grid grid-cols-1 gap-x-3 gap-y-2.5 border-t border-hairline pt-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1 sm:col-span-3 lg:col-span-1">
          <span className="font-body text-xs font-medium text-ink">Insurance (optional)</span>
          <CompactSelect value={payerId} onChange={(e) => setPayerId(e.target.value)}>
            <option value="">No insurance yet</option>
            {payers?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </CompactSelect>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-body text-xs font-medium text-ink">Member ID</span>
          <CompactInput value={memberId} onChange={(e) => setMemberId(e.target.value)} disabled={!payerId} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-body text-xs font-medium text-ink">Group #</span>
          <CompactInput value={groupNo} onChange={(e) => setGroupNo(e.target.value)} disabled={!payerId} />
        </label>
      </div>

      <div className="flex flex-col gap-2 border-t border-hairline pt-3">
        <p className="flex flex-wrap items-center gap-2 font-body text-xs font-medium text-ink">
          <CalendarClock size={14} className="text-muted" /> Appointment
          {(requested.dates?.length > 0 || requested.time) && (
            <span className="font-normal text-muted">
              · requested {requested.dates?.map(formatDate).join(', ')}{requested.time ? ` at ${requested.time}` : ''}
            </span>
          )}
          {draft.matches_request ? <Badge variant="success">Requested time is open</Badge>
            : requested.dates?.length || requested.time ? <Badge variant="warning">Requested time not open</Badge> : null}
        </p>
        <div className="flex flex-col divide-y divide-hairline rounded-lg border border-hairline" role="radiogroup" aria-label="Appointment slot">
          {slots.map((s, i) => (
            <label key={slotKey(s)} className="flex cursor-pointer items-center gap-2.5 px-3 py-2 font-body text-sm hover:bg-surface-subtle/70">
              <input type="radio" name={`slot-${r.id}`} checked={choice === slotKey(s)} onChange={() => setChoice(slotKey(s))} />
              {formatSlot(s.start_at)} <span className="text-muted">· {s.provider_name}</span>
              {i === 0 && draft.matches_request && <Badge variant="success" className="ml-auto">as requested</Badge>}
            </label>
          ))}
          <label className="flex cursor-pointer items-center gap-2.5 px-3 py-2 font-body text-sm text-muted hover:bg-surface-subtle/70">
            <input type="radio" name={`slot-${r.id}`} checked={choice === 'none'} onChange={() => setChoice('none')} />
            Don't book now: create the chart only
          </label>
        </div>
        {!more && <button className="self-start cursor-pointer font-body text-xs font-medium text-muted underline hover:text-ink" disabled={act.busy} onClick={loadMore}>Show more openings</button>}
        {slots.length === 0 && <p className="font-body text-xs text-muted">No openings found in the next weeks. Create the chart and book from the schedule.</p>}
        {chosen && (
          <p className="font-body text-xs text-muted">
            A confirmation reply goes to {draft.requester?.from_address || 'the sender'}
            {outboundOff ? ' and is held (outbound email is off), not sent.' : '.'}
          </p>
        )}
      </div>

      {missing.length > 0 && <p role="alert" className="font-body text-xs text-error">Required: {missing.join(', ')}.</p>}
      <ErrorNote error={act.error} />
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-hairline pt-3">
        {discarding ? (
          <div className="flex flex-1 flex-wrap items-center gap-2">
            <CompactInput placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} className="min-w-48 flex-1" />
            <Button variant="outline" className="gap-1.5" disabled={act.busy} onClick={discard}><Trash2 size={14} /> Discard draft</Button>
            <Button variant="text" onClick={() => setDiscarding(false)}>Keep</Button>
          </div>
        ) : (
          <Button variant="text" disabled={act.busy} onClick={() => setDiscarding(true)}>Discard draft…</Button>
        )}
        {!discarding && (
          <Button className="gap-2" disabled={act.busy || missing.length > 0} onClick={approve}>
            <FilePlus2 size={15} /> {act.busy ? 'Saving…' : chosen ? 'Approve: create chart & book' : 'Approve: create chart'}
          </Button>
        )}
      </div>
    </Section>
  )
}
