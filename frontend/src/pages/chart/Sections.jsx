import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { FileImage, ScanLine, Upload } from 'lucide-react'
import { DocumentScanner } from '@/components/DocumentScanner'
import { FileDropZone } from '@/components/FileDropZone'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/Card'
import { CompactSelect } from '@/components/ui/Field'
import { ConfirmDialog } from '@/components/ui/Overlay'
import { ErrorNote, Section, Table } from '@/components/ui/Page'
import { api, post, tokenStore, useAction } from '@/lib/api'
import { formatDate, formatDateTime, formatSlot } from '@/lib/format'
import { appointmentVariant, intentLabel, requestStatus } from '@/lib/status'
import { ChangeList, SaveBar, SelectField, TextField, US_STATES } from './shared'

/* ------------------------------------------------------------------ Guarantor */

const G_KEYS = ['relationship_to_patient', 'first_name', 'last_name', 'date_of_birth', 'phone', 'email', 'address_line1', 'city', 'state', 'postal_code']

export function Guarantor() {
  const { chart, setChart, notify } = useOutletContext()
  const original = useMemo(() => ({ same_as_patient: true, ...chart.guarantor }), [chart])
  const [f, setF] = useState(original)
  const act = useAction()
  useEffect(() => setF(original), [original])
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }))
  const dirty = JSON.stringify(f) !== JSON.stringify(original)
  const save = () =>
    act.run(async () => {
      const body = { same_as_patient: f.same_as_patient }
      if (!f.same_as_patient) G_KEYS.forEach((k) => (body[k] = f[k] || null))
      setChart(await api(`/patients/${chart.id}/guarantor`, { method: 'PUT', body }))
      notify('Guarantor saved.')
    })
  return (
    <div className="flex flex-col gap-4">
      <SaveBar dirty={dirty} busy={act.busy} error={act.error} onSave={save} onCancel={() => setF(original)} />
      <Section title="Guarantor (financially responsible party)">
        <label className="flex cursor-pointer items-center gap-2 font-body text-sm text-ink">
          <input type="checkbox" className="accent-primary" checked={!!f.same_as_patient} onChange={(e) => set('same_as_patient')(e.target.checked)} />
          Patient is their own guarantor
        </label>
        {!f.same_as_patient && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <SelectField label="Relationship" value={f.relationship_to_patient} onChange={set('relationship_to_patient')}
              options={['parent', 'spouse', 'legal guardian', 'child', 'other']} />
            <TextField label="First Name" required value={f.first_name} onChange={set('first_name')} />
            <TextField label="Last Name" required value={f.last_name} onChange={set('last_name')} />
            <TextField label="Date of Birth" type="date" value={f.date_of_birth} onChange={set('date_of_birth')} />
            <TextField label="Phone" value={f.phone} onChange={set('phone')} />
            <TextField label="Email" type="email" value={f.email} onChange={set('email')} />
            <TextField label="Address" value={f.address_line1} onChange={set('address_line1')} className="xl:col-span-2" />
            <TextField label="City" value={f.city} onChange={set('city')} />
            <SelectField label="State" value={f.state} onChange={set('state')} options={US_STATES} />
            <TextField label="ZIP" value={f.postal_code} onChange={set('postal_code')} />
          </div>
        )}
      </Section>
    </div>
  )
}

/* ------------------------------------------------------------------ Patient identity (documents) */

const DOC_KINDS = [
  ['photo_id', 'Photo ID'], ['insurance_card_front', 'Insurance card (front)'], ['insurance_card_back', 'Insurance card (back)'],
  ['referral', 'Referral'], ['other', 'Other'],
]

