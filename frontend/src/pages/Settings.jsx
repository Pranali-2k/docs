import { useState } from 'react'
import { Bot, Mail, RefreshCw, Stethoscope } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Detail, ErrorNote, PageHeader, Section, Spinner } from '@/components/ui/Page'
import { post, useAction, useApi } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatPercent } from '@/lib/format'
import { EmailDelivery } from './EmailDelivery'

export default function Settings() {
  const { data: s, error } = useApi('/system/info')
  const { data: providers } = useApi('/providers')
  const act = useAction()
  const [poll, setPoll] = useState(null)
  const { user } = useAuth()

  if (error) return <ErrorNote error={error} />
  if (!s) return <Spinner />

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Configuration"
        title="Settings"
        subtitle="Configuration from backend/.env. Admins can set up and test email delivery below."
      />
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        {user?.role === 'admin' && <EmailDelivery />}
        <Section title={<span className="flex items-center gap-2"><Bot size={14} /> AI engine</span>}>
          <div className="grid grid-cols-1 gap-x-5 gap-y-3.5 sm:grid-cols-2">
            <Detail label="Provider"><Badge variant="info">{s.llm.provider}</Badge></Detail>
            <Detail label="Model">{s.llm.model}</Detail>
            <Detail label="Mode" className="sm:col-span-2">
              {s.llm.offline
                ? 'Rules engine: no approved AI model is set up for patient emails, so no patient data leaves the server'
                : `Patient emails go to ${s.llm.model} (approved for patient data); the rules engine is the last fallback`}
            </Detail>
            {s.llm.chain?.length > 0 && (
              <div className="flex flex-col gap-1.5 sm:col-span-2">
                <span className="font-body text-xs font-semibold uppercase tracking-wide text-muted">Fallback order</span>
                <ol className="flex flex-col divide-y divide-hairline rounded-lg border border-hairline">
                  {s.llm.chain.map((c, i) => (
                    <li key={c.provider + c.model} className="flex flex-wrap items-center gap-2 px-3 py-2 font-body text-sm">
                      <span className="w-5 text-muted tabular-nums">{i + 1}.</span>
                      <span className="text-ink">{c.provider}</span>
                      <span className="text-muted">{c.model}</span>
                      <span className="ml-auto flex flex-wrap gap-1">
                        <Badge variant={c.configured ? 'success' : 'neutral'}>{c.configured ? 'key set' : 'no key'}</Badge>
                        <Badge variant={c.phi_allowed ? 'info' : 'warning'}>{c.phi_allowed ? 'patient data OK' : 'test data only'}</Badge>
                        {c.cooling_down && <Badge variant="error">paused after error</Badge>}
                      </span>
                    </li>
                  ))}
                  <li className="flex items-center gap-2 px-3 py-2 font-body text-sm">
                    <span className="w-5 text-muted tabular-nums">{s.llm.chain.length + 1}.</span>
                    <span className="text-ink">rules engine</span><span className="text-muted">always available</span>
                  </li>
                </ol>
              </div>
            )}
            <Detail label="Supported providers" className="sm:col-span-2">{s.llm_options.join(', ')}</Detail>
            <Detail label="AI confidence threshold">{formatPercent(s.ai_confidence_threshold)}</Detail>
            <Detail label="Match confidence threshold">{formatPercent(s.match_confidence_threshold)}</Detail>
            <Detail label="Auto-book appointments">{s.auto_book_appointments ? 'Yes' : 'No (offer slots only)'}</Detail>
            <Detail label="Auto-send replies">{s.auto_send_responses ? 'Yes' : 'No (draft only)'}</Detail>
          </div>
        </Section>

        <Section title={<span className="flex items-center gap-2"><Mail size={14} /> Outlook / email integration</span>}>
          <div className="grid grid-cols-1 gap-x-5 gap-y-3.5 sm:grid-cols-2">
            <Detail label="Scheduling mailbox" className="sm:col-span-2">{s.scheduling_mailbox}</Detail>
            <Detail label="Inbound">{s.email_inbound_mode}</Detail>
            <Detail label="Outbound">{s.email_outbound_mode === 'outbox' ? 'Outbox (stored, not sent)' : s.email_outbound_mode}</Detail>
            <Detail label="Eligibility adapter">{s.eligibility_adapter}</Detail>
            <Detail label="Environment">{s.environment}</Detail>
            <Detail label="Clinic" className="sm:col-span-2">{s.clinic_name} · {s.clinic_timezone}</Detail>
          </div>
          <div className="flex flex-wrap items-center gap-3 border-t border-hairline pt-3.5">
            <Button
              variant="outline"
              className="gap-2"
              disabled={act.busy || s.email_inbound_mode === 'none'}
              onClick={() => act.run(async () => setPoll(await post('/intake/poll')))}
            >
              <RefreshCw size={14} />
              Poll mailbox now
            </Button>
            {s.email_inbound_mode === 'none' && <span className="font-body text-xs text-muted">Set EMAIL_INBOUND_MODE=graph or imap to enable.</span>}
            {poll && <span className="font-body text-xs text-muted">Processed {poll.processed.length} new email(s).</span>}
          </div>
          <ErrorNote error={act.error} />
        </Section>

        <Section title={<span className="flex items-center gap-2"><Stethoscope size={14} /> Providers & network participation</span>} className="lg:col-span-2">
          <div className="flex flex-col divide-y divide-hairline">
            {providers?.map((p) => (
              <div key={p.id} className="grid grid-cols-1 gap-1 py-3 sm:grid-cols-[220px_160px_120px_1fr] sm:gap-4">
                <span className="font-body text-sm font-medium text-ink">{p.full_name}</span>
                <span className="font-body text-sm text-body">{p.specialty}</span>
                <span className="font-body text-xs text-muted tabular-nums">NPI {p.npi}</span>
                <div className="flex flex-wrap gap-1">
                  {p.in_network_payers.map((n) => (
                    <Badge key={n} variant="success">{n}</Badge>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Section>
      </div>
    </div>
  )
}
