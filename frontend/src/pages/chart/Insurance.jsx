import { useState } from 'react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { ChevronRight, Plus, ShieldPlus } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/Card'
import { ErrorNote, Section } from '@/components/ui/Page'
import { post, useAction } from '@/lib/api'
import { formatDate, formatMoney } from '@/lib/format'
import { PayerLookup, PlanLookup } from './PayerLookup'
import { SelectField, TextField } from './shared'

const ORDER_LABEL = { primary: 'Primary', secondary: 'Secondary', tertiary: 'Tertiary' }

function PolicyCard({ p, base }) {
  return (
    <Link to={`${base}/insurance/${p.id}`} className="group flex flex-col gap-2 rounded-xl border border-hairline bg-card p-4.5 shadow-subtle transition-all hover:border-hairline-strong hover:no-underline hover:shadow-soft">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.8px] text-muted">{ORDER_LABEL[p.coverage_order] || p.coverage_order} insurance</p>
          <p className="font-display text-lg leading-snug text-ink">{p.payer_label}</p>
        </div>
        <Badge variant={p.eligibility_status === 'active' ? 'success' : p.eligibility_status === 'inactive' ? 'error' : 'neutral'}>
          {p.eligibility_status || 'Not checked'}
        </Badge>
      </div>
      <p className="font-body text-sm text-body">{p.plan_name || 'Plan not set'} · {p.policy_type || '—'}</p>
      <p className="font-body text-xs text-muted">Policy {p.member_id} · Group {p.group_number || '—'} · Co-pay {p.copay_amount != null ? formatMoney(p.copay_amount) : '—'}</p>
      <span className="mt-1 inline-flex items-center gap-1 font-body text-xs font-medium text-muted group-hover:text-ink">Edit & check eligibility <ChevronRight size={13} /></span>
    </Link>
  )
}

export default function Insurance() {
  const { chart, setChart, notify } = useOutletContext()
  const navigate = useNavigate()
  const base = `/patients/${chart.id}`
  const active = chart.policies.filter((p) => p.is_active)
  const archived = chart.policies.filter((p) => !p.is_active)
  const [adding, setAdding] = useState(false)
  const [f, setF] = useState({ coverage_order: active.some((p) => p.coverage_order === 'primary') ? 'secondary' : 'primary' })
  const act = useAction()
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }))
  const add = () =>
    act.run(async () => {
      const updated = await post(`/patients/${chart.id}/insurance`, { ...f, payer_id: f.payer?.id, payer: undefined })
      setChart(updated)
      setAdding(false)
      notify('Policy added.')
      const created = updated.policies.find((p) => p.is_active && p.coverage_order === f.coverage_order)
      if (created) navigate(`${base}/insurance/${created.id}`)
    })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-xl text-ink">Insurance</h2>
        <Button className="h-9 gap-2 px-4 text-sm" onClick={() => setAdding((v) => !v)}><Plus size={14} /> {adding ? 'Close' : 'Add policy'}</Button>
      </div>

      {adding && (
        <Section title="New insurance policy">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            <SelectField label="Coverage" required value={f.coverage_order} onChange={set('coverage_order')} placeholder="—"
              options={[{ value: 'primary', label: 'Primary' }, { value: 'secondary', label: 'Secondary' }, { value: 'tertiary', label: 'Tertiary' }]} />
            <div className="xl:col-span-2"><PayerLookup value={f.payer} onChange={set('payer')} required /></div>
            <PlanLookup payerId={f.payer?.id} value={f.plan_name} onChange={(name, type) => setF((p) => ({ ...p, plan_name: name, policy_type: type || p.policy_type }))} />
            <TextField label="Policy Number" required value={f.member_id} onChange={set('member_id')} />
            <TextField label="Group Number" value={f.group_number} onChange={set('group_number')} />
            <SelectField label="Policy Type" required value={f.policy_type} onChange={set('policy_type')} options={['PPO', 'HMO', 'EPO', 'POS', 'HDHP', 'Medicare', 'Medicaid', 'Other']} />
            <SelectField label="Patient's Relationship to Policy Holder" value={f.relationship_to_subscriber || 'self'} onChange={set('relationship_to_subscriber')} placeholder="—"
              options={[{ value: 'self', label: 'Self' }, { value: 'spouse', label: 'Spouse' }, { value: 'child', label: 'Child' }, { value: 'other', label: 'Other' }]} />
          </div>
          <p className="font-body text-xs text-muted">Adding a policy in a coverage slot that's already filled archives the existing one.</p>
          <div><Button disabled={act.busy || !f.payer || !f.member_id} onClick={add}>Save policy</Button></div>
          <ErrorNote error={act.error} />
        </Section>
      )}

      {active.length ? (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">{active.map((p) => <PolicyCard key={p.id} p={p} base={base} />)}</div>
      ) : (
        <EmptyState icon={ShieldPlus} title="No active insurance">Add the patient's primary medical insurance to run eligibility.</EmptyState>
      )}

      {archived.length > 0 && (
        <Section title={`Archived policies (${archived.length})`}>
          <div className="flex flex-col divide-y divide-hairline">
            {archived.map((p) => (
              <Link key={p.id} to={`${base}/insurance/${p.id}`} className="flex flex-wrap items-center justify-between gap-2 py-2.5 opacity-75 hover:no-underline hover:opacity-100">
                <span className="font-body text-sm text-ink">{p.payer_label} · {p.member_id}</span>
                <span className="font-body text-xs text-muted">Archived {formatDate(p.archived_at)} by {p.archived_by}</span>
              </Link>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}
