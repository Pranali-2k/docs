import { useCallback, useEffect, useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/Overlay'
import { ErrorNote, Section, Spinner, Table } from '@/components/ui/Page'
import { api, patch, post, useAction } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatDate } from '@/lib/format'
import { SelectField, TextField } from './shared'

const ANY = ['admin', 'scheduler']
const ADMIN = ['admin']
const SEVERITY = { info: 'neutral', warning: 'warning', critical: 'error', mild: 'neutral', moderate: 'warning', severe: 'error' }

/**
 * Every chart add-on screen is described here: where it lives in the API, the form, the table columns, which status
 * moves the server allows (mirroring app/services/chart_extras.TRANSITIONS) and who may write. The server enforces all of
 * it; this only decides what to offer.
 */
export const KINDS = {
  alerts: {
    title: 'Alerts', empty: 'No alerts on this chart.', write: ANY, deletable: true,
    fields: [
      { k: 'text', label: 'Alert', required: true },
      { k: 'severity', label: 'Severity', type: 'select', options: ['info', 'warning', 'critical'], def: 'info' },
    ],
    columns: [{ key: 'text', label: 'Alert' }, { key: 'severity', label: 'Severity', render: (r) => <Badge variant={SEVERITY[r.severity]}>{r.severity}</Badge> },
      { key: 'is_active', label: 'Status', render: (r) => (r.is_active ? 'Active' : 'Inactive') }],
    toggle: { field: 'is_active', on: true, offLabel: 'Deactivate', onLabel: 'Reactivate' },
  },
  allergies: {
    title: 'Allergies', empty: 'No allergies recorded.', write: ADMIN,
    fields: [
      { k: 'allergen', label: 'Allergen', required: true }, { k: 'reaction', label: 'Reaction' },
      { k: 'severity', label: 'Severity', type: 'select', options: ['mild', 'moderate', 'severe'] },
      { k: 'noted_on', label: 'Noted on', type: 'date' },
    ],
    columns: [{ key: 'allergen', label: 'Allergen' }, { key: 'reaction', label: 'Reaction' },
      { key: 'severity', label: 'Severity', render: (r) => (r.severity ? <Badge variant={SEVERITY[r.severity]}>{r.severity}</Badge> : '—') },
      { key: 'status', label: 'Status' }],
    moves: { active: [['inactive', 'Mark inactive']], inactive: [['active', 'Mark active']] },
  },
  notes: {
    title: 'Notes and sticky notes', empty: 'No notes yet.', write: ANY, deletable: true,
    fields: [{ k: 'kind', label: 'Type', type: 'select', options: ['sticky', 'general', 'clinical'], def: 'general' },
      { k: 'body', label: 'Note', type: 'textarea', required: true }],
    columns: [{ key: 'kind', label: 'Type', render: (r) => <Badge variant={r.kind === 'sticky' ? 'warning' : 'neutral'}>{r.kind}</Badge> },
      { key: 'body', label: 'Note', cellClassName: 'whitespace-pre-wrap' }, { key: 'created_by', label: 'By' },
      { key: 'created_at', label: 'Added', render: (r) => formatDate(r.created_at) }],
  },
  reminders: {
    title: 'Reminders', empty: 'No reminders.', write: ANY, deletable: true,
    fields: [{ k: 'message', label: 'Reminder', required: true }, { k: 'due_on', label: 'Due on', type: 'date' }],
    columns: [{ key: 'message', label: 'Reminder' }, { key: 'due_on', label: 'Due', render: (r) => formatDate(r.due_on) }, { key: 'status', label: 'Status' }],
    moves: { open: [['done', 'Done'], ['cancelled', 'Cancel']] },
  },
  flags: {
    title: 'Patient flags', empty: 'No flags.', write: ANY, deletable: true,
    fields: [{ k: 'flag_type', label: 'Flag', required: true }, { k: 'note', label: 'Note' }],
    columns: [{ key: 'flag_type', label: 'Flag' }, { key: 'note', label: 'Note' }, { key: 'is_active', label: 'Status', render: (r) => (r.is_active ? 'Active' : 'Inactive') }],
    toggle: { field: 'is_active', on: true, offLabel: 'Deactivate', onLabel: 'Reactivate' },
  },
  tasks: {
    title: 'Tasks', empty: 'No tasks for this patient.', write: ANY, deletable: true,
    listPath: (id) => `/tasks?patient_id=${id}&limit=100`, createPath: () => '/tasks', itemPath: (_, it) => `/tasks/${it}`,
    extraBody: (id) => ({ patient_id: Number(id) }), needsStaff: true,
    fields: [{ k: 'title', label: 'Task', required: true },
      { k: 'priority', label: 'Priority', type: 'select', options: ['low', 'normal', 'high'], def: 'normal' },
      { k: 'due_on', label: 'Due on', type: 'date' }, { k: 'assigned_to_id', label: 'Assign to', type: 'staff' },
      { k: 'description', label: 'Details', type: 'textarea' }],
    columns: [{ key: 'title', label: 'Task' }, { key: 'priority', label: 'Priority', render: (r) => <Badge variant={r.priority === 'high' ? 'error' : 'neutral'}>{r.priority}</Badge> },
      { key: 'due_on', label: 'Due', render: (r) => formatDate(r.due_on) }, { key: 'status', label: 'Status' }],
    moves: { open: [['in_progress', 'Start'], ['done', 'Done'], ['cancelled', 'Cancel']], in_progress: [['done', 'Done'], ['open', 'Back to open'], ['cancelled', 'Cancel']] },
  },
  medications: {
    title: 'Medications (Patient Rx)', empty: 'No medications recorded.', write: ADMIN, warnings: true,
    fields: [{ k: 'drug_name', label: 'Drug', required: true }, { k: 'strength', label: 'Strength' }, { k: 'route', label: 'Route' },
      { k: 'frequency', label: 'Frequency' }, { k: 'quantity', label: 'Quantity', type: 'number' }, { k: 'refills', label: 'Refills', type: 'number' },
      { k: 'start_on', label: 'Start', type: 'date' }, { k: 'instructions', label: 'Instructions', type: 'textarea' }],
    columns: [{ key: 'drug_name', label: 'Drug', render: (r) => `${r.drug_name}${r.strength ? ` ${r.strength}` : ''}` }, { key: 'frequency', label: 'Frequency' },
      { key: 'start_on', label: 'Start', render: (r) => formatDate(r.start_on) }, { key: 'stop_on', label: 'Stop', render: (r) => formatDate(r.stop_on) },
      { key: 'status', label: 'Status' }],
    moves: { active: [['stopped', 'Stop'], ['completed', 'Complete']], stopped: [['active', 'Restart']] },
  },
  orders: {
    title: 'Non-visit orders', empty: 'No orders.', write: ADMIN,
    fields: [{ k: 'order_type', label: 'Type', type: 'select', options: ['lab', 'imaging', 'referral', 'other'], required: true },
      { k: 'description', label: 'Order', required: true }, { k: 'due_on', label: 'Due on', type: 'date' }],
    columns: [{ key: 'order_type', label: 'Type' }, { key: 'description', label: 'Order' }, { key: 'due_on', label: 'Due', render: (r) => formatDate(r.due_on) },
      { key: 'status', label: 'Status' }, { key: 'result_note', label: 'Result' }],
    moves: { draft: [['ordered', 'Place order'], ['cancelled', 'Cancel']], ordered: [['completed', 'Complete'], ['cancelled', 'Cancel']] },
  },
  procedures: {
    title: 'Procedure logs', empty: 'No procedures logged.', write: ADMIN,
    fields: [{ k: 'cpt_code', label: 'CPT / HCPCS', required: true }, { k: 'icd10_code', label: 'ICD-10' }, { k: 'description', label: 'Description' },
      { k: 'units', label: 'Units', type: 'number', def: 1 }, { k: 'performed_on', label: 'Performed on', type: 'date', required: true }],
    columns: [{ key: 'performed_on', label: 'Date', render: (r) => formatDate(r.performed_on) }, { key: 'cpt_code', label: 'CPT' },
      { key: 'icd10_code', label: 'ICD-10' }, { key: 'description', label: 'Description' }, { key: 'units', label: 'Units' }],
  },
  consents: {
    title: 'Consents', empty: 'No consents issued.', write: ANY, needsTemplates: true,
    fields: [{ k: 'template_id', label: 'Consent form', type: 'template', required: true }],
    columns: [{ key: 'template_name', label: 'Form' }, { key: 'template_version', label: 'Version' },
      { key: 'status', label: 'Status', render: (r) => <Badge variant={r.status === 'signed' ? 'success' : r.status === 'revoked' ? 'error' : 'neutral'}>{r.status}</Badge> },
      { key: 'signed_by_name', label: 'Signed by' }, { key: 'signed_on', label: 'Signed', render: (r) => formatDate(r.signed_on) }],
    moves: { pending: [['signed', 'Record signature'], ['revoked', 'Revoke']], signed: [['revoked', 'Revoke']] },
  },
}

