import { useMemo, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, Sparkles, X } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { CompactSelect } from '@/components/ui/Field'
import { ErrorNote, Spinner } from '@/components/ui/Page'
import { useApi } from '@/lib/api'
import { cn } from '@/lib/cn'
import { formatDate, formatMoney } from '@/lib/format'

export const CATEGORY_ORDER = [
  ['co_payment', 'Co-Payment'],
  ['co_insurance', 'Co-Insurance'],
  ['deductible', 'Deductible'],
  ['remaining_deductible', 'Remaining Deductible'],
  ['out_of_pocket', 'Out of Pocket'],
  ['remaining_out_of_pocket', 'Remaining Out of Pocket'],
]

export const benefitValue = (b) =>
  b.percent !== null && b.percent !== undefined ? `${Number(b.percent)}%` : formatMoney(b.amount)

function HeaderItem({ label, children, tone }) {
  return (
    <div className="flex min-w-0 gap-1.5 font-body text-xs">
      <span className="shrink-0 font-semibold text-ink">{label}:</span>
      <span className={cn('min-w-0 break-words', tone === 'ok' ? 'text-sage' : tone === 'bad' ? 'text-error' : 'text-body')}>
        {children || '—'}
      </span>
    </div>
  )
}

function DetailedView({ e }) {
  const [network, setNetwork] = useState('in')
  const [draft, setDraft] = useState([])
  const [applied, setApplied] = useState([])
  const [open, setOpen] = useState({ co_payment: true })
  const serviceTypes = useMemo(() => [...new Set(e.benefits.map((b) => b.service_type))].sort(), [e])
  const rows = e.benefits.filter((b) => b.network === network && (!applied.length || applied.includes(b.service_type)))

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex min-w-[260px] flex-1 flex-col gap-1">
          <span className="font-body text-xs font-semibold text-muted">Service Type</span>
          <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-md border border-hairline-strong bg-card px-2 py-1.5">
            {draft.map((s) => (
              <span key={s} className="inline-flex items-center gap-1 rounded-pill bg-strong px-2 py-0.5 font-body text-xs text-ink">
                {s}
                <button aria-label={`Remove ${s}`} className="cursor-pointer text-muted hover:text-error" onClick={() => setDraft(draft.filter((d) => d !== s))}><X size={11} /></button>
              </span>
            ))}
            <CompactSelect aria-label="Add service type" value="" onChange={(ev) => ev.target.value && setDraft([...new Set([...draft, ev.target.value])])}
              className="min-w-[160px] flex-1 border-none px-1 py-0.5 text-xs">
              <option value="">{draft.length ? 'Add…' : 'All service types'}</option>
              {serviceTypes.filter((s) => !draft.includes(s)).map((s) => <option key={s}>{s}</option>)}
            </CompactSelect>
          </div>
        </div>
        <Button variant="outline" className="h-10 px-4 text-sm" disabled={JSON.stringify(draft) === JSON.stringify(applied)} onClick={() => setApplied(draft)}>Apply Filters</Button>
        <Button variant="outline" className="h-10 px-4 text-sm" onClick={() => { setDraft([]); setApplied([]) }}>Clear Filters</Button>
      </div>

      <div className="flex gap-1 border-b border-hairline">
        {[['in', 'In Network'], ['out', 'Out of Network']].map(([k, l]) => (
          <button key={k} onClick={() => setNetwork(k)}
            className={cn('-mb-px cursor-pointer rounded-t-md border px-4 py-2 font-body text-sm',
              network === k ? 'border-hairline border-b-card bg-card font-medium text-ink' : 'border-transparent text-muted hover:text-ink')}>
            {l}
          </button>
        ))}
      </div>

      <div className="flex flex-col gap-1.5">
        {CATEGORY_ORDER.map(([cat, label]) => {
          const list = rows.filter((b) => b.category === cat)
          const isOpen = !!open[cat] && list.length > 0
          return (
            <div key={cat} className="overflow-hidden rounded-lg border border-hairline">
              <button
                onClick={() => setOpen({ ...open, [cat]: !open[cat] })}
                disabled={!list.length}
                className="flex w-full cursor-pointer items-center gap-2 bg-surface-subtle px-4 py-2.5 text-left font-body text-sm font-medium text-ink disabled:cursor-default disabled:text-muted"
              >
                {isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                {label} ({list.length})
              </button>
              {isOpen && (
                <div className="overflow-x-auto">
                  <table className="w-full font-body text-xs">
                    <thead>
                      <tr className="border-b border-hairline text-left text-muted">
                        <th className="px-4 py-2 font-semibold">Service Type</th>
                        <th className="px-4 py-2 font-semibold">Level</th>
                        <th className="px-4 py-2 font-semibold">Amount</th>
                        <th className="px-4 py-2 font-semibold">Auth/Cert Req'd</th>
                        <th className="px-4 py-2 font-semibold">Message</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-hairline">
                      {list.map((b) => (
                        <tr key={b.id} className="align-top">
                          <td className="px-4 py-2 text-ink">{b.service_type}</td>
                          <td className="px-4 py-2 capitalize text-muted">{b.coverage_level}</td>
                          <td className="px-4 py-2 font-medium text-ink tabular-nums">{benefitValue(b)}</td>
                          <td className="px-4 py-2">{b.auth_required ? <Badge variant="warning">Required</Badge> : b.auth_required === false ? 'No' : '—'}</td>
                          <td className="px-4 py-2 text-body">{b.message || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )
        })}
        {!rows.length && <p className="py-4 text-center font-body text-sm text-muted">No {network === 'in' ? 'in-network' : 'out-of-network'} benefits returned{applied.length ? ' for the selected service types' : ''}.</p>}
      </div>
    </div>
  )
}

function Discrepancies({ e }) {
  if (!e.discrepancies.length) {
    return <p className="rounded-lg border border-sage-border bg-sage-soft px-4 py-3 font-body text-sm text-sage">No discrepancies: the payer's data matches the chart and the policy on file.</p>
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-hairline">
      <table className="w-full font-body text-sm">
        <thead><tr className="border-b border-hairline bg-surface-subtle text-left text-xs text-muted">
          <th className="px-4 py-2 font-semibold">Field</th><th className="px-4 py-2 font-semibold">On chart</th>
          <th className="px-4 py-2 font-semibold">Payer response</th><th className="px-4 py-2 font-semibold">Action</th>
        </tr></thead>
        <tbody className="divide-y divide-hairline">
          {e.discrepancies.map((d, i) => (
            <tr key={i} className="align-top">
              <td className="px-4 py-2.5 font-medium text-ink">
                <Badge variant={d.severity === 'error' ? 'error' : d.severity === 'info' ? 'info' : 'warning'} className="mr-2">{d.severity}</Badge>{d.field}
              </td>
              <td className="px-4 py-2.5 text-body">{d.chart || '—'}</td>
              <td className="px-4 py-2.5 text-body">{d.payer || '—'}</td>
              <td className="px-4 py-2.5 text-xs text-muted">{d.note || 'Verify with the patient and update the chart if needed.'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** "Eligibility Response (Received: …)": detailed benefits, original 271, discrepancies. */
export function EligibilityResponseDialog({ patientId, checkId, onClose }) {
  const { data: e, error } = useApi(`/patients/${patientId}/eligibility/${checkId}`, [checkId])
  const [tab, setTab] = useState('detail')
  const received = e ? formatDate(e.checked_at) : ''
  const statusTone = e?.coverage_status === 'active' ? 'ok' : 'bad'

  return (
    <Dialog title={`Eligibility Response${e ? ` (Received: ${received})` : ''}`} onClose={onClose} size="xl">
      <ErrorNote error={error} />
      {!e ? <Spinner /> : (
        <>
          <div className="grid grid-cols-1 gap-x-6 gap-y-1.5 border-b border-hairline pb-4 sm:grid-cols-2 xl:grid-cols-4">
            <HeaderItem label="Checked">{new Date(e.checked_at).toLocaleString()}</HeaderItem>
            <HeaderItem label="Date of Service">{formatDate(e.date_of_service)}</HeaderItem>
            <HeaderItem label="Patient Name">{e.patient_name}</HeaderItem>
            <HeaderItem label="Policy ID">{e.policy_number}</HeaderItem>
            <HeaderItem label="Status" tone={statusTone}>{e.coverage_status?.replace('_', ' ')}</HeaderItem>
            <HeaderItem label="Provider">{e.provider_name || `Group NPI`}</HeaderItem>
            <HeaderItem label="Date of Birth">{formatDate(e.patient_dob)}</HeaderItem>
            <HeaderItem label="Plan Dates">{e.plan_start ? `${formatDate(e.plan_start)} – ${formatDate(e.plan_end)}` : null}</HeaderItem>
            <HeaderItem label="Eligibility Dates">{e.eligibility_start ? `${formatDate(e.eligibility_start)} – ${e.eligibility_end ? formatDate(e.eligibility_end) : 'ongoing'}` : null}</HeaderItem>
            <HeaderItem label="NPI">{e.npi} ({e.npi_type})</HeaderItem>
            <HeaderItem label="Payer">{e.payer_name}</HeaderItem>
            <HeaderItem label="Plan Name">{e.plan_name}{e.plan_type ? ` · ${e.plan_type}` : ''}</HeaderItem>
          </div>

          {e.ai_summary && (
            <div className="flex gap-2.5 rounded-lg border border-slate-border bg-slate-soft px-4 py-3">
              <Sparkles size={16} className="mt-0.5 shrink-0 text-slate-blue" />
              <p className="font-body text-sm leading-relaxed text-slate-blue">{e.ai_summary}</p>
            </div>
          )}

          <div className="flex gap-1 border-b-2 border-ink/80">
            {[['detail', 'Detailed View'], ['raw', 'Original Response'], ['disc', 'Discrepancies']].map(([k, l]) => (
              <button key={k} onClick={() => setTab(k)}
                className={cn('flex cursor-pointer items-center gap-1.5 rounded-t-md px-4 py-2 font-body text-sm',
                  tab === k ? 'bg-primary text-on-primary' : 'text-muted hover:text-ink')}>
                {k === 'disc' && e.discrepancies.length > 0 && <AlertTriangle size={14} className={tab === k ? '' : 'text-error'} />}
                {l}{k === 'disc' && e.discrepancies.length > 0 ? ` (${e.discrepancies.length})` : ''}
              </button>
            ))}
          </div>

          {tab === 'detail' && (e.benefits.length ? <DetailedView e={e} /> : <p className="font-body text-sm text-muted">{e.message}</p>)}
          {tab === 'raw' && (
            <pre className="max-h-[480px] overflow-auto rounded-lg bg-canvas-deep p-4 font-mono text-[11px] leading-relaxed text-on-dark">{e.raw_x12 || e.message}</pre>
          )}
          {tab === 'disc' && <Discrepancies e={e} />}
        </>
      )}
    </Dialog>
  )
}
