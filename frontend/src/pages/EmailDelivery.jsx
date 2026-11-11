import { useEffect, useState } from 'react'
import { CheckCircle2, Inbox, Search, Send, XCircle } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { CompactInput, CompactSelect, FieldLabel } from '@/components/ui/Field'
import { ErrorNote, Section, Spinner } from '@/components/ui/Page'
import { api, post, useAction, useApi } from '@/lib/api'

const METHODS = [
  { id: 'smtp', label: 'SMTP (Gmail, Microsoft 365, Yahoo, Zoho, any provider)' },
  { id: 'graph', label: 'Microsoft 365 via Graph API (OAuth, recommended for company mailboxes)' },
  { id: 'sendgrid', label: 'SendGrid API (HTTPS, API key)' },
  { id: 'mailgun', label: 'Mailgun API (HTTPS, API key)' },
]
const HINTS = {
  auth: 'Credentials were rejected. Gmail/Yahoo/Zoho need an app password; Microsoft 365 needs Authenticated SMTP enabled by an admin, or use the Graph method.',
  config: 'A required setting is missing or invalid.',
  connection: 'The server could not be reached. Check host and port.',
  timeout: 'The server did not answer in time. Try again.',
  tls: 'The secure connection failed. Use STARTTLS on 587 or SSL on 465.',
  rejected: 'The provider refused the message or sender. For APIs, the sender address must be verified with the provider.',
  rate_limited: 'The provider asked to slow down. Wait a minute and try again.',
  validation: 'Check the email addresses.',
}

function F({ label, id, children, className }) {
  return (
    <div className={`flex flex-col ${className || ''}`}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      {children}
    </div>
  )
}

function Secret({ id, label, set, value, onChange }) {
  return (
    <F label={set ? `${label} (saved, leave blank to keep)` : label} id={id}>
      <CompactInput id={id} type="password" value={value} onChange={onChange} autoComplete="new-password" placeholder={set ? '••••••••' : ''} />
    </F>
  )
}

function Result({ r, okText }) {
  if (!r) return null
  return r.ok ? (
    <div className="flex gap-2.5 rounded-lg border border-sage-border bg-sage-soft px-4 py-3 font-body text-sm text-sage">
      <CheckCircle2 size={18} className="mt-0.5 shrink-0" />
      <div><p className="font-semibold">{okText}</p><p className="text-xs">{r.saved ? 'Settings saved and active now.' : 'Settings were not saved.'}</p></div>
    </div>
  ) : (
    <div className="flex gap-2.5 rounded-lg border border-[#f7d4d4] bg-[#fdf2f2] px-4 py-3 font-body text-sm text-error">
      <XCircle size={18} className="mt-0.5 shrink-0" />
      <div className="min-w-0">
        <p className="font-semibold">Failed{r.error_kind ? ` [${r.error_kind}]` : ''}: {r.error}</p>
        {r.server_reply && <p className="break-words font-mono text-xs">Server said: {r.server_reply}</p>}
        <p className="mt-1 text-xs text-body">{HINTS[r.error_kind] || ''} Nothing was saved.</p>
      </div>
    </div>
  )
}

