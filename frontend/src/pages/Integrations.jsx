import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, Copy, Inbox, Link2, Plug, RefreshCw, Send, Trash2, Unplug, XCircle } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { CompactInput, CompactSelect, FieldLabel } from '@/components/ui/Field'
import { ConfirmDialog, Toast } from '@/components/ui/Overlay'
import { ErrorNote, PageHeader, Section, Spinner } from '@/components/ui/Page'
import { api, patch, post, useAction, useApi } from '@/lib/api'
import { formatDateTime } from '@/lib/format'

const STATUS = {
  not_configured: ['neutral', 'Not configured'], configured: ['warning', 'Configured: not connected'],
  connected: ['success', 'Connected'], reauth_required: ['error', 'Sign-in expired: reconnect'], error: ['error', 'Error'],
}

function F({ label, id, children, className, hint }) {
  return (
    <div className={`flex flex-col ${className || ''}`}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      {children}
      {hint && <span className="mt-1 font-body text-[11px] text-muted">{hint}</span>}
    </div>
  )
}

function Toggle({ checked, onChange, label, disabled }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 font-body text-xs text-body">
      <input type="checkbox" className="accent-primary" checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /> {label}
    </label>
  )
}

function CopyField({ value }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard blocked: the value is selectable */
    }
  }
  return (
    <div className="flex gap-2">
      <CompactInput readOnly value={value} className="flex-1 font-mono text-xs" onFocus={(e) => e.target.select()} />
      <Button variant="outline" className="h-10 gap-2 px-3 text-xs" onClick={copy}><Copy size={13} /> {copied ? 'Copied' : 'Copy'}</Button>
    </div>
  )
}

