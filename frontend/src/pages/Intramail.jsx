import { useCallback, useEffect, useState } from 'react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ErrorNote, PageHeader, Section, Spinner, Table } from '@/components/ui/Page'
import { api, post, useAction } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { SelectField, TextField } from '@/pages/chart/shared'

/** Internal staff messages. Inbox and sent, with an optional link to a patient chart. */
export default function Intramail() {
  const [box, setBox] = useState('inbox')
  const [page, setPage] = useState(null)
  const [open, setOpen] = useState(null)
  const [staff, setStaff] = useState([])
  const [f, setF] = useState({ recipient_id: '', subject: '', body: '' })
  const [error, setError] = useState(null)
  const [sent, setSent] = useState('')
  const act = useAction()
  const people = (id) => staff.find((u) => u.id === id)?.name || `User #${id}`

  const load = useCallback(async () => {
    try {
      setPage(await api(`/intramail?box=${box}&limit=100`))
      setError(null)
    } catch (e) {
      setError(e)
    }
  }, [box])
  useEffect(() => { setOpen(null); load() }, [load])
  useEffect(() => { api('/intramail/recipients').then(setStaff).catch(() => {}) }, [])

  const show = (m) => act.run(async () => { setOpen(await api(`/intramail/${m.id}`)); await load() })
  const remove = (m) => act.run(async () => { await api(`/intramail/${m.id}`, { method: 'DELETE' }); setOpen(null); await load() })
  const send = () => act.run(async () => {
    await post('/intramail', { recipient_id: Number(f.recipient_id), subject: f.subject, body: f.body })
    setF({ recipient_id: '', subject: '', body: '' })
    setSent('Message sent.')
    setBox('sent')
    await load()
  })

  const columns = [
    { key: 'subject', label: 'Subject', render: (m) => <span className={!m.read_at && box === 'inbox' ? 'font-semibold' : ''}>{m.subject}</span> },
    { key: 'who', label: box === 'inbox' ? 'From' : 'To', render: (m) => people(box === 'inbox' ? m.sender_id : m.recipient_id) },
    { key: 'sent_at', label: 'Sent', render: (m) => formatDateTime(m.sent_at) },
    { key: 'read_at', label: '', render: (m) => (box === 'inbox' && !m.read_at ? <Badge variant="info">New</Badge> : null) },
  ]
  return (
    <div className="flex flex-col gap-5">
      <PageHeader eyebrow="Staff" title="Intramail" subtitle="Internal messages between staff accounts." />
      <div className="flex gap-2">
        {['inbox', 'sent'].map((b) => (
          <Button key={b} variant={box === b ? 'primary' : 'outline'} onClick={() => setBox(b)}>{b === 'inbox' ? 'Inbox' : 'Sent'}</Button>
        ))}
      </div>
      <ErrorNote error={error} />
      <Section title={box === 'inbox' ? 'Inbox' : 'Sent'}>
        {!page ? <Spinner /> : (
          <Table rows={page.items} columns={columns} onRowClick={show} empty={<p className="font-body text-sm text-muted">No messages.</p>} />
        )}
      </Section>
      {open && (
        <Section title={open.subject} actions={<Button variant="outline" className="h-8 px-3 text-xs" disabled={act.busy} onClick={() => remove(open)}>Delete</Button>}>
          <p className="font-body text-xs text-muted">From {people(open.sender_id)} to {people(open.recipient_id)} · {formatDateTime(open.sent_at)}</p>
          <p className="whitespace-pre-wrap font-body text-sm text-ink">{open.body}</p>
        </Section>
      )}
      <Section title="New message">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <SelectField label="To" required value={f.recipient_id} onChange={(v) => setF((p) => ({ ...p, recipient_id: v }))} options={staff.map((u) => ({ value: u.id, label: `${u.name} (${u.role})` }))} />
          <TextField label="Subject" required value={f.subject} onChange={(v) => setF((p) => ({ ...p, subject: v }))} />
        </div>
        <label className="flex flex-col gap-1 font-body text-xs font-medium text-muted">
          Message
          <textarea rows={5} value={f.body} onChange={(e) => setF((p) => ({ ...p, body: e.target.value }))} className="rounded-lg border border-hairline bg-card p-2 text-sm text-ink" />
        </label>
        <div><Button disabled={act.busy || !f.recipient_id || !f.subject.trim() || !f.body.trim()} onClick={send}>Send</Button></div>
        {sent && <p className="font-body text-xs text-success">{sent}</p>}
        <ErrorNote error={act.error} />
      </Section>
    </div>
  )
}