export function Identity() {
  const { chart, setChart, notify } = useOutletContext()
  const [kind, setKind] = useState('photo_id')
  const [files, setFiles] = useState([])
  const [rejected, setRejected] = useState([])
  const [scanning, setScanning] = useState(false)
  const act = useAction()
  const post1 = async (f) => {
    const form = new FormData()
    form.append('kind', kind)
    form.append('file', f)
    await api(`/patients/${chart.id}/documents`, { method: 'POST', form })
  }
  const send = async (f, message) => {
    await post1(f)
    setChart(await api(`/patients/${chart.id}`))
    notify(message)
  }
  // upload one by one; files that worked leave the list, a failure stops and keeps the rest for a retry
  const upload = () =>
    act.run(async () => {
      let left = [...files]
      try {
        while (left.length) {
          await post1(left[0])
          left = left.slice(1)
        }
      } finally {
        const uploaded = files.length - left.length
        setFiles(left)
        if (uploaded) {
          setChart(await api(`/patients/${chart.id}`))
          notify(`${uploaded} document${uploaded === 1 ? '' : 's'} uploaded.`)
        }
      }
    })
  // errors propagate to the scanner dialog, which stays open so nothing scanned is lost
  const saveScan = async (f) => {
    await send(f, 'Scanned document saved to the chart.')
    setScanning(false)
  }
  const view = (d) =>
    act.run(async () => {
      const res = await fetch(`/api/patients/${chart.id}/documents/${d.id}`, { headers: { Authorization: `Bearer ${tokenStore.get()}` } })
      if (!res.ok) throw new Error(res.status === 410 ? 'The stored file is missing. Upload it again.' : 'Could not open the document.')
      window.open(URL.createObjectURL(await res.blob()), '_blank', 'noopener')
    })
  return (
    <Section title="Patient identity: photo ID, insurance cards & attachments">
      {chart.documents.length ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {chart.documents.map((d) => (
            <div key={d.id} className="flex items-center justify-between gap-3 rounded-lg border border-hairline px-3.5 py-3">
              <div className="flex min-w-0 items-center gap-2.5">
                <FileImage size={18} className="shrink-0 text-muted" />
                <div className="min-w-0">
                  <p className="truncate font-body text-sm font-medium text-ink">{d.filename}</p>
                  <p className="font-body text-xs text-muted">{d.kind.replace(/_/g, ' ')} · {Math.max(1, Math.round(d.size_bytes / 1024))} KB · {formatDateTime(d.uploaded_at)}</p>
                </div>
              </div>
              <Button variant="text" onClick={() => view(d)}>View</Button>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState icon={FileImage} title="No identity documents">Scan or upload the patient's photo ID and both sides of the insurance card.</EmptyState>
      )}
      <div className="flex flex-col gap-3 border-t border-hairline pt-3.5">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="font-body text-xs font-medium text-ink">Document type</span>
            <CompactSelect value={kind} onChange={(e) => setKind(e.target.value)} className="min-w-52">
              {DOC_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </CompactSelect>
          </label>
          <Button className="gap-2 sm:ml-auto" disabled={act.busy} onClick={() => setScanning(true)}><ScanLine size={14} /> Scan document</Button>
        </div>
        <FileDropZone files={files} onChange={setFiles} onReject={setRejected} disabled={act.busy} />
        {rejected.length > 0 && <p role="alert" className="font-body text-xs text-error">{rejected.join(' ')}</p>}
        {files.length > 0 && (
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button variant="text" disabled={act.busy} onClick={() => { setFiles([]); setRejected([]) }}>Clear</Button>
            <Button variant="outline" className="gap-2" disabled={act.busy} onClick={upload}>
              <Upload size={14} /> {act.busy ? 'Uploading…' : `Upload ${files.length} as ${DOC_KINDS.find(([k]) => k === kind)?.[1]}`}
            </Button>
          </div>
        )}
      </div>
      <ErrorNote error={act.error} />
      {scanning && (
        <DocumentScanner kind={kind} kindLabel={DOC_KINDS.find(([k]) => k === kind)?.[1] || kind}
          onClose={() => setScanning(false)} onSave={saveScan} />
      )}
    </Section>
  )
}

/* ------------------------------------------------------------------ Appointment history */

export function Appointments() {
  const { chart, setChart, notify } = useOutletContext()
  const navigate = useNavigate()
  const [confirm, setConfirm] = useState(null)
  const act = useAction()
  const cancel = (a) =>
    setConfirm({
      title: 'Cancel this appointment?',
      body: `${formatSlot(a.start_at)} with ${a.provider_name}. The slot is released; the patient is not emailed automatically.`,
      onYes: () => act.run(async () => {
        setConfirm(null)
        await post(`/appointments/${a.id}/cancel`)
        setChart(await api(`/patients/${chart.id}`))
        notify('Appointment cancelled.')
      }),
    })
  return (
    <Section title="Appointment history" actions={<Button className="h-8 px-4 text-xs" onClick={() => navigate(`/schedule?patient=${chart.id}`)}>New appointment</Button>}>
      <ErrorNote error={act.error} />
      <Table
        rows={chart.appointments}
        columns={[
          { key: 'when', label: 'Date / time', render: (a) => formatSlot(a.start_at), cellClassName: 'whitespace-nowrap' },
          { key: 'provider_name', label: 'Provider' },
          { key: 'visit_reason', label: 'Reason', cellClassName: 'text-muted' },
          { key: 'status', label: 'Status', render: (a) => <Badge variant={appointmentVariant(a.status)}>{a.status}</Badge> },
          { key: 'src', label: 'Source', render: (a) => (a.source_request_id ? <Link to={`/requests/${a.source_request_id}`}>Email request</Link> : a.booked_by) , cellClassName: 'text-xs' },
          { key: 'x', label: '', render: (a) => a.status === 'booked' && <Button variant="text" onClick={() => cancel(a)}>Cancel</Button> },
        ]}
        empty={<EmptyState title="No appointments">Book one from the Schedule.</EmptyState>}
      />
      {confirm && <ConfirmDialog title={confirm.title} body={confirm.body} cta="Cancel appointment" onConfirm={confirm.onYes} onCancel={() => setConfirm(null)} />}
    </Section>
  )
}

/* ------------------------------------------------------------------ Release of PHI */

const EMPTY_RELEASE = { recipient_name: '', relationship_to_patient: '', phone: '', may_discuss_appointments: true,
  may_discuss_billing: false, may_discuss_clinical: false, signed_on: '', expires_on: '' }

export function PhiReleases() {
  const { chart, setChart, notify } = useOutletContext()
  const [f, setF] = useState(EMPTY_RELEASE)
  const act = useAction()
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }))
  const add = () =>
    act.run(async () => {
      const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v === '' ? null : v]))
      setChart(await post(`/patients/${chart.id}/phi-releases`, body))
      setF(EMPTY_RELEASE)
      notify('Release of PHI recorded.')
    })
  const revoke = (r) => act.run(async () => {
    setChart(await post(`/patients/${chart.id}/phi-releases/${r.id}/revoke`))
    notify('Release revoked.')
  })
  const today = new Date().toISOString().slice(0, 10)
  return (
    <div className="flex flex-col gap-4">
      <Section title="Authorized contacts (release of PHI)">
        {chart.phi_releases.length ? (
          <div className="flex flex-col divide-y divide-hairline">
            {chart.phi_releases.map((r) => {
              const expired = r.expires_on && r.expires_on < today
              return (
                <div key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div>
                    <p className="font-body text-sm font-medium text-ink">{r.recipient_name} <span className="font-normal text-muted">· {r.relationship_to_patient || '—'} · {r.phone || 'no phone'}</span></p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {r.may_discuss_appointments && <Badge variant="info">Appointments</Badge>}
                      {r.may_discuss_billing && <Badge variant="info">Billing</Badge>}
                      {r.may_discuss_clinical && <Badge variant="info">Clinical</Badge>}
                      <span className="font-body text-xs text-muted">Signed {formatDate(r.signed_on)} · expires {formatDate(r.expires_on)}</span>
                    </div>
                  </div>
                  {r.revoked_at ? <Badge>Revoked</Badge> : expired ? <Badge variant="warning">Expired</Badge> : (
                    <Button variant="outline" className="h-8 px-3 text-xs" disabled={act.busy} onClick={() => revoke(r)}>Revoke</Button>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <p className="font-body text-sm text-muted">No one is authorized to receive this patient's information.</p>
        )}
      </Section>
      <Section title="Add authorized contact">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <TextField label="Name" required value={f.recipient_name} onChange={set('recipient_name')} />
          <TextField label="Relationship" value={f.relationship_to_patient} onChange={set('relationship_to_patient')} />
          <TextField label="Phone" value={f.phone} onChange={set('phone')} />
          <div />
          <TextField label="Signed On" type="date" value={f.signed_on} onChange={set('signed_on')} />
          <TextField label="Expires On" type="date" value={f.expires_on} onChange={set('expires_on')} />
        </div>
        <div className="flex flex-wrap gap-5 font-body text-sm">
          {[['may_discuss_appointments', 'Appointments'], ['may_discuss_billing', 'Billing'], ['may_discuss_clinical', 'Clinical information']].map(([k, l]) => (
            <label key={k} className="flex cursor-pointer items-center gap-2">
              <input type="checkbox" className="accent-primary" checked={f[k]} onChange={(e) => set(k)(e.target.checked)} /> {l}
            </label>
          ))}
        </div>
        <div><Button disabled={act.busy || !f.recipient_name.trim()} onClick={add}>Add release</Button></div>
        <ErrorNote error={act.error} />
      </Section>
    </div>
  )
}

/* ------------------------------------------------------------------ Requests & history */

export function ChartRequests() {
  const { chart } = useOutletContext()
  const navigate = useNavigate()
  return (
    <Section title="Portal requests from this patient">
      <Table
        rows={chart.requests}
        onRowClick={(r) => navigate(`/requests/${r.id}`)}
        columns={[
          { key: 'reference', label: 'Reference', cellClassName: 'font-medium whitespace-nowrap' },
          { key: 'received_at', label: 'Received', render: (r) => formatDateTime(r.received_at), cellClassName: 'text-xs text-muted whitespace-nowrap' },
          { key: 'intent', label: 'Intent', render: (r) => intentLabel(r.intent) },
          { key: 'subject', label: 'Subject', cellClassName: 'text-muted' },
          { key: 'status', label: 'Status', render: (r) => { const s = requestStatus(r.status); return <Badge variant={s.variant}>{s.label}</Badge> } },
        ]}
        empty={<p className="font-body text-sm text-muted">No requests from this patient.</p>}
      />
    </Section>
  )
}

export function ChartHistory() {
  const { chart } = useOutletContext()
  return (
    <Section title="Chart history (amendments, eligibility checks, bookings)">
      <ChangeList events={chart.history} />
    </Section>
  )
}
