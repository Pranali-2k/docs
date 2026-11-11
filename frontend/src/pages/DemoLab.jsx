import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Mail, Send } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardLabel } from '@/components/ui/Card'
import { CompactInput, CompactTextArea, FieldLabel } from '@/components/ui/Field'
import { ErrorNote, PageHeader, Section, Spinner } from '@/components/ui/Page'
import { post, useAction, useApi } from '@/lib/api'

export default function DemoLab() {
  const navigate = useNavigate()
  const { data: samples, error } = useApi('/demo/samples')
  const act = useAction()
  const [sending, setSending] = useState(null)
  const [f, setF] = useState({ from_name: '', from_address: '', subject: '', body: '' })

  const send = (key) => {
    setSending(key)
    act.run(async () => navigate(`/requests/${(await post(`/demo/samples/${key}`)).request.id}`)).finally(() => setSending(null))
  }
  const sendCustom = (e) => {
    e.preventDefault()
    act.run(async () => navigate(`/requests/${(await post('/intake/email', { ...f, from_name: f.from_name || null })).request.id}`))
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Future validation & demo"
        title="Demo lab"
        subtitle="Validate the workflow with scheduling staff before rollout. Each sample exercises one PRD path; send it and compare the outcome with the expected result."
      />
      <ErrorNote error={error || act.error} />
      {!samples ? (
        <Spinner />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {samples.map((s) => (
            <Card key={s.key} hoverable className="flex flex-col gap-3 px-5 py-4.5">
              <div className="flex items-start justify-between gap-2">
                <p className="font-display text-lg leading-snug text-ink">{s.title}</p>
                <Mail size={16} className="mt-1 shrink-0 text-muted" />
              </div>
              <p className="font-body text-xs text-muted">
                {s.from_name || '(no name)'} &lt;{s.from_address}&gt;
              </p>
              <div className="rounded-lg border border-hairline/70 bg-surface-subtle/60 px-3.5 py-2.5">
                <p className="font-body text-sm font-medium text-ink">{s.subject}</p>
                <p className="mt-1 line-clamp-4 font-body text-xs leading-relaxed whitespace-pre-wrap text-body">{s.body}</p>
              </div>
              <div className="rounded-lg border border-sage-border bg-sage-soft/60 px-3.5 py-2.5">
                <CardLabel className="text-sage">Expected</CardLabel>
                <p className="mt-0.5 font-body text-xs leading-relaxed text-body">{s.expected}</p>
              </div>
              <Button className="mt-auto gap-2" disabled={act.busy} onClick={() => send(s.key)}>
                {sending === s.key ? 'Processing…' : 'Send to AI inbox'}
                <ArrowRight size={14} />
              </Button>
            </Card>
          ))}
        </div>
      )}

      <Section title="Compose a custom email">
        <form onSubmit={sendCustom} className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col">
              <FieldLabel htmlFor="c-name">Sender name</FieldLabel>
              <CompactInput id="c-name" value={f.from_name} onChange={(e) => setF({ ...f, from_name: e.target.value })} />
            </div>
            <div className="flex flex-col">
              <FieldLabel htmlFor="c-from">Sender email *</FieldLabel>
              <CompactInput id="c-from" type="email" required value={f.from_address} onChange={(e) => setF({ ...f, from_address: e.target.value })} />
            </div>
            <div className="flex flex-col sm:col-span-2">
              <FieldLabel htmlFor="c-subject">Subject</FieldLabel>
              <CompactInput id="c-subject" value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} />
            </div>
            <div className="flex flex-col sm:col-span-2">
              <FieldLabel htmlFor="c-body">Body *</FieldLabel>
              <CompactTextArea id="c-body" rows={6} required value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} />
            </div>
          </div>
          <div>
            <Button type="submit" disabled={act.busy} className="gap-2">
              <Send size={15} />
              {act.busy ? 'Processing…' : 'Send to AI inbox'}
            </Button>
          </div>
        </form>
      </Section>
    </div>
  )
}
