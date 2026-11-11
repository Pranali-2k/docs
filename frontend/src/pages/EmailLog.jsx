import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowDownLeft, ArrowUpRight, ChevronDown, Mail } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/Card'
import { ErrorNote, PageHeader, Spinner, Tabs } from '@/components/ui/Page'
import { useApi } from '@/lib/api'
import { cn } from '@/lib/cn'
import { formatDateTime } from '@/lib/format'

export default function EmailLog() {
  const [direction, setDirection] = useState('all')
  const [open, setOpen] = useState(null)
  const { data, error, loading } = useApi(`/emails${direction !== 'all' ? `?direction=${direction}` : ''}`)

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Outlook integration"
        title="Email log"
        subtitle="Inbound scheduling emails and every reply sent by the AI assistant or staff. In outbox mode replies are stored, not sent."
      />
      <Tabs
        tabs={[
          { id: 'all', label: 'All mail' },
          { id: 'inbound', label: 'Inbound' },
          { id: 'outbound', label: 'Outbound' },
        ]}
        value={direction}
        onChange={setDirection}
      />
      <ErrorNote error={error} />
      {loading && !data ? (
        <Spinner />
      ) : !data?.length ? (
        <EmptyState icon={Mail} title="No emails yet">
          Emails appear here once the mailbox is polled or a sample is sent from the Demo lab.
        </EmptyState>
      ) : (
        <div className="overflow-hidden rounded-xl border border-hairline bg-card shadow-card">
          <div className="divide-y divide-hairline">
            {data.map((e) => {
              const inbound = e.direction === 'inbound'
              const expanded = open === e.id
              return (
                <Fragment key={e.id}>
                  <button
                    onClick={() => setOpen(expanded ? null : e.id)}
                    className="grid w-full cursor-pointer grid-cols-[28px_1fr_auto] items-center gap-3 px-5 py-3.5 text-left transition-colors hover:bg-surface-subtle/70 md:grid-cols-[28px_140px_1.2fr_1.5fr_110px_20px]"
                  >
                    <span
                      className={cn(
                        'flex size-7 items-center justify-center rounded-full',
                        inbound ? 'bg-slate-soft text-slate-blue' : 'bg-sage-soft text-sage',
                      )}
                    >
                      {inbound ? <ArrowDownLeft size={14} /> : <ArrowUpRight size={14} />}
                    </span>
                    <span className="hidden font-body text-xs text-muted tabular-nums md:block">{formatDateTime(e.received_at)}</span>
                    <span className="min-w-0 truncate font-body text-sm text-ink">
                      {inbound ? e.from_address : `→ ${e.to_address}`}
                    </span>
                    <span className="hidden min-w-0 truncate font-body text-sm text-body md:block">{e.subject}</span>
                    <span>
                      {e.send_status ? (
                        <Badge variant={e.send_status === 'failed' ? 'error' : ['sent', 'test_sent'].includes(e.send_status) ? 'success' : e.send_status === 'awaiting_approval' ? 'warning' : 'neutral'}
                          title={e.redirect_to ? `Test mode: delivered only to ${e.redirect_to}` : undefined}>
                          {e.send_status === 'test_sent' ? `test → ${e.redirect_to}` : e.send_status.replace('_', ' ')}
                        </Badge>
                      ) : (
                        <Badge variant="info">{e.source}</Badge>
                      )}
                    </span>
                    <ChevronDown size={14} className={cn('hidden text-muted transition-transform md:block', expanded && 'rotate-180')} />
                  </button>
                  {expanded && (
                    <div className="flex flex-col gap-2 bg-surface-subtle/60 px-5 py-4">
                      <p className="font-body text-sm font-medium text-ink md:hidden">{e.subject}</p>
                      <pre className="font-body text-sm leading-relaxed whitespace-pre-wrap text-body">{e.body_text}</pre>
                      {e.send_error && <p className="font-body text-xs text-error">{e.send_error}</p>}
                      {e.request_id && (
                        <Link to={`/requests/${e.request_id}`} className="link font-body text-xs">
                          Open portal request
                        </Link>
                      )}
                    </div>
                  )}
                </Fragment>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
