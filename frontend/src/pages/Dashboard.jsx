import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowUpRight,
  CalendarDays,
  Clock,
  FlaskConical,
  Inbox,
  Mail,
  Sparkles,
  Timer,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Card, CardLabel, EmptyState, StatCard } from '@/components/ui/Card'
import { BarList, Sparkline } from '@/components/ui/DataViz'
import { ErrorNote, PageHeader, Spinner } from '@/components/ui/Page'
import { useApi } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatDateTime, formatPercent } from '@/lib/format'
import { REQUEST_STATUS, intentLabel, reasonLabel, reasonVariant, uniqueReasons, requestStatus } from '@/lib/status'

function MailboxStats({ boxes }) {
  return (
    <Card className="flex flex-col gap-3.5 px-6 py-5.5 lg:col-span-2">
      <div className="flex items-center justify-between">
        <CardLabel>Connected mailboxes</CardLabel>
        <span className="font-body text-xs text-muted">Counts from Microsoft 365, refreshed on every mailbox check</span>
      </div>
      <div className="flex flex-col divide-y divide-hairline">
        {boxes.map((m) => {
          return (
            <div key={m.id} className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="flex items-center gap-2 font-body text-sm font-medium text-ink">
                  <Mail size={14} className="text-muted" /> {m.address}
                  <span className="font-normal text-muted">· {m.folder}</span>
                  {!m.enabled && <Badge>paused</Badge>}
                </p>
                <span className="font-body text-xs text-muted">{m.counts_at ? `as of ${formatDateTime(m.counts_at)}` : 'not checked yet'}</span>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                {[
                  ['Total in folder', m.total, 'Every message in the mailbox folder (read + unread), as Microsoft 365 reports it.'],
                  ['Imported as requests', m.imported, 'Messages turned into portal requests (each one once).'],
                  ['Internal skipped', m.skipped_internal, 'Messages from skip-listed senders (e.g. @docshealth.com staff): not patient requests, never imported.'],
                  ['Auto-replies skipped', m.skipped_other, 'Out-of-office, bounces, bulk mail and mail sent by the clinic’s own mailboxes.'],
                  ['Waiting', m.pending, 'In the folder but not handled yet: new mail is picked up on the next check (every ~2 min).'],
                  ['Unread', m.unread, 'Unread in Outlook. Stays high until the app is allowed to mark mail as read (Mail.ReadWrite).'],
                ].map(([label, v, hint]) => (
                  <div key={label} title={hint}>
                    <p className="font-body text-[11px] uppercase tracking-wide text-muted">{label}</p>
                    <p className="font-display text-xl text-ink tabular-nums">{v ?? '—'}</p>
                  </div>
                ))}
              </div>
              {m.total != null && (
                <p className="font-body text-xs text-muted">
                  {m.total} in folder = {m.imported} imported + {m.skipped_internal} internal + {m.skipped_other} auto-replies + {m.pending} waiting
                  {m.imported + m.skipped_internal + m.skipped_other + m.pending !== m.total ? ' (some handled mail has since been deleted or moved in Outlook)' : ''}
                </p>
              )}
              {m.total > 0 && (
                <div className="flex h-1.5 overflow-hidden rounded-full bg-surface-subtle" aria-hidden="true">
                  <div style={{ width: `${(m.imported / m.total) * 100}%`, background: 'var(--color-slate-blue)' }} />
                  <div style={{ width: `${(m.skipped_internal / m.total) * 100}%`, background: 'var(--color-warm-amber)' }} />
                  <div style={{ width: `${(m.skipped_other / m.total) * 100}%`, background: 'var(--color-sage)' }} />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

function bars(record, label = (k) => k, color) {
  const entries = Object.entries(record || {}).sort((a, b) => b[1] - a[1])
  const max = Math.max(1, ...entries.map(([, v]) => v))
  return entries.map(([k, v]) => ({
    label: label(k),
    value: v,
    pct: `${(v / max) * 100}%`,
    color: typeof color === 'function' ? color(k) : color,
  }))
}

/** Daily received counts -> Sparkline polyline in its 560x140 viewBox. */
function sparkPoints(daily) {
  const max = Math.max(1, ...daily.map((d) => d.received))
  const step = daily.length > 1 ? 560 / (daily.length - 1) : 0
  return daily.map((d, i) => `${Math.round(i * step)},${Math.round(130 - (d.received / max) * 110)}`).join(' ')
}

export default function Dashboard() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const { data: d, error } = useApi('/dashboard/summary')
  const { data: queue } = useApi('/requests?status=needs_review,in_review&limit=6')

  if (error) return <ErrorNote error={error} />
  if (!d) return <Spinner />

  const daily = d.daily.slice(-14)
  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })

  return (
    <div className="flex flex-col gap-7">
      <PageHeader
        eyebrow="Scheduling workspace"
        title={`${greeting()}, ${user?.full_name?.split(' ')[0] ?? ''}`}
        subtitle={`${today} · last ${d.window_days} days of scheduling email activity`}
        actions={
          <>
            <Button variant="outline" onClick={() => navigate('/demo')} className="gap-2">
              <FlaskConical size={15} className="text-muted" />
              Demo lab
            </Button>
            <Button onClick={() => navigate('/requests')} className="gap-2 shadow-subtle">
              <Inbox size={15} />
              Start review ({d.needs_review})
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Requests received" value={d.total_requests} icon={Inbox} />
        <StatCard label="Auto-response rate" value={formatPercent(d.auto_response_rate)} icon={Sparkles} />
        <StatCard label="Needs review" value={d.needs_review} icon={AlertTriangle} />
        <StatCard label="Avg. first reply" value={d.avg_minutes_to_first_response != null ? `${d.avg_minutes_to_first_response}m` : '—'} icon={Clock} />
        <StatCard label="Avg. to resolve" value={d.avg_minutes_to_resolve != null ? `${d.avg_minutes_to_resolve}m` : '—'} icon={Timer} />
        <StatCard label="Appointments today" value={d.appointments_today} icon={CalendarDays} />
      </div>

      <div className="grid grid-cols-1 items-start gap-4.5 lg:grid-cols-2">
        {d.mailboxes?.length > 0 && <MailboxStats boxes={d.mailboxes} />}
        <Card className="flex flex-col gap-3.5 px-6 py-5.5">
          <div className="flex items-center justify-between">
            <CardLabel>Priority review queue</CardLabel>
            <button
              onClick={() => navigate('/requests')}
              className="flex cursor-pointer items-center gap-1 font-body text-xs font-medium text-muted hover:text-ink"
            >
              Open inbox <ArrowUpRight size={13} />
            </button>
          </div>
          {!queue?.length ? (
            <p className="py-6 text-center font-body text-sm text-muted">Nothing waiting for review.</p>
          ) : (
            <div className="flex flex-col divide-y divide-hairline">
              {queue.map((r) => (
                <button
                  key={r.id}
                  onClick={() => navigate(`/requests/${r.id}`)}
                  className="group flex cursor-pointer items-start justify-between gap-3 py-3 text-left"
                >
                  <div className="min-w-0">
                    <p className="truncate font-body text-sm font-medium text-ink">
                      {r.patient?.full_name || r.requested_name || r.from_address}
                      <span className="ml-2 font-normal text-muted">{intentLabel(r.intent)}</span>
                    </p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {r.priority === 'high' && <Badge variant="error">Urgent</Badge>}
                      {uniqueReasons(r.review_reasons).slice(0, 2).map((x) => (
                        <Badge key={x.code} variant={reasonVariant(x.code)}>
                          {reasonLabel(x.code)}
                        </Badge>
                      ))}
                    </div>
                  </div>
                  <span className="shrink-0 font-body text-xs text-muted tabular-nums">{formatDateTime(r.received_at)}</span>
                </button>
              ))}
            </div>
          )}
        </Card>

        <Card className="flex flex-col gap-3.5 px-6 py-5.5">
          <CardLabel>Manual review triggers</CardLabel>
          {Object.keys(d.review_reasons).length ? (
            <BarList
              items={bars(d.review_reasons, reasonLabel, (k) =>
                reasonVariant(k) === 'error' ? 'var(--semantic-error)' : 'var(--color-warm-amber)',
              )}
            />
          ) : (
            <EmptyState icon={Sparkles} title="No manual reviews">
              Every request so far was handled automatically.
            </EmptyState>
          )}
        </Card>

        <Card className="flex flex-col gap-3.5 px-6 py-5.5">
          <div className="flex items-center justify-between">
            <CardLabel>Daily request volume</CardLabel>
            <span className="font-body text-xs text-muted">
              {d.auto_responded} auto · {d.total_requests - d.auto_responded} review
            </span>
          </div>
          {daily.length > 1 ? (
            <Sparkline points={sparkPoints(daily)} labels={daily.map((x) => x.date.slice(5))} />
          ) : daily.length === 1 ? (
            <BarList
              items={[
                { label: `Auto-responded · ${daily[0].date}`, value: daily[0].auto, pct: `${(daily[0].auto / daily[0].received) * 100}%` },
                { label: `Manual review · ${daily[0].date}`, value: daily[0].review, pct: `${(daily[0].review / daily[0].received) * 100}%`, color: 'var(--color-warm-amber)' },
              ]}
            />
          ) : (
            <EmptyState icon={Inbox} title="No requests yet">
              Send a sample email from the Demo lab to see the workflow run.
            </EmptyState>
          )}
        </Card>

        <Card className="flex flex-col gap-3.5 px-6 py-5.5">
          <CardLabel>Requests by status & intent</CardLabel>
          <BarList
            items={bars(d.by_status, (k) => REQUEST_STATUS[k]?.label ?? k, (k) =>
              requestStatus(k).variant === 'success'
                ? 'var(--color-sage)'
                : requestStatus(k).variant === 'warning'
                  ? 'var(--color-warm-amber)'
                  : 'var(--color-slate-blue)',
            )}
          />
          <div className="border-t border-hairline pt-3.5">
            <BarList items={bars(d.by_intent, intentLabel, 'var(--color-slate-blue)')} />
          </div>
        </Card>
      </div>
    </div>
  )
}
