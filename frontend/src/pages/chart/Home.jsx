import { useEffect } from 'react'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import { Printer } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Detail, Section } from '@/components/ui/Page'
import { formatDate, formatMoney, formatSlot } from '@/lib/format'
import { appointmentVariant, intentLabel, requestStatus } from '@/lib/status'

/** Patient Home: chart summary. Also the printable Face Sheet (?print=1). */
export default function ChartHome() {
  const { chart } = useOutletContext()
  const [params, setParams] = useSearchParams()
  const primary = chart.policies.find((p) => p.is_active && p.coverage_order === 'primary')
  const upcoming = chart.appointments.filter((a) => a.status === 'booked').slice(0, 5)
  const lastCheck = chart.eligibility_checks[0]

  useEffect(() => {
    if (params.get('print') !== '1') return
    const t = setTimeout(() => {
      window.print()
      setParams({}, { replace: true })
    }, 300)
    return () => clearTimeout(t)
  }, [params, setParams])

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between print:hidden">
        <h2 className="font-display text-xl text-ink">Patient Home</h2>
        <Button variant="outline" className="h-9 gap-2 px-4 text-sm" onClick={() => setParams({ print: '1' })}>
          <Printer size={14} /> Print face sheet
        </Button>
      </div>

      <Section title="Face sheet">
        <div className="grid grid-cols-1 gap-x-6 gap-y-3.5 sm:grid-cols-3">
          <Detail label="Name">{chart.display_name}</Detail>
          <Detail label="Date of birth">{formatDate(chart.date_of_birth)}</Detail>
          <Detail label="MRN">{chart.mrn}</Detail>
          <Detail label="Phone">{chart.phone}</Detail>
          <Detail label="Email">{chart.email}</Detail>
          <Detail label="Address">
            {[chart.address_line1, chart.address_line2, chart.city, chart.state, chart.postal_code].filter(Boolean).join(', ')}
          </Detail>
          <Detail label="Guarantor">
            {chart.guarantor?.same_as_patient ? 'Self' : `${chart.guarantor?.first_name ?? ''} ${chart.guarantor?.last_name ?? ''} (${chart.guarantor?.relationship_to_patient ?? '—'})`}
          </Detail>
          <Detail label="SSN">{chart.ssn_masked}</Detail>
          <Detail label="Marital status">{chart.marital_status?.replace('_', ' ')}</Detail>
        </div>
      </Section>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Section
          title="Primary insurance"
          actions={primary && <Link to={`insurance/${primary.id}`} className="font-body text-xs text-muted hover:text-ink print:hidden">Open</Link>}
        >
          {primary ? (
            <div className="grid grid-cols-2 gap-x-5 gap-y-3">
              <Detail label="Payer">{primary.payer_label}</Detail>
              <Detail label="Plan">{primary.plan_name}</Detail>
              <Detail label="Policy number">{primary.member_id}</Detail>
              <Detail label="Policy type">{primary.policy_type}</Detail>
              <Detail label="Co-pay">{primary.copay_amount != null ? formatMoney(primary.copay_amount) : null}</Detail>
              <Detail label="Remaining deductible">
                {primary.remaining_deductible != null ? formatMoney(primary.remaining_deductible) : null}
              </Detail>
              <Detail label="Eligibility">
                <Badge variant={primary.eligibility_status === 'active' ? 'success' : primary.eligibility_status === 'inactive' ? 'error' : 'neutral'}>
                  {primary.eligibility_status || 'Not checked'}
                </Badge>
              </Detail>
              <Detail label="Last checked">{lastCheck ? new Date(lastCheck.checked_at).toLocaleString() : null}</Detail>
              {lastCheck?.ai_summary && (
                <p className="col-span-2 rounded-lg border border-slate-border bg-slate-soft px-3.5 py-2.5 font-body text-xs leading-relaxed text-slate-blue">
                  {lastCheck.ai_summary}
                </p>
              )}
            </div>
          ) : (
            <p className="font-body text-sm text-error">No active insurance on file.</p>
          )}
        </Section>

        <Section title="Upcoming appointments">
          {upcoming.length ? (
            <div className="flex flex-col divide-y divide-hairline">
              {upcoming.map((a) => (
                <div key={a.id} className="flex items-center justify-between gap-2 py-2">
                  <div>
                    <p className="font-body text-sm font-medium text-ink">{formatSlot(a.start_at)}</p>
                    <p className="font-body text-xs text-muted">{a.provider_name} · {a.visit_reason || '—'}</p>
                  </div>
                  <Badge variant={appointmentVariant(a.status)}>{a.status}</Badge>
                </div>
              ))}
            </div>
          ) : (
            <p className="font-body text-sm text-muted">No upcoming appointments.</p>
          )}
        </Section>
      </div>

      <Section title="Recent portal requests" className="print:hidden">
        {chart.requests.length ? (
          <div className="flex flex-col divide-y divide-hairline">
            {chart.requests.slice(0, 5).map((r) => {
              const s = requestStatus(r.status)
              return (
                <Link key={r.id} to={`/requests/${r.id}`} className="flex items-center justify-between gap-3 py-2.5 hover:no-underline">
                  <span className="font-body text-sm font-medium text-ink">{r.reference}</span>
                  <span className="flex-1 truncate font-body text-xs text-muted">{intentLabel(r.intent)} · {r.subject}</span>
                  <Badge variant={s.variant}>{s.label}</Badge>
                </Link>
              )
            })}
          </div>
        ) : (
          <p className="font-body text-sm text-muted">No requests from this patient.</p>
        )}
      </Section>
    </div>
  )
}