const blank = (cfg) => Object.fromEntries(cfg.fields.map((f) => [f.k, f.def ?? '']))

function FieldInput({ f, value, onChange, staff, templates }) {
  if (f.type === 'select') return <SelectField label={f.label} required={f.required} value={value} onChange={onChange} options={f.options} />
  if (f.type === 'staff') return <SelectField label={f.label} value={value} onChange={onChange} options={staff.map((u) => ({ value: u.id, label: `${u.name} (${u.role})` }))} placeholder="Unassigned" />
  if (f.type === 'template') return <SelectField label={f.label} required value={value} onChange={onChange} options={templates.map((t) => ({ value: t.id, label: `${t.name} (v${t.version})` }))} />
  if (f.type === 'textarea') {
    return (
      <label className="flex flex-col gap-1 font-body text-xs font-medium text-muted sm:col-span-2">
        {f.label}
        <textarea rows={3} value={value ?? ''} onChange={(e) => onChange(e.target.value)} className="rounded-lg border border-hairline bg-card p-2 text-sm text-ink" />
      </label>
    )
  }
  return <TextField label={f.label} required={f.required} type={f.type || 'text'} value={value} onChange={onChange} />
}

/** One chart add-on screen, driven by KINDS[kind]. */
export function ChartKind({ kind }) {
  const cfg = KINDS[kind]
  const { chart, notify } = useOutletContext()
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const [rows, setRows] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [staff, setStaff] = useState([])
  const [templates, setTemplates] = useState([])
  const [form, setForm] = useState(() => blank(cfg))
  const [warnings, setWarnings] = useState([])
  const [confirm, setConfirm] = useState(null)
  const act = useAction()
  const canWrite = cfg.write.includes(user?.role)
  const base = `/patients/${chart.id}/${kind}`

  const load = useCallback(async () => {
    try {
      const page = await api(cfg.listPath ? cfg.listPath(chart.id) : `${base}?limit=100`)
      setRows(page.items)
      setLoadError(null)
    } catch (e) {
      setLoadError(e)
    }
  }, [base, cfg, chart.id])

  useEffect(() => {
    setForm(blank(cfg))
    setRows(null)
    setWarnings([])
    load()
    if (cfg.needsStaff) api('/intramail/recipients').then(setStaff).catch(() => {})
    if (cfg.needsTemplates) api('/consent-templates?active=true').then(setTemplates).catch(() => {})
  }, [kind, chart.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (params.get('new')) {
      document.getElementById('chart-kind-form')?.scrollIntoView({ behavior: 'smooth' })
      setParams({}, { replace: true })
    }
  }, [params, setParams])

  const set = (k) => (v) => setForm((p) => ({ ...p, [k]: v }))
  const clean = () => {
    const body = {}
    for (const f of cfg.fields) {
      const v = form[f.k]
      if (v === '' || v === null || v === undefined) continue
      body[f.k] = f.type === 'number' || f.type === 'staff' || f.type === 'template' ? Number(v) : v
    }
    return { ...body, ...(cfg.extraBody ? cfg.extraBody(chart.id) : {}) }
  }
  const missing = cfg.fields.some((f) => f.required && !form[f.k])

  const add = () =>
    act.run(async () => {
      const created = await post(cfg.createPath ? cfg.createPath(chart.id) : base, clean())
      setWarnings(created.warnings || [])
      setForm(blank(cfg))
      await load()
      notify(`${cfg.title.split(' (')[0]} added.`)
    })
  const itemPath = (row) => (cfg.itemPath ? cfg.itemPath(chart.id, row.id) : `${base}/${row.id}`)
  const update = (row, body, msg) =>
    act.run(async () => {
      const res = await patch(itemPath(row), body)
      setWarnings(res.warnings || [])
      await load()
      if (msg) notify(msg)
    })
  const remove = (row) =>
    act.run(async () => {
      await api(itemPath(row), { method: 'DELETE' })
      setConfirm(null)
      await load()
      notify('Removed.')
    })

  const signer = (row) => {
    const name = window.prompt('Name of the person signing:', `${chart.first_name} ${chart.last_name}`)
    if (name?.trim()) update(row, { status: 'signed', signed_by_name: name.trim() }, 'Signature recorded.')
  }

  const columns = [
    ...cfg.columns.map((c) => (c.key === 'template_name'
      ? { ...c, render: (r) => r.template_name || `Form #${r.template_id}` } : c)),
    ...(canWrite ? [{
      key: '_actions', label: '', cellClassName: 'text-right whitespace-nowrap',
      render: (r) => (
        <span className="inline-flex flex-wrap justify-end gap-1.5">
          {(cfg.moves?.[r.status] || []).map(([to, label]) => (
            <Button key={to} variant="outline" className="h-8 px-3 text-xs" disabled={act.busy}
              onClick={() => (kind === 'consents' && to === 'signed' ? signer(r) : update(r, { status: to }, `${label}: done.`))}>{label}</Button>
          ))}
          {cfg.toggle && (
            <Button variant="outline" className="h-8 px-3 text-xs" disabled={act.busy}
              onClick={() => update(r, { [cfg.toggle.field]: !r[cfg.toggle.field] })}>
              {r[cfg.toggle.field] ? cfg.toggle.offLabel : cfg.toggle.onLabel}
            </Button>
          )}
          {cfg.deletable && (
            <Button variant="outline" className="h-8 px-3 text-xs" disabled={act.busy} onClick={() => setConfirm(r)}>Delete</Button>
          )}
        </span>
      ),
    }] : []),
  ]

  return (
    <div className="flex flex-col gap-4">
      {warnings.length > 0 && (
        <div role="alert" className="rounded-xl border border-error/40 bg-error/5 p-4 font-body text-sm text-ink">
          <p className="mb-1 font-semibold">Drug warnings</p>
          <ul className="list-disc pl-5">{warnings.map((w, i) => <li key={i}><Badge variant={SEVERITY[w.severity === 'major' ? 'severe' : 'moderate']}>{w.severity}</Badge> {w.drug}: {w.message}</li>)}</ul>
        </div>
      )}
      <Section title={cfg.title}>
        <ErrorNote error={loadError} />
        {!rows ? <Spinner /> : (
          <Table columns={columns} rows={rows} empty={<p className="font-body text-sm text-muted">{cfg.empty}</p>} />
        )}
      </Section>
      {cfg.warnings && <DrugWarnings patientId={chart.id} refreshKey={rows} />}
      {canWrite ? (
        <Section title={`Add to ${cfg.title.split(' (')[0].toLowerCase()}`}>
          <div id="chart-kind-form" className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {cfg.fields.map((f) => <FieldInput key={f.k} f={f} value={form[f.k]} onChange={set(f.k)} staff={staff} templates={templates} />)}
          </div>
          <div><Button disabled={act.busy || missing} onClick={add}>Add</Button></div>
          <ErrorNote error={act.error} />
        </Section>
      ) : (
        <p className="font-body text-xs text-muted">Your role can view this section. Changes need {cfg.write.join(' or ')} access.</p>
      )}
      {confirm && <ConfirmDialog title="Delete this entry?" body="This cannot be undone." cta="Delete" onConfirm={() => remove(confirm)} onCancel={() => setConfirm(null)} />}
    </div>
  )
}