/** Admin-only: choose how the portal sends and receives email, test it for real, save on success. */
export function EmailDelivery() {
  const { data: current, error, setData } = useApi('/system/email-settings')
  const [f, setF] = useState(null)
  const [inb, setInb] = useState(null)
  const [profile, setProfile] = useState(null)
  const [result, setResult] = useState(null)
  const [inResult, setInResult] = useState(null)
  const act = useAction()
  const inAct = useAction()
  const detectAct = useAction()

  useEffect(() => {
    if (!current) return
    const mailbox = current.smtp_username || (current.scheduling_mailbox?.endsWith('.example') ? '' : current.scheduling_mailbox) || 'pranali@ashunya.com'
    setF({
      method: ['smtp', 'graph', 'sendgrid', 'mailgun'].includes(current.email_outbound_mode) ? current.email_outbound_mode : 'smtp',
      smtp_host: current.smtp_host || 'smtp.office365.com', smtp_port: current.smtp_port || 587,
      smtp_security: current.smtp_security || 'starttls', smtp_username: mailbox, smtp_password: '',
      smtp_from: current.smtp_from && !current.smtp_from.endsWith('.example') ? current.smtp_from : mailbox,
      smtp_from_name: current.smtp_from_name || 'Docs Health Scheduling',
      graph_tenant_id: current.graph_tenant_id || '', graph_client_id: current.graph_client_id || '', graph_client_secret: '',
      scheduling_mailbox: current.scheduling_mailbox?.endsWith('.example') ? mailbox : current.scheduling_mailbox,
      sendgrid_api_key: '', mailgun_api_key: '', mailgun_domain: current.mailgun_domain || '', mailgun_region: current.mailgun_region || 'us',
      to: mailbox, save_on_success: true, enable_outbound: true,
    })
    setInb({
      method: current.email_inbound_mode && current.email_inbound_mode !== 'none' ? current.email_inbound_mode : 'imap',
      imap_host: current.imap_host || '', imap_port: current.imap_port || 993, imap_username: current.imap_username || mailbox,
      imap_password: '', imap_folder: current.imap_folder || 'INBOX', save_on_success: true,
    })
  }, [current])

  if (error) return <Section title="Email delivery"><ErrorNote error={error} /></Section>
  if (!f || !inb) return <Section title="Email delivery"><Spinner className="py-4" /></Section>

  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const setI = (k) => (e) => setInb({ ...inb, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })

  const detect = () => detectAct.run(async () => {
    const p = await api(`/system/email-providers/detect?email=${encodeURIComponent(f.smtp_username || f.smtp_from)}`)
    setProfile(p)
    setF((prev) => ({
      ...prev,
      method: p.recommended_outbound === 'graph' ? prev.method : 'smtp',
      smtp_host: p.smtp_host || prev.smtp_host, smtp_port: p.smtp_port || prev.smtp_port, smtp_security: p.smtp_security || prev.smtp_security,
    }))
    setInb((prev) => ({ ...prev, method: p.recommended_inbound === 'graph' ? 'graph' : 'imap', imap_host: p.imap_host || prev.imap_host, imap_port: p.imap_port || 993 }))
  })

  const send = () => act.run(async () => {
    setResult(null)
    const payload = { ...f, smtp_port: Number(f.smtp_port) }
    for (const k of ['smtp_password', 'graph_client_secret', 'sendgrid_api_key', 'mailgun_api_key']) if (!payload[k]) payload[k] = null
    const r = await post('/system/email-settings/test', payload)
    setResult(r)
    setData(r.settings)
  })

  const testInbound = () => inAct.run(async () => {
    setInResult(null)
    const r = await post('/system/email-settings/test-inbound', { ...inb, imap_port: Number(inb.imap_port), imap_password: inb.imap_password || null })
    setInResult(r)
    setData(r.settings)
  })

  const mode = current.email_outbound_mode
  return (
    <Section
      title={<span className="flex items-center gap-2"><Send size={14} /> Email delivery</span>}
      actions={<Badge variant={mode !== 'outbox' ? 'success' : 'warning'}>{mode !== 'outbox' ? `Sending via ${mode}` : 'Not sending (outbox)'}</Badge>}
      className="lg:col-span-2"
    >
      <p className="font-body text-xs text-muted">
        All methods are encrypted in transit (TLS). A real test email is sent first; settings are saved only if it's accepted. Secrets are never shown again.
      </p>

      {/* ------------------------------------------------ sending */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <F label="Delivery method" id="em-method" className="sm:col-span-2">
          <CompactSelect id="em-method" value={f.method} onChange={set('method')}>
            {METHODS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </CompactSelect>
        </F>
        <F label="Send test email to" id="em-to"><CompactInput id="em-to" type="email" value={f.to} onChange={set('to')} /></F>
        <F label="Sender name" id="em-name"><CompactInput id="em-name" value={f.smtp_from_name} onChange={set('smtp_from_name')} /></F>
      </div>

      {f.method === 'smtp' && (
        <>
          <div className="flex flex-wrap items-end gap-2">
            <F label="Mailbox (login)" id="em-user" className="min-w-[260px] flex-1">
              <CompactInput id="em-user" type="email" value={f.smtp_username} onChange={set('smtp_username')} autoComplete="off" />
            </F>
            <Button variant="outline" className="h-10 gap-2 px-4 text-sm" disabled={detectAct.busy} onClick={detect}>
              <Search size={14} /> {detectAct.busy ? 'Detecting…' : 'Detect settings'}
            </Button>
          </div>
          {profile && (
            <div className="rounded-lg border border-slate-border bg-slate-soft px-3.5 py-2.5 font-body text-xs text-slate-blue">
              <p className="font-semibold">{profile.name}{profile.mx ? ` · MX ${profile.mx}` : ''}</p>
              <ul className="mt-1 list-disc pl-4">{profile.notes.map((n) => <li key={n}>{n}</li>)}</ul>
              {profile.recommended_outbound === 'graph' && <p className="mt-1">Tip: choose “Microsoft 365 via Graph API” above once IT provides an app registration.</p>}
            </div>
          )}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <F label="SMTP host" id="em-host"><CompactInput id="em-host" value={f.smtp_host} onChange={set('smtp_host')} /></F>
            <F label="Port" id="em-port"><CompactInput id="em-port" type="number" value={f.smtp_port} onChange={set('smtp_port')} /></F>
            <F label="Security" id="em-sec">
              <CompactSelect id="em-sec" value={f.smtp_security} onChange={set('smtp_security')}>
                <option value="starttls">STARTTLS (587)</option>
                <option value="ssl">SSL/TLS (465)</option>
              </CompactSelect>
            </F>
            <Secret id="em-pw" label="Password / app password" set={current.smtp_password_set} value={f.smtp_password} onChange={set('smtp_password')} />
            <F label="Send from" id="em-from"><CompactInput id="em-from" type="email" value={f.smtp_from} onChange={set('smtp_from')} /></F>
          </div>
        </>
      )}

      {f.method === 'graph' && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <F label="Mailbox to send from" id="em-mbx"><CompactInput id="em-mbx" type="email" value={f.scheduling_mailbox} onChange={set('scheduling_mailbox')} /></F>
          <F label="Tenant ID" id="em-tenant"><CompactInput id="em-tenant" value={f.graph_tenant_id} onChange={set('graph_tenant_id')} /></F>
          <F label="Client (application) ID" id="em-client"><CompactInput id="em-client" value={f.graph_client_id} onChange={set('graph_client_id')} /></F>
          <Secret id="em-secret" label="Client secret" set={current.graph_client_secret_set} value={f.graph_client_secret} onChange={set('graph_client_secret')} />
        </div>
      )}

      {f.method === 'sendgrid' && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Secret id="em-sg" label="SendGrid API key" set={current.sendgrid_api_key_set} value={f.sendgrid_api_key} onChange={set('sendgrid_api_key')} />
          <F label="Verified sender address" id="em-sgfrom"><CompactInput id="em-sgfrom" type="email" value={f.smtp_from} onChange={set('smtp_from')} /></F>
        </div>
      )}

      {f.method === 'mailgun' && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Secret id="em-mg" label="Mailgun API key" set={current.mailgun_api_key_set} value={f.mailgun_api_key} onChange={set('mailgun_api_key')} />
          <F label="Sending domain" id="em-mgdom"><CompactInput id="em-mgdom" value={f.mailgun_domain} onChange={set('mailgun_domain')} placeholder="mg.yourdomain.com" /></F>
          <F label="Region" id="em-mgreg">
            <CompactSelect id="em-mgreg" value={f.mailgun_region} onChange={set('mailgun_region')}><option value="us">US</option><option value="eu">EU</option></CompactSelect>
          </F>
          <F label="Send from" id="em-mgfrom"><CompactInput id="em-mgfrom" type="email" value={f.smtp_from} onChange={set('smtp_from')} /></F>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-5 font-body text-sm text-body">
        <label className="flex cursor-pointer items-center gap-2">
          <input type="checkbox" className="accent-primary" checked={f.save_on_success} onChange={set('save_on_success')} /> Save if the test email is accepted
        </label>
        <label className="flex cursor-pointer items-center gap-2">
          <input type="checkbox" className="accent-primary" checked={f.enable_outbound} onChange={set('enable_outbound')} disabled={!f.save_on_success} />
          Use it for all app emails (Send only / Send &amp; resolve, auto-replies, password resets)
        </label>
      </div>
      <div>
        <Button className="gap-2" disabled={act.busy || !f.to} onClick={send}><Send size={15} /> {act.busy ? 'Sending…' : 'Send test email'}</Button>
      </div>
      <ErrorNote error={act.error} />
      <Result r={result} okText={result?.ok ? `Accepted for ${result.accepted.join(', ')} via ${result.method} (${result.elapsed_ms} ms). Check that inbox and Junk for “Docs Health test email”.` : ''} />

      {/* ------------------------------------------------ receiving */}
      <div className="mt-2 flex flex-col gap-3.5 border-t border-hairline pt-4">
        <p className="flex items-center gap-2 font-body text-sm font-semibold text-ink"><Inbox size={15} /> Receiving patient emails
          <Badge variant={current.email_inbound_mode !== 'none' ? 'success' : 'neutral'}>{current.email_inbound_mode}</Badge>
        </p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <F label="Method" id="in-method">
            <CompactSelect id="in-method" value={inb.method} onChange={setI('method')}>
              <option value="imap">IMAP (Gmail, Yahoo, Zoho, other providers)</option>
              <option value="graph">Microsoft 365 via Graph (uses the Graph settings above)</option>
            </CompactSelect>
          </F>
          {inb.method === 'imap' && (
            <>
              <F label="IMAP host" id="in-host"><CompactInput id="in-host" value={inb.imap_host} onChange={setI('imap_host')} placeholder="imap.gmail.com" /></F>
              <F label="Port" id="in-port"><CompactInput id="in-port" type="number" value={inb.imap_port} onChange={setI('imap_port')} /></F>
              <F label="Folder" id="in-folder"><CompactInput id="in-folder" value={inb.imap_folder} onChange={setI('imap_folder')} /></F>
              <F label="Mailbox (login)" id="in-user"><CompactInput id="in-user" type="email" value={inb.imap_username} onChange={setI('imap_username')} /></F>
              <Secret id="in-pw" label="Password / app password" set={current.imap_password_set} value={inb.imap_password} onChange={setI('imap_password')} />
            </>
          )}
        </div>
        <div>
          <Button variant="outline" className="gap-2" disabled={inAct.busy} onClick={testInbound}><Inbox size={15} /> {inAct.busy ? 'Connecting…' : 'Test mailbox & enable receiving'}</Button>
        </div>
        <ErrorNote error={inAct.error} />
        <Result r={inResult} okText={inResult?.ok ? `Mailbox opened (${inResult.mode}); ${inResult.unread ?? 0} unread message(s). New mail will become portal requests.` : ''} />
      </div>
    </Section>
  )
}