function MicrosoftCard({ view, setView, notify }) {
  const conn = view.connection
  const [f, setF] = useState({ auth_mode: 'delegated', tenant_id: '', client_id: '', client_secret: '', poll_interval_seconds: 120 })
  const [test, setTest] = useState(null)
  const [confirm, setConfirm] = useState(false)
  const act = useAction()
  useEffect(() => {
    if (conn) setF((p) => ({ ...p, auth_mode: conn.auth_mode, tenant_id: conn.tenant_id || '', client_id: conn.client_id || '', poll_interval_seconds: conn.poll_interval_seconds || 120 }))
  }, [conn?.auth_mode, conn?.tenant_id, conn?.client_id, conn?.poll_interval_seconds])
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const [variant, label] = STATUS[conn?.status || 'not_configured']

  const save = () => act.run(async () => {
    setView(await api('/integrations/microsoft', { method: 'PUT', body: { ...f, client_secret: f.client_secret || null, poll_interval_seconds: Number(f.poll_interval_seconds) } }))
    setF((p) => ({ ...p, client_secret: '' }))
    notify('Microsoft settings saved.')
  })
  const connect = () => act.run(async () => {
    const r = await post('/integrations/microsoft/connect')
    window.location.assign(r.authorize_url)  // Microsoft sign-in / admin consent, then back to this page
  })
  const runTest = () => act.run(async () => {
    const r = await post('/integrations/microsoft/test')
    setTest(r)
    setView(r.integration)
  })
  const disconnect = () => act.run(async () => {
    setConfirm(false)
    setView(await post('/integrations/microsoft/disconnect'))
    notify('Disconnected. Mailboxes stop syncing until you connect again.')
  })

  return (
    <Section title={<span className="flex items-center gap-2"><Plug size={14} /> Microsoft Entra ID / Microsoft 365</span>} actions={<Badge variant={variant}>{label}</Badge>}>
      <div className="rounded-lg border border-slate-border bg-slate-soft px-4 py-3 font-body text-xs text-slate-blue">
        <p className="mb-1.5 font-semibold">1. Register the app in Microsoft Entra admin center → App registrations → New registration</p>
        <p className="mb-1">Platform: <b>Web</b>. Add this <b>Redirect URI</b>:</p>
        <CopyField value={view.redirect_uri} />
        <p className="mt-2">API permissions (Microsoft Graph):{' '}
          {f.auth_mode === 'delegated'
            ? <><b>Delegated</b> {view.delegated_scopes.filter((s) => !['openid', 'profile', 'email', 'offline_access'].includes(s)).join(', ')} (+ openid, profile, email, offline_access)</>
            : <><b>Application</b> {view.application_permissions.join(', ')}; then restrict the app to the scheduling mailboxes with an Exchange application access policy</>}
          . Then create a client secret under Certificates &amp; secrets.</p>
      </div>

      <p className="font-body text-xs font-semibold text-muted">2. Enter the app details</p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <F label="Sign-in mode" id="ms-mode" className="sm:col-span-2">
          <CompactSelect id="ms-mode" value={f.auth_mode} onChange={set('auth_mode')}>
            <option value="delegated">Delegated: sign in as the mailbox user (Connect with Microsoft)</option>
            <option value="application">Application: app-only permissions (admin consent)</option>
          </CompactSelect>
        </F>
        <F label="Directory (tenant) ID" id="ms-tenant"><CompactInput id="ms-tenant" value={f.tenant_id} onChange={set('tenant_id')} placeholder="xxxxxxxx-xxxx-…" /></F>
        <F label="Application (client) ID" id="ms-client"><CompactInput id="ms-client" value={f.client_id} onChange={set('client_id')} placeholder="xxxxxxxx-xxxx-…" /></F>
        <F label={conn?.client_secret_set ? `Client secret value (saved ••••${conn.client_secret_hint || ''}; blank = keep)` : 'Client secret value'} id="ms-secret" className="sm:col-span-2">
          <CompactInput id="ms-secret" type="password" value={f.client_secret} onChange={set('client_secret')} autoComplete="new-password" />
        </F>
        <F label="Check mailboxes every (seconds)" id="ms-poll"><CompactInput id="ms-poll" type="number" min="30" max="3600" value={f.poll_interval_seconds} onChange={set('poll_interval_seconds')} /></F>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={act.busy || !f.tenant_id || !f.client_id || (!f.client_secret && !conn?.client_secret_set)} onClick={save}>Save</Button>
        <Button className="gap-2" disabled={act.busy || !conn?.client_secret_set} onClick={connect}>
          <Link2 size={15} /> {f.auth_mode === 'application' ? 'Grant admin consent' : conn?.status === 'connected' ? 'Reconnect with Microsoft' : 'Connect with Microsoft'}
        </Button>
        {conn?.status === 'connected' && <Button variant="outline" className="gap-2" disabled={act.busy} onClick={runTest}><RefreshCw size={14} /> Test connection</Button>}
        {conn && ['connected', 'reauth_required'].includes(conn.status) && <Button variant="text" onClick={() => setConfirm(true)}><Unplug size={14} /> Disconnect</Button>}
      </div>
      <ErrorNote error={act.error} />
      {conn?.status === 'connected' && (
        <p className="font-body text-xs text-muted">Connected as <b className="text-ink">{conn.connected_name} ({conn.connected_account})</b> by {conn.connected_by} · {formatDateTime(conn.connected_at)}</p>
      )}
      {conn?.last_error && conn.status !== 'connected' && <p className="font-body text-xs text-error">{conn.last_error}</p>}
      {test && (test.ok
        ? <p className="flex items-center gap-1.5 font-body text-sm text-sage"><CheckCircle2 size={15} /> Microsoft Graph reachable as {test.account}.</p>
        : <p className="flex items-center gap-1.5 font-body text-sm text-error"><XCircle size={15} /> {test.error}</p>)}
      {confirm && <ConfirmDialog title="Disconnect Microsoft 365?" body="Mailboxes stop being read and in-thread replies stop until you connect again. Saved app details are kept." cta="Disconnect" onConfirm={disconnect} onCancel={() => setConfirm(false)} />}
    </Section>
  )
}

