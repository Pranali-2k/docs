import { useCallback, useEffect, useState } from 'react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ErrorNote, PageHeader, Section, Table } from '@/components/ui/Page'
import { api, post, useAction } from '@/lib/api'
import { SelectField, TextField } from '@/pages/chart/shared'

/** Administrator-maintained reference data: drug interaction rules and consent form templates. */
export default function ClinicalSettings() {
  const [rules, setRules] = useState([])
  const [templates, setTemplates] = useState([])
  const [r, setR] = useState({ drug_a: '', drug_b: '', severity: 'moderate', message: '', source: '' })
  const [t, setT] = useState({ name: '', body: '' })
  const act = useAction()
  const load = useCallback(async () => {
    setRules(await api('/drug-rules?limit=200'))
    setTemplates(await api('/consent-templates'))
  }, [])
  useEffect(() => { load().catch(() => {}) }, [load])
  const addRule = () => act.run(async () => { await post('/drug-rules', { ...r, source: r.source || null }); setR({ drug_a: '', drug_b: '', severity: 'moderate', message: '', source: '' }); await load() })
  const addTemplate = () => act.run(async () => { await post('/consent-templates', t); setT({ name: '', body: '' }); await load() })
  const toggle = (tpl) => act.run(async () => { await api(`/consent-templates/${tpl.id}`, { method: 'PUT', body: { name: tpl.name, body: tpl.body, is_active: !tpl.is_active } }); await load() })
  return (
    <div className="flex flex-col gap-5">
      <PageHeader eyebrow="Settings" title="Clinical reference data" subtitle="Interaction rules and consent forms used on patient charts. No clinical data is built in: enter what your practice relies on." />
      <Section title="Drug interaction rules">
        <Table rows={rules} columns={[{ key: 'drug_a', label: 'Drug A' }, { key: 'drug_b', label: 'Drug B' },
          { key: 'severity', label: 'Severity', render: (x) => <Badge variant={x.severity === 'major' ? 'error' : 'warning'}>{x.severity}</Badge> },
          { key: 'message', label: 'Message' }, { key: 'source', label: 'Source' }]}
          empty={<p className="font-body text-sm text-muted">No rules yet. Without rules only allergy matches are checked.</p>} />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <TextField label="Drug A" required value={r.drug_a} onChange={(v) => setR((p) => ({ ...p, drug_a: v }))} />
          <TextField label="Drug B" required value={r.drug_b} onChange={(v) => setR((p) => ({ ...p, drug_b: v }))} />
          <SelectField label="Severity" value={r.severity} onChange={(v) => setR((p) => ({ ...p, severity: v }))} options={['minor', 'moderate', 'major']} />
          <TextField label="Message" required value={r.message} onChange={(v) => setR((p) => ({ ...p, message: v }))} />
          <TextField label="Source" value={r.source} onChange={(v) => setR((p) => ({ ...p, source: v }))} />
        </div>
        <div><Button disabled={act.busy || !r.drug_a.trim() || !r.drug_b.trim() || !r.message.trim()} onClick={addRule}>Add rule</Button></div>
      </Section>
      <Section title="Consent templates">
        <Table rows={templates} columns={[{ key: 'name', label: 'Name' }, { key: 'version', label: 'Version' },
          { key: 'is_active', label: 'Status', render: (x) => (x.is_active ? 'Active' : 'Inactive') },
          { key: 'a', label: '', render: (x) => <Button variant="outline" className="h-8 px-3 text-xs" disabled={act.busy} onClick={() => toggle(x)}>{x.is_active ? 'Deactivate' : 'Activate'}</Button> }]}
          empty={<p className="font-body text-sm text-muted">No templates yet.</p>} />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField label="Name" required value={t.name} onChange={(v) => setT((p) => ({ ...p, name: v }))} />
          <label className="flex flex-col gap-1 font-body text-xs font-medium text-muted sm:col-span-2">
            Consent text
            <textarea rows={5} value={t.body} onChange={(e) => setT((p) => ({ ...p, body: e.target.value }))} className="rounded-lg border border-hairline bg-card p-2 text-sm text-ink" />
          </label>
        </div>
        <div><Button disabled={act.busy || !t.name.trim() || !t.body.trim()} onClick={addTemplate}>Add template</Button></div>
      </Section>
      <ErrorNote error={act.error} />
    </div>
  )
}
