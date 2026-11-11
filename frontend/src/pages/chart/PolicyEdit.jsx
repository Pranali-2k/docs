import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useOutletContext, useParams } from 'react-router-dom'
import { ArrowLeft, Archive, CheckCircle2, Info, Printer, RefreshCw, ShieldCheck } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { CompactInput, CompactSelect, CompactTextArea } from '@/components/ui/Field'
import { ConfirmDialog } from '@/components/ui/Overlay'
import { ErrorNote, Spinner } from '@/components/ui/Page'
import { api, patch, post, useAction, useApi } from '@/lib/api'
import { cn } from '@/lib/cn'
import { formatDate, formatMoney, todayIsoKey } from '@/lib/format'
import { EligibilityResponseDialog, benefitValue } from './EligibilityResponse'
import { PayerLookup, PlanLookup } from './PayerLookup'
import { ChangeList, FormField, SelectField, TextField } from './shared'

const POLICY_TYPES = ['PPO', 'HMO', 'EPO', 'POS', 'HDHP', 'Medicare', 'Medicaid', 'Other']
const EDIT_KEYS = [
  'member_id', 'group_number', 'plan_name', 'policy_type', 'claims_address', 'notes', 'requires_referral',
  'requires_precert_inpatient', 'requires_preauth_outpatient', 'relationship_to_subscriber', 'holder_first_name',
  'holder_last_name', 'holder_date_of_birth', 'eligibility_status', 'copay_amount', 'coinsurance_pct', 'deductible',
  'remaining_deductible', 'out_of_pocket', 'remaining_out_of_pocket', 'effective_date', 'end_date',
]
const MONEY = [
  ['copay_amount', 'Co-Pay Amount', 'co_payment', '$'],
  ['coinsurance_pct', 'Co-Insurance %', 'co_insurance', '%'],
  ['deductible', 'Deductible', 'deductible', '$'],
  ['remaining_deductible', 'Remaining Deductible', 'remaining_deductible', '$'],
  ['out_of_pocket', 'Out of Pocket', 'out_of_pocket', '$'],
  ['remaining_out_of_pocket', 'Remaining Out of Pocket', 'remaining_out_of_pocket', '$'],
]
const ORDER_TITLE = { primary: 'Primary', secondary: 'Secondary', tertiary: 'Tertiary' }

function Panel({ title, actions, children, className }) {
  return (
    <section className={cn('rounded-xl border border-hairline bg-card shadow-card', className)}>
      <div className="flex items-center justify-between gap-3 rounded-t-xl border-b border-hairline bg-surface-subtle px-5 py-3">
        <p className="font-display text-lg text-ink">{title}</p>
        {actions}
      </div>
      {children}
    </section>
  )
}