function SkipList({ m, update, busy }) {
  const [draft, setDraft] = useState('')
  const list = m.skip_sender_domains || []
  const add = () => {
    const items = draft.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean)
    if (items.length) update({ skip_sender_domains: [...list, ...items] })
    setDraft('')
  }
  return (
    <div className="flex flex-col gap-1.5 border-t border-hairline pt-2.5">
      <p className="font-body text-xs font-medium text-ink">
        Skip mail from these senders <span className="font-normal text-muted">(internal staff, partners: not patient requests; subdomains included)</span>
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        {list.length === 0 && <span className="font-body text-xs text-muted">No sender filter: every sender becomes a request.</span>}
        {list.map((d) => (
          <span key={d} className="inline-flex items-center gap-1 rounded-full border border-hairline bg-surface-subtle px-2.5 py-0.5 font-body text-xs text-ink">
            {d.includes('@') ? d : `@${d}`}
            <button aria-label={`Stop skipping ${d}`} disabled={busy} className="cursor-pointer text-muted hover:text-error"
              onClick={() => update({ skip_sender_domains: list.filter((x) => x !== d) })}><XCircle size={12} /></button>
          </span>
        ))}
        <CompactInput aria-label="Add domain or address to skip" className="h-7 w-48 text-xs" placeholder="domain.com or name@domain.com"
          value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
        <Button variant="outline" className="h-7 px-2.5 text-xs" disabled={busy || !draft.trim()} onClick={add}>Add</Button>
      </div>
    </div>
  )
}