function DrugWarnings({ patientId, refreshKey }) {
  const [data, setData] = useState(null)
  useEffect(() => { api(`/patients/${patientId}/drug-warnings`).then(setData).catch(() => setData(null)) }, [patientId, refreshKey])
  if (!data) return null
  return (
    <Section title="Current drug warnings">
      {data.warnings.length ? (
        <ul className="flex flex-col gap-2 font-body text-sm">
          {data.warnings.map((w, i) => (
            <li key={i}><Badge variant={w.severity === 'major' ? 'error' : 'warning'}>{w.severity}</Badge> <b>{w.drug}</b>: {w.message}</li>
          ))}
        </ul>
      ) : <p className="font-body text-sm text-muted">No warnings found among {data.active_medications} active medication(s).</p>}
      {data.rules_loaded === 0 && (
        <p className="font-body text-xs text-muted">No interaction rules are loaded, so only allergy matches are checked. An administrator can add rules under Settings.</p>
      )}
    </Section>
  )
}

export function LinkedAccounts() {
  const { chart, notify } = useOutletContext()
  const [links, setLinks] = useState(null)
  const [q, setQ] = useState('')
  const [hits, setHits] = useState([])
  const [rel, setRel] = useState('')
  const act = useAction()
  const load = useCallback(() => api(`/patients/${chart.id}/links`).then(setLinks), [chart.id])
  useEffect(() => { load() }, [load])
  const search = () => act.run(async () => setHits((await api(`/patients?q=${encodeURIComponent(q)}&limit=8`)).filter((p) => p.id !== chart.id)))
  const link = (p) => act.run(async () => { await post(`/patients/${chart.id}/links`, { linked_patient_id: p.id, relationship_label: rel || null }); setHits([]); setQ(''); await load(); notify('Accounts linked.') })
  const unlink = (l) => act.run(async () => { await api(`/patients/${chart.id}/links/${l.linked_patient_id}`, { method: 'DELETE' }); await load(); notify('Link removed.') })
  return (
    <div className="flex flex-col gap-4">
      <Section title="Linked accounts">
        {!links ? <Spinner /> : (
          <Table rows={links} columns={[{ key: 'name', label: 'Patient' }, { key: 'mrn', label: 'MRN' }, { key: 'date_of_birth', label: 'DOB', render: (r) => formatDate(r.date_of_birth) },
            { key: 'relationship_label', label: 'Relationship' },
            { key: 'a', label: '', render: (r) => <Button variant="outline" className="h-8 px-3 text-xs" disabled={act.busy} onClick={() => unlink(r)}>Unlink</Button> }]}
            empty={<p className="font-body text-sm text-muted">No linked accounts.</p>} />
        )}
      </Section>
      <Section title="Link another chart">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <TextField label="Find patient (name, MRN, phone)" value={q} onChange={setQ} />
          <TextField label="Relationship" value={rel} onChange={setRel} />
          <div className="flex items-end"><Button disabled={act.busy || q.trim().length < 2} onClick={search}>Search</Button></div>
        </div>
        {hits.length > 0 && (
          <ul className="flex flex-col divide-y divide-hairline font-body text-sm">
            {hits.map((p) => <li key={p.id} className="flex items-center justify-between py-2"><span>{p.full_name} · MRN {p.mrn} · DOB {formatDate(p.date_of_birth)}</span><Button className="h-8 px-3 text-xs" disabled={act.busy} onClick={() => link(p)}>Link</Button></li>)}
          </ul>
        )}
        <ErrorNote error={act.error} />
      </Section>
    </div>
  )
}