/** "Select" next to an eligibility field: pick a value from the latest 271. */
function SelectFromResponse({ benefits, category, unit, onPick, disabled }) {
  const [open, setOpen] = useState(false)
  const box = useRef(null)
  useEffect(() => {
    const close = (e) => box.current && !box.current.contains(e.target) && setOpen(false)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])
  const options = (benefits || []).filter((b) => b.category === category && b.coverage_level === 'individual')
    .sort((a, b) => (a.network === b.network ? 0 : a.network === 'in' ? -1 : 1))
  return (
    <div ref={box} className="relative">
      <button type="button" disabled={disabled || !options.length} onClick={() => setOpen((v) => !v)}
        title={options.length ? 'Pick a value from the latest eligibility response' : 'Run an eligibility check first'}
        className="cursor-pointer font-body text-xs font-medium text-slate-blue hover:underline disabled:cursor-default disabled:text-muted-soft disabled:no-underline">
        Select
      </button>
      {open && (
        <ul className="absolute right-0 z-30 mt-1 max-h-72 w-[340px] overflow-auto rounded-lg border border-hairline bg-card py-1 shadow-elevated">
          {options.map((b) => (
            <li key={b.id}>
              <button type="button" onClick={() => { onPick(b); setOpen(false) }}
                className="flex w-full cursor-pointer items-start justify-between gap-3 px-3 py-2 text-left hover:bg-surface-subtle">
                <span className="min-w-0">
                  <span className="block font-body text-xs font-medium text-ink">{b.service_type}</span>
                  <span className="block truncate font-body text-[11px] text-muted">{b.network === 'in' ? 'In network' : 'Out of network'} · {b.message || '—'}</span>
                </span>
                <span className="shrink-0 font-body text-sm font-semibold text-ink tabular-nums">{benefitValue(b)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <span className="sr-only">{unit}</span>
    </div>
  )
}

function ReplaceDialog({ policy, onClose, onDone }) {
  const [f, setF] = useState({ relationship_to_subscriber: 'self' })
  const act = useAction()
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }))
  const submit = () => act.run(async () => onDone(await post(`/patients/${policy.patient.id}/policies/${policy.id}/replace`,
    { ...f, payer_id: f.payer?.id, payer: undefined })))
  return (
    <Dialog title={`Archive & create new ${policy.coverage_order}`} subtitle={`${policy.payer_label} · ${policy.member_id} will be archived and kept in history`} onClose={onClose} size="md" headerTone="light"
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button disabled={act.busy || !f.payer || !f.member_id} onClick={submit}>Archive & create</Button></>}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2"><PayerLookup value={f.payer} onChange={set('payer')} required /></div>
        <PlanLookup payerId={f.payer?.id} value={f.plan_name} onChange={(n, t) => setF((p) => ({ ...p, plan_name: n, policy_type: t || p.policy_type }))} />
        <SelectField label="Policy Type" required value={f.policy_type} onChange={set('policy_type')} options={POLICY_TYPES} />
        <TextField label="Policy Number" required value={f.member_id} onChange={set('member_id')} />
        <TextField label="Group Number" value={f.group_number} onChange={set('group_number')} />
        <TextField label="Effective Date" type="date" value={f.effective_date} onChange={set('effective_date')} />
      </div>
      <ErrorNote error={act.error} />
    </Dialog>
  )
}

export default function PolicyEdit() {
  const { policyId } = useParams()
  const { chart, setChart, notify } = useOutletContext()
  const location = useLocation()
  const navigate = useNavigate()
  const { data: policy, setData: setPolicy, error } = useApi(`/patients/${chart.id}/policies/${policyId}`, [policyId])
  const { data: providers } = useApi('/providers')
  const { data: payers } = useApi('/payers')
  const [f, setF] = useState(null)
  const [payer, setPayer] = useState(null)
  const [check, setCheck] = useState({ npi_type: 'individual', provider_id: '', service_date: todayIsoKey() })
  const [latest, setLatest] = useState(null) // latest eligibility response (with benefits)
  const [showResponse, setShowResponse] = useState(null)
  const [showHistory, setShowHistory] = useState(false)
  const [confirm, setConfirm] = useState(null)
  const [replacing, setReplacing] = useState(false)
  const save = useAction()
  const elig = useAction()
  const eligRef = useRef(null)

  const original = useMemo(() => {
    if (!policy) return null
    const o = {}
    EDIT_KEYS.forEach((k) => (o[k] = policy[k] ?? null))
    return o
  }, [policy])

  useEffect(() => {
    if (!policy) return
    setF(original)
    setPayer(payers?.find((p) => p.id === policy.payer_id) || { id: policy.payer_id, label: policy.payer_label, financial_category: policy.financial_category })
    setCheck((c) => ({ ...c, provider_id: c.provider_id || String(chart.preferred_provider_id || '') }))
  }, [policy, original, payers, chart.preferred_provider_id])

  useEffect(() => {
    if (!policy?.last_eligibility_check_id) return setLatest(null)
    api(`/patients/${chart.id}/eligibility/${policy.last_eligibility_check_id}`).then(setLatest).catch(() => setLatest(null))
  }, [policy?.last_eligibility_check_id, chart.id])

  useEffect(() => {
    if (location.hash === '#eligibility' && f) eligRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [location.hash, f])

  if (error) return <ErrorNote error={error} />
  if (!policy || !f) return <Spinner />

  const readOnly = !policy.is_active
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }))
  const diff = {}
  EDIT_KEYS.forEach((k) => {
    const a = f[k] === '' ? null : f[k]
    const b = original[k]
    if (String(a ?? '') !== String(b ?? '')) diff[k] = MONEY.some(([m]) => m === k) && a !== null ? Number(a) : a
  })
  if (payer && payer.id !== policy.payer_id) diff.payer_id = payer.id
  const dirty = Object.keys(diff).length > 0
  const accepted = payer && providers?.some((p) => p.in_network_payers.includes(payer.name))

  const onSave = () => save.run(async () => {
    const updated = await patch(`/patients/${chart.id}/policies/${policy.id}`, diff)
    setPolicy(updated)
    setChart(await api(`/patients/${chart.id}`))
    notify('Policy saved.')
  })

  const runCheck = () => elig.run(async () => {
    const body = { npi_type: check.npi_type, service_date: check.service_date || null,
      provider_id: check.npi_type === 'individual' && check.provider_id ? Number(check.provider_id) : null }
    const res = await post(`/patients/${chart.id}/policies/${policy.id}/eligibility`, body)
    setLatest(res)
    setPolicy(await api(`/patients/${chart.id}/policies/${policy.id}`))
    setChart(await api(`/patients/${chart.id}`))
    notify(`Eligibility: ${res.coverage_status}${res.discrepancies.length ? ` · ${res.discrepancies.length} discrepancy(ies)` : ''}.`)
  })

  const pick = (b) => save.run(async () => {
    if (dirty) throw new Error('Save or cancel your other changes before selecting a value.')
    const updated = await post(`/patients/${chart.id}/policies/${policy.id}/select-benefit`, { benefit_id: b.id })
    setPolicy(updated)
    notify('Value copied from the eligibility response.')
  })

  const archive = () => setConfirm({
    title: 'Archive this policy?',
    body: `${policy.payer_label} · ${policy.member_id} becomes read-only and stays in the chart's history. The patient will have no active ${policy.coverage_order} insurance until you add one.`,
    cta: 'Archive policy',
    onYes: () => save.run(async () => {
      setConfirm(null)
      setChart(await post(`/patients/${chart.id}/policies/${policy.id}/archive`, { reason: 'archived from insurance form' }))
      setPolicy(await api(`/patients/${chart.id}/policies/${policy.id}`))
      notify('Policy archived.')
    }),
  })

  const lastChecked = policy.eligibility_checks[0]

  return (
    <div className="flex flex-col gap-4 print:gap-2">
      {/* Title bar: Back · Edit Primary Insurance · Print · Save */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link to={`/patients/${chart.id}/insurance`} className="inline-flex items-center gap-1.5 font-body text-sm text-muted hover:text-ink print:hidden">
          <ArrowLeft size={15} /> Back
        </Link>
        <h2 className="font-display text-2xl text-ink">{readOnly ? 'Archived' : 'Edit'} {ORDER_TITLE[policy.coverage_order]} Insurance</h2>
        <div className="flex items-center gap-2 print:hidden">
          <Button variant="outline" className="h-9 gap-2 px-4 text-sm" onClick={() => window.print()}><Printer size={14} /> Print</Button>
          {!readOnly && <Button variant="outline" className="h-9 px-4 text-sm" disabled={!dirty || save.busy} onClick={() => setF(original)}>Cancel</Button>}
          {!readOnly && <Button className="h-9 px-5 text-sm" disabled={!dirty || save.busy} onClick={onSave}>{save.busy ? 'Saving…' : 'Save'}</Button>}
        </div>
      </div>
      <ErrorNote error={save.error} />
      {readOnly && (
        <div className="rounded-lg border border-hairline bg-surface-subtle px-4 py-3 font-body text-sm text-body">
          Archived {formatDate(policy.archived_at)} by {policy.archived_by}. Archived policies are read-only.
        </div>
      )}

      {/* Policy */}
      <Panel title={`${ORDER_TITLE[policy.coverage_order]} Insurance Policy`}
        actions={<button onClick={() => setShowHistory(true)} className="cursor-pointer font-body text-sm text-slate-blue hover:underline">View History</button>}>
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px]">
          <fieldset disabled={readOnly} className="grid grid-cols-1 content-start gap-4 p-5 sm:grid-cols-2">
            {payer && (
              <p className={cn('flex items-center gap-1.5 font-body text-xs sm:col-span-2', accepted ? 'text-sage' : 'text-warm-amber')}>
                {accepted ? <CheckCircle2 size={14} /> : <Info size={14} />}
                {accepted ? 'Payer insurance is accepted.' : 'No provider at this practice is in network with this payer.'}
              </p>
            )}
            <div className="sm:col-span-2"><PayerLookup value={payer} onChange={setPayer} required /></div>
            <FormField label="Financial Category"><p className="py-2 font-body text-sm text-ink">{payer?.financial_category || '—'}</p></FormField>
            <div />
            <PlanLookup payerId={payer?.id} value={f.plan_name} onChange={(n, t) => setF((p) => ({ ...p, plan_name: n, policy_type: t || p.policy_type }))} />
            <TextField label="Policy Number" required value={f.member_id} onChange={set('member_id')} />
            <TextField label="Group Number" value={f.group_number} onChange={set('group_number')} />
            <SelectField label="Policy Type" required value={f.policy_type} onChange={set('policy_type')} options={POLICY_TYPES} />
            <SelectField label="Claims Submission Address(es) On File" value={f.claims_address} onChange={set('claims_address')}
              options={payer?.claims_addresses || []} placeholder="-- Choose One --" className="sm:col-span-2" />
            <FormField label="Notes" htmlFor="policy-notes" className="sm:col-span-2" hint={`Characters remaining: ${500 - (f.notes?.length || 0)}`}>
              <CompactTextArea id="policy-notes" rows={3} maxLength={500} value={f.notes ?? ''} onChange={(e) => set('notes')(e.target.value)} />
            </FormField>
          </fieldset>

          <div className="flex flex-col gap-4 rounded-b-xl border-t border-hairline bg-surface-subtle/60 p-5 lg:rounded-bl-none lg:border-t-0 lg:border-l">
            <p className="font-body text-sm font-semibold text-ink">Authorization requirements:</p>
            <fieldset disabled={readOnly} className="flex flex-col gap-2.5 font-body text-sm text-body">
              {[['requires_referral', 'Referral/Authorization for office visit'], ['requires_precert_inpatient', 'Pre-Cert for In-Patient Services'],
                ['requires_preauth_outpatient', 'Pre-Auth for Out-Patient Services']].map(([k, l]) => (
                <label key={k} className="flex cursor-pointer items-center gap-2">
                  <input type="checkbox" className="accent-primary" checked={!!f[k]} onChange={(e) => set(k)(e.target.checked)} /> {l}
                </label>
              ))}
            </fieldset>
            <div className="flex gap-2.5 rounded-lg border border-hairline bg-card px-3 py-2.5">
              <Info size={16} className="mt-0.5 shrink-0 text-muted" />
              <p className="font-body text-xs text-body">Requirements are checked against the eligibility response: services marked "Auth/Cert Req'd" need prior authorization.</p>
            </div>
            {!readOnly && (
              <div className="flex flex-col gap-2 print:hidden">
                <Button className="h-9 gap-2 text-sm" onClick={archive}><Archive size={14} /> Archive This Policy</Button>
                <Button variant="outline" className="h-9 text-sm" onClick={() => setReplacing(true)}>Archive & Create New {ORDER_TITLE[policy.coverage_order]}</Button>
              </div>
            )}
            <p className="text-center font-body text-xs text-muted">Last {lastChecked ? `${lastChecked.coverage_status} · ${formatDate(lastChecked.checked_at)}` : 'N/A'}</p>
          </div>
        </div>
      </Panel>

      {/* Policy holder */}
      <Panel title="Policy Holder">
        <fieldset disabled={readOnly} className="grid grid-cols-1 gap-4 p-5 sm:grid-cols-2 xl:grid-cols-4">
          <SelectField label="Patient's Relationship to Policy Holder" required value={f.relationship_to_subscriber} onChange={set('relationship_to_subscriber')} placeholder="—"
            options={[{ value: 'self', label: 'Self' }, { value: 'spouse', label: 'Spouse' }, { value: 'child', label: 'Child' }, { value: 'other', label: 'Other' }]} className="xl:col-span-2" />
          {f.relationship_to_subscriber && f.relationship_to_subscriber !== 'self' && (
            <>
              <TextField label="Holder First Name" value={f.holder_first_name} onChange={set('holder_first_name')} />
              <TextField label="Holder Last Name" value={f.holder_last_name} onChange={set('holder_last_name')} />
              <TextField label="Holder Date of Birth" type="date" value={f.holder_date_of_birth} onChange={set('holder_date_of_birth')} />
            </>
          )}
        </fieldset>
      </Panel>

      {/* Eligibility */}
      <div ref={eligRef} id="eligibility">
        <Panel title="Eligibility Information">
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px]">
            <fieldset disabled={readOnly} className="grid grid-cols-1 content-start gap-4 p-5 sm:grid-cols-2">
              <SelectField label="Eligibility Status" value={f.eligibility_status} onChange={set('eligibility_status')} placeholder="—"
                options={[{ value: 'active', label: 'Active' }, { value: 'inactive', label: 'Inactive' }, { value: 'unknown', label: 'Unknown' }]} />
              <div className="hidden sm:block" />
              {MONEY.map(([key, label, category, unit]) => (
                <FormField key={key} label={label} htmlFor={`m-${key}`}>
                  <div className="flex items-center gap-2">
                    <div className="relative flex-1">
                      <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 font-body text-xs text-muted">{unit}</span>
                      <CompactInput id={`m-${key}`} type="number" min="0" step="0.01" className="w-full pl-7"
                        value={f[key] ?? ''} onChange={(e) => set(key)(e.target.value === '' ? null : e.target.value)} />
                    </div>
                    <SelectFromResponse benefits={latest?.policy_id === policy.id ? latest.benefits : []} category={category} unit={unit}
                      onPick={pick} disabled={readOnly} />
                  </div>
                </FormField>
              ))}
              <TextField label="Policy Effective Date" type="date" value={f.effective_date} onChange={set('effective_date')} />
              <TextField label="Policy End Date" type="date" value={f.end_date} onChange={set('end_date')} />
            </fieldset>

            <div className="flex flex-col gap-3.5 rounded-b-xl border-t border-hairline bg-surface-subtle/60 p-5 lg:rounded-bl-none lg:border-t-0 lg:border-l print:hidden">
              <p className="font-body text-sm font-semibold text-ink">Check Eligibility By</p>
              <div className="flex gap-5 font-body text-sm">
                {[['group', 'Group NPI'], ['individual', 'Individual NPI']].map(([k, l]) => (
                  <label key={k} className="flex cursor-pointer items-center gap-1.5">
                    <input type="radio" name="npi-type" className="accent-primary" checked={check.npi_type === k}
                      onChange={() => setCheck((c) => ({ ...c, npi_type: k }))} /> {l}
                  </label>
                ))}
              </div>
              {check.npi_type === 'individual' ? (
                <CompactSelect aria-label="Rendering provider" value={check.provider_id} onChange={(e) => setCheck((c) => ({ ...c, provider_id: e.target.value }))}>
                  <option value="">Choose provider…</option>
                  {providers?.map((p) => <option key={p.id} value={p.id}>{p.full_name.replace('Dr. ', '')}, MD ({p.npi})</option>)}
                </CompactSelect>
              ) : (
                <p className="rounded-md border border-hairline bg-card px-3 py-2 font-body text-xs text-body">Practice group NPI will be sent.</p>
              )}
              <FormField label="Date of Service" htmlFor="dos">
                <CompactInput id="dos" type="date" value={check.service_date} onChange={(e) => setCheck((c) => ({ ...c, service_date: e.target.value }))} />
              </FormField>
              <Button className="gap-2" disabled={readOnly || elig.busy || (check.npi_type === 'individual' && !check.provider_id)} onClick={runCheck}>
                {elig.busy ? <RefreshCw size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
                {elig.busy ? 'Checking…' : 'Check Eligibility'}
              </Button>
              <ErrorNote error={elig.error} />
              {latest && latest.policy_id === policy.id && (
                <div className="flex flex-col gap-1.5 rounded-lg border border-hairline bg-card px-3 py-2.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant={latest.coverage_status === 'active' ? 'success' : 'error'}>{latest.coverage_status}</Badge>
                    {latest.in_network != null && <Badge variant={latest.in_network ? 'success' : 'error'}>{latest.in_network ? 'In network' : 'Out of network'}</Badge>}
                    {latest.discrepancies.length > 0 && <Badge variant="warning">{latest.discrepancies.length} discrepancy</Badge>}
                  </div>
                  {latest.ai_summary && <p className="font-body text-xs leading-relaxed text-body">{latest.ai_summary}</p>}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2 font-body text-sm">
                <button className="cursor-pointer text-slate-blue hover:underline disabled:text-muted-soft disabled:no-underline" disabled={!policy.last_eligibility_check_id}
                  onClick={() => setShowResponse(policy.last_eligibility_check_id)}>View Eligibility Response</button>
                <span className="text-hairline-strong">|</span>
                <button className="cursor-pointer text-slate-blue hover:underline" onClick={() => setShowResponse('history')}>View History</button>
              </div>
              <p className="font-body text-xs text-muted">Last Checked: {lastChecked ? `${new Date(lastChecked.checked_at).toLocaleString()} · ${lastChecked.checked_by}` : 'never'}</p>
            </div>
          </div>
        </Panel>
      </div>

      {showResponse && showResponse !== 'history' && (
        <EligibilityResponseDialog patientId={chart.id} checkId={showResponse} onClose={() => setShowResponse(null)} />
      )}
      {showResponse === 'history' && (
        <Dialog title="Eligibility check history" onClose={() => setShowResponse(null)} headerTone="light">
          {policy.eligibility_checks.length ? (
            <div className="flex flex-col divide-y divide-hairline">
              {policy.eligibility_checks.map((c) => (
                <button key={c.id} onClick={() => setShowResponse(c.id)} className="flex cursor-pointer flex-wrap items-center justify-between gap-2 py-2.5 text-left hover:bg-surface-subtle">
                  <span className="font-body text-sm text-ink">{new Date(c.checked_at).toLocaleString()} · DOS {formatDate(c.date_of_service)}</span>
                  <span className="flex items-center gap-1.5">
                    <Badge variant={c.coverage_status === 'active' ? 'success' : 'error'}>{c.coverage_status}</Badge>
                    <span className="font-body text-xs text-muted">{c.npi_type} NPI {c.npi} · {c.checked_by}</span>
                  </span>
                </button>
              ))}
            </div>
          ) : <p className="font-body text-sm text-muted">No eligibility checks yet.</p>}
        </Dialog>
      )}
      {showHistory && <PolicyHistory patientId={chart.id} policyId={policy.id} onClose={() => setShowHistory(false)} />}
      {replacing && (
        <ReplaceDialog policy={policy} onClose={() => setReplacing(false)} onDone={async (created) => {
          setReplacing(false)
          setChart(await api(`/patients/${chart.id}`))
          notify('Old policy archived; new policy created.')
          navigate(`/patients/${chart.id}/insurance/${created.id}`)
        }} />
      )}
      {confirm && <ConfirmDialog title={confirm.title} body={confirm.body} cta={confirm.cta} onConfirm={confirm.onYes} onCancel={() => setConfirm(null)} />}
      {/* money values print-friendly summary */}
      <p className="hidden font-body text-xs print:block">Co-pay {formatMoney(policy.copay_amount)} · Deductible {formatMoney(policy.deductible)} · Remaining {formatMoney(policy.remaining_deductible)}</p>
    </div>
  )
}

function PolicyHistory({ patientId, policyId, onClose }) {
  const { data, error } = useApi(`/patients/${patientId}/history?entity=policy`)
  const rows = (data || []).filter((e) => e.entity_id === policyId)
  return (
    <Dialog title="Policy history" subtitle="Every change to this policy, newest first" onClose={onClose} headerTone="light">
      <ErrorNote error={error} />
      {!data ? <Spinner /> : rows.length ? <ChangeList events={rows} /> : <p className="font-body text-sm text-muted">No changes recorded yet.</p>}
    </Dialog>
  )
}