function MailboxRow({ m, setView, notify }) {
  const act = useAction()
  const [result, setResult] = useState(null)
  const update = (changes) => act.run(async () => setView(await patch(`/integrations/mailboxes/${m.id}`, changes)))
  const test = () => act.run(async () => {
    const r = await post(`/integrations/mailboxes/${m.id}/test`)
    setResult(r.ok ? `Opened “${r.folder}”: ${r.unread} unread of ${r.total}.` : r.error)
    setView(r.integration)
  })
  const poll = () => act.run(async () => {
    const r = await post(`/integrations/mailboxes/${m.id}/poll`)
    setResult(r.ok ? `${r.created.length} new request(s)${r.created.length ? `: ${r.created.join(', ')}` : ''}; ${r.skipped} skipped (${r.skipped_internal || 0} internal, rest auto-replies/own mail).` : r.error)
    setView(r.integration)
    if (r.ok && r.created.length) notify(`${r.created.length} new request(s) from ${m.address}.`)
  })
  const remove = () => act.run(async () => setView(await api(`/integrations/mailboxes/${m.id}`, { method: 'DELETE' })))
  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-hairline px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-body text-sm font-semibold text-ink">{m.address} {m.is_default_sender && <Badge variant="info">default sender</Badge>} {!m.enabled && <Badge>paused</Badge>}</p>
          <p className="font-body text-xs text-muted">Folder “{m.folder}” · last checked {m.last_polled_at ? formatDateTime(m.last_polled_at) : 'never'}{m.last_poll_count != null ? ` (${m.last_poll_count} new)` : ''}</p>
          {m.total_count != null && (
            <p className="font-body text-xs text-muted">
              <b className="text-ink">{m.unread_count}</b> unread · <b className="text-ink">{m.read_count}</b> read · {m.total_count} total
              {m.skipped_internal_total ? ` · ${m.skipped_internal_total} internal skipped` : ''}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" className="h-8 px-3 text-xs" disabled={act.busy} onClick={test}>Test</Button>
          <Button variant="outline" className="h-8 gap-1.5 px-3 text-xs" disabled={act.busy} onClick={poll}><Inbox size={13} /> Check now</Button>
          <button aria-label={`Remove ${m.address}`} className="cursor-pointer px-1 text-muted hover:text-error" onClick={remove}><Trash2 size={15} /></button>
        </div>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1.5">
        <Toggle checked={m.enabled} onChange={(v) => update({ enabled: v })} label="Enabled" />
        <Toggle checked={m.process_inbound} onChange={(v) => update({ process_inbound: v })} label="Process incoming mail with AI" />
        <Toggle checked={m.reply_from_same_mailbox} onChange={(v) => update({ reply_from_same_mailbox: v })} label="Reply from this mailbox in the same thread" />
        <Toggle checked={m.mark_as_read} onChange={(v) => update({ mark_as_read: v })} label="Mark processed mail as read" />
        <Toggle checked={m.is_default_sender} onChange={(v) => update({ is_default_sender: v })} label="Default sender" />
      </div>
      <SkipList m={m} update={update} busy={act.busy} />
      {(result || m.last_error) && <p className={`font-body text-xs ${m.last_error && !result ? 'text-error' : 'text-body'}`}>{result || m.last_error}</p>}
      <ErrorNote error={act.error} />
    </div>
  )
}

function TestModeBox({ view, setView, notify }) {
  const act = useAction()
  const tm = view.test_mode
  const [to, setTo] = useState(tm?.recipient || '')
  const change = (enabled) => act.run(async () => {
    setView(await api('/integrations/outbound/test-mode', { method: 'PUT', body: { enabled, recipient: enabled ? to : tm?.recipient } }))
    notify(enabled ? `Test mode ON: replies wait for approval and go only to ${to}.` : 'Test mode OFF: normal outbound rules apply.')
  })
  return (
    <div className={`flex flex-col gap-2.5 rounded-lg border px-4 py-3 ${tm ? 'border-warm-amber-border bg-warm-amber-soft' : 'border-hairline'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-body text-sm font-semibold text-ink">Test mode</p>
        <Badge variant={tm ? 'warning' : 'neutral'}>{tm ? `On: replies go only to ${tm.recipient}` : 'Off'}</Badge>
      </div>
      <p className="font-body text-xs text-muted">
        For testing with real mail: every reply (AI or staff, including Microsoft 365 in-thread replies) waits on the request as
        <b> awaiting approval</b>. <b>Approve &amp; send</b> delivers it as a new email to the test address only, never to the
        patient or attorney. Works even while outbound email is off.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1">
          <span className="font-body text-xs font-medium text-ink">Send test replies to</span>
          <CompactInput type="email" value={to} onChange={(e) => setTo(e.target.value)} placeholder="you@company.com" className="w-64" disabled={!!tm} />
        </label>
        {tm
          ? <Button variant="outline" disabled={act.busy} onClick={() => change(false)}>Turn test mode off</Button>
          : <Button disabled={act.busy || !to.includes('@')} onClick={() => change(true)}>Turn test mode on</Button>}
      </div>
      <ErrorNote error={act.error} />
    </div>
  )
}

function OutboundCard({ view, setView, notify }) {
  const act = useAction()
  const [confirm, setConfirm] = useState(null)
  const on = view.outbound?.enabled
  const change = (enabled) => act.run(async () => {
    setConfirm(null)
    setView(await api('/integrations/outbound', { method: 'PUT', body: { enabled, reason: enabled ? 'enabled by admin' : 'disabled by admin' } }))
    notify(enabled ? 'Outbound email is ON: replies will be sent.' : 'Outbound email is OFF: replies are held in the portal.')
  })
  return (
    <Section title={<span className="flex items-center gap-2"><Send size={14} /> Outbound email</span>}
      actions={<Badge variant={on ? 'success' : 'warning'}>{on ? 'On: replies are sent' : 'Off: replies are held'}</Badge>}>
      <p className="font-body text-xs text-muted">
        Master switch for every email to patients: AI auto-replies, staff Send only / Send &amp; resolve, and in-thread replies from the
        mailboxes below. While it's off, replies are saved on the request as <b>held</b> and nothing is sent; turn it on and use
        “Retry sending” on a request to send a held reply. Reading incoming mailboxes is not affected.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant={on ? 'outline' : 'primary'} disabled={act.busy} onClick={() => setConfirm(on ? 'off' : 'on')}>
          {on ? 'Turn outbound email off' : 'Turn outbound email on'}
        </Button>
        {view.outbound?.updated_by && <span className="font-body text-xs text-muted">Last changed by {view.outbound.updated_by} · {formatDateTime(view.outbound.updated_at)}</span>}
      </div>
      <ErrorNote error={act.error} />
      <TestModeBox view={view} setView={setView} notify={notify} />
      {confirm && (
        <ConfirmDialog
          title={confirm === 'on' ? 'Turn outbound email on?' : 'Turn outbound email off?'}
          body={confirm === 'on' ? 'Replies will be emailed to patients from now on (held replies stay held until you retry them).' : 'No email will be sent to patients until you turn it back on.'}
          cta={confirm === 'on' ? 'Turn on' : 'Turn off'}
          onConfirm={() => change(confirm === 'on')}
          onCancel={() => setConfirm(null)}
        />
      )}
    </Section>
  )
}

function MailboxesCard({ view, setView, notify }) {
  const [f, setF] = useState({ address: '', folder: 'Inbox' })
  const act = useAction()
  const ready = view.connection && view.connection.status === 'connected'
  const add = () => act.run(async () => {
    setView(await post('/integrations/mailboxes', { ...f, is_default_sender: view.mailboxes.length === 0 }))
    setF({ address: '', folder: 'Inbox' })
    notify('Mailbox added.')
  })
  return (
    <Section title={<span className="flex items-center gap-2"><Inbox size={14} /> Incoming mailboxes to process</span>}>
      <p className="font-body text-xs text-muted">
        New mail in these mailboxes becomes portal requests. Replies (auto and staff, Send only / Send &amp; resolve) go back
        from the <b>same mailbox</b> in the <b>same mail thread</b>. Mail from the clinic's own mailboxes, auto-replies and bounces is ignored.
      </p>
      {!ready && <p className="rounded-lg border border-warm-amber-border bg-warm-amber-soft px-3 py-2 font-body text-xs text-warm-amber">Mailboxes can be added now; they are read once Microsoft is connected above.</p>}
      {view.mailboxes.map((m) => <MailboxRow key={m.id} m={m} setView={setView} notify={notify} />)}
      <div className="flex flex-wrap items-end gap-2 border-t border-hairline pt-3.5">
        <F label="Mailbox address (user or shared mailbox)" id="mb-addr" className="min-w-[260px] flex-1">
          <CompactInput id="mb-addr" type="email" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} placeholder="scheduling@yourclinic.com" />
        </F>
        <F label="Folder" id="mb-folder" hint="Inbox, or a folder name such as Scheduling">
          <CompactInput id="mb-folder" value={f.folder} onChange={(e) => setF({ ...f, folder: e.target.value })} />
        </F>
        <Button disabled={act.busy || !f.address} onClick={add}>Add mailbox</Button>
      </div>
      <ErrorNote error={act.error} />
    </Section>
  )
}

export default function Integrations() {
  const { data: view, setData: setView, error } = useApi('/integrations/microsoft')
  const [params, setParams] = useSearchParams()
  const [toast, setToast] = useState('')
  const notify = (m) => {
    setToast(m)
    setTimeout(() => setToast(''), 3000)
  }
  const ms = params.get('ms')
  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Settings" title="Integrations" subtitle="Connect Microsoft 365 with Entra ID to read scheduling mailboxes and reply to patients in the same mail thread." />
      {ms === 'connected' && <div className="rounded-lg border border-sage-border bg-sage-soft px-4 py-3 font-body text-sm text-sage">Microsoft 365 connected. Add the mailboxes to process below. <button className="link ml-2" onClick={() => setParams({})}>Dismiss</button></div>}
      {ms === 'error' && <div className="rounded-lg border border-[#f7d4d4] bg-[#fdf2f2] px-4 py-3 font-body text-sm text-error">Connection failed: {params.get('reason')} <button className="link ml-2" onClick={() => setParams({})}>Dismiss</button></div>}
      <ErrorNote error={error} />
      {!view ? <Spinner /> : (
        <>
          <OutboundCard view={view} setView={setView} notify={notify} />
          <MicrosoftCard view={view} setView={setView} notify={notify} />
          <MailboxesCard view={view} setView={setView} notify={notify} />
        </>
      )}
      <Toast message={toast} />
    </div>
  )
}