/** Clipboard: the at-a-glance summary of everything above. */
export function Clipboard() {
  const { chart } = useOutletContext()
  const [c, setC] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api(`/patients/${chart.id}/clipboard`).then(setC).catch(setErr) }, [chart.id])
  if (err) return <ErrorNote error={err} />
  if (!c) return <Spinner />
  const list = (items, text) => (items.length ? items.map((x) => <li key={x.id}>{text(x)}</li>) : [<li key="none" className="text-muted">None recorded</li>])
  return (
    <Section title="Patient clipboard">
      <div className="grid grid-cols-1 gap-5 font-body text-sm md:grid-cols-2">
        <div><h3 className="mb-1 font-semibold">Alerts</h3><ul className="list-disc pl-5">{list(c.alerts, (a) => `${a.text} (${a.severity})`)}</ul></div>
        <div><h3 className="mb-1 font-semibold">Allergies</h3><ul className="list-disc pl-5">{list(c.allergies, (a) => `${a.allergen}${a.reaction ? ` – ${a.reaction}` : ''}`)}</ul></div>
        <div><h3 className="mb-1 font-semibold">Flags</h3><ul className="list-disc pl-5">{list(c.flags, (f) => f.flag_type)}</ul></div>
        <div><h3 className="mb-1 font-semibold">Sticky notes</h3><ul className="list-disc pl-5">{list(c.sticky_notes, (n) => n.body)}</ul></div>
        <div><h3 className="mb-1 font-semibold">Active medications</h3><ul className="list-disc pl-5">{list(c.active_medications, (m) => `${m.drug_name} ${m.strength || ''} ${m.frequency || ''}`.trim())}</ul></div>
        <div>
          <h3 className="mb-1 font-semibold">Open work</h3>
          <p>{c.open_tasks} task(s) · {c.open_reminders} reminder(s) · {c.pending_orders} pending order(s) · {c.pending_consents} consent(s) awaiting signature · {c.linked_accounts} linked account(s)</p>
        </div>
      </div>
    </Section>
  )
}
