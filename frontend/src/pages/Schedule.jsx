import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle, CalendarDays, ChevronLeft, ChevronRight, Mail, Stethoscope } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardLabel } from '@/components/ui/Card'
import { CompactInput, CompactSelect } from '@/components/ui/Field'
import { Toast } from '@/components/ui/Overlay'
import { ErrorNote, PageHeader, Section, Spinner } from '@/components/ui/Page'
import { api, post, useAction, useApi } from '@/lib/api'
import { cn } from '@/lib/cn'
import { formatDate, formatTime, shiftIso, todayIsoKey } from '@/lib/format'

function BookingPanel({ day, providers, presetPatient, onBooked }) {
  const [q, setQ] = useState('')
  const [matches, setMatches] = useState([])
  const [patient, setPatient] = useState(null)
  const [providerId, setProviderId] = useState('')
  const [slots, setSlots] = useState(null)
  const [reason, setReason] = useState('')
  const act = useAction()

  useEffect(() => {
    if (presetPatient) api(`/patients/${presetPatient}`).then(setPatient).catch(() => {})
  }, [presetPatient])
  useEffect(() => {
    setSlots(null)
    if (providerId) api(`/providers/${providerId}/slots?day=${day}`).then(setSlots)
  }, [providerId, day])

  const search = async () => setMatches(await api(`/patients?q=${encodeURIComponent(q)}`))
  const book = (start) =>
    act.run(async () => {
      await post('/appointments', { patient_id: patient.id, provider_id: Number(providerId), start_at: start, visit_reason: reason || null })
      setSlots(await api(`/providers/${providerId}/slots?day=${day}`))
      onBooked(`Booked ${patient.full_name} at ${formatTime(start)}.`)
    })

  const payer = patient?.primary_payer || patient?.policies?.find((x) => x.is_primary && x.is_active)?.payer_name
  const prov = providers.find((p) => String(p.id) === providerId)
  const outOfNetwork = prov && payer && !prov.in_network_payers.includes(payer)

  return (
    <Section title={`Book for ${formatDate(day)}`} className="lg:sticky lg:top-6">
      {patient ? (
        <div className="flex items-center justify-between gap-2 rounded-lg border border-hairline bg-surface-subtle/60 px-3.5 py-2.5">
          <div>
            <p className="font-body text-sm font-medium text-ink">{patient.full_name}</p>
            <p className="font-body text-xs text-muted">
              {patient.mrn} · {payer || 'No insurance'}
            </p>
          </div>
          <Button variant="text" onClick={() => setPatient(null)}>
            Change
          </Button>
        </div>
      ) : (
        <>
          <div className="flex gap-2">
            <CompactInput className="flex-1" placeholder="Find patient…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} />
            <Button variant="outline" className="h-10 px-4 text-sm" onClick={search}>
              Search
            </Button>
          </div>
          {matches.length > 0 && (
            <div className="flex flex-col divide-y divide-hairline">
              {matches.map((p) => (
                <div key={p.id} className="flex items-center justify-between py-2">
                  <span className="font-body text-sm">
                    {p.full_name} <span className="text-muted">· {formatDate(p.date_of_birth)}</span>
                  </span>
                  <Button variant="outline" className="h-8 px-3 text-xs" onClick={() => setPatient(p)}>
                    Select
                  </Button>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <CompactSelect value={providerId} onChange={(e) => setProviderId(e.target.value)}>
        <option value="">Provider…</option>
        {providers.map((p) => (
          <option key={p.id} value={p.id}>
            {p.full_name} ({p.specialty})
          </option>
        ))}
      </CompactSelect>
      <CompactInput placeholder="Visit reason" value={reason} onChange={(e) => setReason(e.target.value)} />

      {outOfNetwork && (
        <div className="flex items-start gap-2 rounded-lg border border-warm-amber-border bg-warm-amber-soft px-3 py-2 font-body text-xs text-warm-amber">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          {prov.full_name} is not in network with {payer}. Confirm with the patient before booking.
        </div>
      )}

      {providerId &&
        (slots == null ? (
          <Spinner className="py-4" />
        ) : slots.length ? (
          <div className="grid grid-cols-3 gap-1.5">
            {slots.map((s) => (
              <button
                key={s.start_at}
                disabled={!patient || act.busy}
                onClick={() => book(s.start_at)}
                className="cursor-pointer rounded-md border border-hairline-strong bg-card px-2 py-1.5 font-body text-xs font-medium text-ink transition-colors hover:border-ink hover:bg-strong disabled:cursor-default disabled:opacity-40"
              >
                {s.label}
              </button>
            ))}
          </div>
        ) : (
          <p className="font-body text-xs text-muted">No open slots that day.</p>
        ))}
      {!patient && providerId && <p className="font-body text-xs text-muted">Select a patient to book a slot.</p>}
      <ErrorNote error={act.error} />
    </Section>
  )
}

export default function Schedule() {
  const [params] = useSearchParams()
  const [day, setDay] = useState(todayIsoKey())
  const [toast, setToast] = useState('')
  const { data: providers } = useApi('/providers')
  const { data: appts, reload, error } = useApi(`/appointments?start=${day}`, [day])

  const onBooked = (msg) => {
    reload()
    setToast(msg)
    setTimeout(() => setToast(''), 2600)
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Schedule module"
        title="Provider schedule"
        subtitle={formatDate(day)}
        actions={
          <div className="flex items-center gap-2">
            <button aria-label="Previous day" onClick={() => setDay(shiftIso(day, -1))} className="flex size-10 cursor-pointer items-center justify-center rounded-pill border border-hairline-strong hover:bg-strong">
              <ChevronLeft size={16} />
            </button>
            <CompactInput type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} />
            <button aria-label="Next day" onClick={() => setDay(shiftIso(day, 1))} className="flex size-10 cursor-pointer items-center justify-center rounded-pill border border-hairline-strong hover:bg-strong">
              <ChevronRight size={16} />
            </button>
            <Button variant="outline" onClick={() => setDay(todayIsoKey())}>
              Today
            </Button>
          </div>
        }
      />
      <ErrorNote error={error} />
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1fr_360px]">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {!providers ? (
            <Spinner />
          ) : (
            providers.map((p) => {
              const list = (appts || []).filter((a) => a.provider_id === p.id)
              return (
                <Card key={p.id} className="flex flex-col gap-3 px-5 py-4.5">
                  <div className="flex items-start gap-2.5">
                    <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-subtle text-muted">
                      <Stethoscope size={16} strokeWidth={1.75} />
                    </div>
                    <div>
                      <p className="font-display text-lg leading-tight text-ink">{p.full_name}</p>
                      <p className="font-body text-xs text-muted">{p.specialty}</p>
                    </div>
                  </div>
                  <p className="font-body text-[11px] text-muted">In network: {p.in_network_payers.join(', ')}</p>
                  <div className="flex flex-col gap-1.5">
                    {list.length ? (
                      list.map((a) => (
                        <div key={a.id} className="flex items-start gap-3 rounded-lg border border-hairline/70 bg-surface-subtle/60 px-3 py-2">
                          <span className="w-16 shrink-0 font-body text-xs font-semibold text-ink tabular-nums">{formatTime(a.start_at)}</span>
                          <div className="min-w-0 flex-1">
                            <Link to={`/patients/${a.patient_id}`} className="block truncate font-body text-sm font-medium text-ink">
                              {a.patient_name}
                            </Link>
                            <p className="truncate font-body text-xs text-muted">{a.visit_reason || '—'}</p>
                          </div>
                          {a.source_request_id && (
                            <Link to={`/requests/${a.source_request_id}`} title="Booked from an email request" className="hover:no-underline">
                              <Badge variant="info">
                                <Mail size={10} />
                                AI
                              </Badge>
                            </Link>
                          )}
                        </div>
                      ))
                    ) : (
                      <div className={cn('flex items-center gap-2 rounded-lg border border-dashed border-hairline-strong/80 px-3 py-4 font-body text-xs text-muted')}>
                        <CalendarDays size={14} /> No appointments
                      </div>
                    )}
                  </div>
                  <CardLabel className="pt-1">{list.length} booked</CardLabel>
                </Card>
              )
            })
          )}
        </div>
        {providers && <BookingPanel day={day} providers={providers} presetPatient={params.get('patient')} onBooked={onBooked} />}
      </div>
      <Toast message={toast} />
    </div>
  )
}
