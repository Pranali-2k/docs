import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Archive, Inbox, RefreshCw, Send, X } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/Card'
import { CompactTextArea } from '@/components/ui/Field'
import { Modal, Toast } from '@/components/ui/Overlay'
import { Confidence, ErrorNote, PageHeader, SearchBox, Spinner, Table, Tabs } from '@/components/ui/Page'
import { post, useAction, useApi } from '@/lib/api'
import { cn } from '@/lib/cn'
import { formatDateTime, initials } from '@/lib/format'
import { intentLabel, reasonLabel, reasonVariant, uniqueReasons, requestStatus } from '@/lib/status'

const TABS = [
  { id: 'review', label: 'Needs review', status: 'needs_review,in_review' },
  { id: 'auto', label: 'Auto-responded', status: 'auto_responded' },
  { id: 'resolved', label: 'Resolved', status: 'resolved,closed' },
  { id: 'all', label: 'All requests' },
]

const OPEN = new Set(['received', 'processing', 'needs_review', 'in_review', 'auto_responded'])

function summary(res) {
  const verb = { close: 'Closed', resolve: 'Resolved', reprocess: 'Re-ran AI on' }[res.action]
  const parts = [`${verb} ${res.done.length}`]
  if (res.skipped.length) parts.push(`${res.skipped.length} skipped`)
  if (res.failed.length) parts.push(`${res.failed.length} failed`)
  return parts.join(' · ')
}

/** Selection toolbar for bulk actions: re-run the AI, or close with a note. */
function BulkBar({ ids, onDone, onClear }) {
  const act = useAction()
  const [closing, setClosing] = useState(false)
  const [note, setNote] = useState('No patient action needed (internal / not a scheduling request).')
  const run = (action, extra = {}) =>
    act.run(async () => {
      const res = await post('/requests/bulk', { ids, action, ...extra })
      setClosing(false)
      onDone(res)
    })
  return (
    <div className="sticky top-2 z-20 flex flex-wrap items-center gap-2 rounded-xl border border-hairline bg-card px-4 py-2.5 shadow-elevated">
      <span className="font-body text-sm font-semibold text-ink">{ids.length} selected</span>
      <Button variant="outline" className="h-8 gap-1.5 px-3 text-xs" disabled={act.busy} onClick={() => run('reprocess')}>
        <RefreshCw size={13} className={act.busy ? 'animate-spin' : ''} /> Re-run AI
      </Button>
      <Button variant="outline" className="h-8 gap-1.5 px-3 text-xs" disabled={act.busy} onClick={() => setClosing(true)}>
        <Archive size={13} /> Close…
      </Button>
      <button className="ml-auto flex cursor-pointer items-center gap-1 font-body text-xs text-muted hover:text-ink" onClick={onClear}>
        <X size={13} /> Clear selection
      </button>
      <div className="basis-full"><ErrorNote error={act.error} /></div>
      {closing && (
        <Modal title={`Close ${ids.length} request${ids.length === 1 ? '' : 's'}?`} onClose={() => setClosing(false)}
          footer={<>
            <Button variant="outline" onClick={() => setClosing(false)}>Cancel</Button>
            <Button disabled={act.busy || !note.trim()} onClick={() => run('close', { note: note.trim() })}>Close requests</Button>
          </>}>
          <p className="font-body text-sm text-body">Closed requests leave the review queue; no email is sent. The note is saved on each request and in the audit trail. Already-closed or resolved requests are skipped.</p>
          <label className="flex flex-col gap-1">
            <span className="font-body text-xs font-medium text-ink">Reason</span>
            <CompactTextArea rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
        </Modal>
      )}
    </div>
  )
}

/** Mobile layout: one stacked card per request, in the UI kit's mobile-list style. */
function RequestCard({ r, onOpen }) {
  const s = requestStatus(r.status)
  const name = r.patient?.full_name || r.requested_name || 'Unknown sender'
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => e.key === 'Enter' && onOpen()}
      className={cn(
        'flex cursor-pointer flex-col gap-3 rounded-xl border border-hairline bg-card p-4.5 shadow-subtle transition-all active:scale-[0.99]',
        r.priority === 'high' && 'shadow-[inset_3px_0_0_var(--semantic-error)]',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex size-7.5 shrink-0 items-center justify-center rounded-full bg-surface-strong font-body text-xs font-semibold text-ink">
            {initials(r.patient?.full_name || r.requested_name || r.from_address)}
          </div>
          <div className="min-w-0">
            <p className="truncate font-body text-sm font-medium text-ink">{name}</p>
            <p className="font-body text-xs text-muted">{intentLabel(r.intent)}</p>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Badge variant={s.variant}>{s.label}</Badge>
          {r.priority === 'high' && <Badge variant="error">Urgent</Badge>}
        </div>
      </div>

      <p className="truncate font-body text-sm text-body">{r.subject || '(no subject)'}</p>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <span className="font-body text-[10px] font-semibold uppercase tracking-[0.8px] text-muted">AI</span>
          <Confidence value={r.ai_confidence} />
        </div>
        <div className="flex flex-col gap-1">
          <span className="font-body text-[10px] font-semibold uppercase tracking-[0.8px] text-muted">Match</span>
          <Confidence value={r.match_confidence} />
        </div>
      </div>

      {r.review_reasons.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {uniqueReasons(r.review_reasons).map((x) => (
            <Badge key={x.code} variant={reasonVariant(x.code)}>
              {reasonLabel(x.code)}
            </Badge>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between border-t border-hairline pt-2.5 font-body text-xs text-muted">
        <span className="tabular-nums">
          {r.reference} · {formatDateTime(r.received_at)}
        </span>
        <span className="font-medium text-ink">Review &rarr;</span>
      </div>
    </div>
  )
}

export default function Requests() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const reason = params.get('reason') || ''
  const tab = params.get('tab') || (reason ? 'all' : 'review')
  const [q, setQ] = useState(params.get('q') || '')

  const qs = new URLSearchParams()
  const status = TABS.find((t) => t.id === tab)?.status
  if (status) qs.set('status', status)
  if (params.get('q')) qs.set('q', params.get('q'))
  if (reason) qs.set('reason', reason)
  const { data, error, loading, reload } = useApi(`/requests?${qs}`)
  const { data: all, reload: reloadAll } = useApi('/requests?limit=500')
  const [selected, setSelected] = useState(() => new Set())
  const [toast, setToast] = useState(null)
  useEffect(() => setSelected(new Set()), [tab, reason, params])
  const visibleIds = (data || []).map((r) => r.id)
  const allChecked = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id))
  const toggle = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const toggleAll = () => setSelected(allChecked ? new Set() : new Set(visibleIds))
  const afterBulk = (res) => {
    setSelected(new Set())
    setToast(summary(res))
    setTimeout(() => setToast(null), 4000)
    reload()
    reloadAll()
  }

  const counts = {
    review: all?.filter((r) => ['needs_review', 'in_review'].includes(r.status)).length,
    auto: all?.filter((r) => r.status === 'auto_responded').length,
    resolved: all?.filter((r) => ['resolved', 'closed'].includes(r.status)).length,
    all: all?.length,
  }

  const update = (changes) => {
    const p = new URLSearchParams(params)
    Object.entries(changes).forEach(([k, v]) => (v ? p.set(k, v) : p.delete(k)))
    setParams(p)
  }

  const stop = (e) => e.stopPropagation()
  const columns = [
    {
      key: 'select',
      className: 'w-10 pr-0',
      label: (
        <input type="checkbox" aria-label="Select all requests in this view" checked={allChecked} onChange={toggleAll}
          className="size-4 cursor-pointer accent-[var(--color-primary)]" />
      ),
      render: (r) => (
        <input type="checkbox" aria-label={`Select ${r.reference}`} checked={selected.has(r.id)} onChange={() => toggle(r.id)}
          onClick={stop} onKeyDown={stop} className="mt-0.5 size-4 cursor-pointer accent-[var(--color-primary)]"
          title={OPEN.has(r.status) ? undefined : `${r.reference} is ${r.status}`} />
      ),
    },
    {
      key: 'reference',
      label: 'Received',
      render: (r) => (
        <div className="flex flex-col">
          <span className="font-body text-xs font-semibold whitespace-nowrap text-ink">{r.reference}</span>
          <span className="font-body text-xs whitespace-nowrap text-muted tabular-nums">{formatDateTime(r.received_at)}</span>
        </div>
      ),
    },
    {
      key: 'patient',
      label: 'Patient / sender',
      render: (r) => (
        <div className="flex items-start gap-2.5">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-surface-strong font-body text-xs font-medium text-ink">
            {initials(r.patient?.full_name || r.requested_name || r.from_address)}
          </div>
          <div className="min-w-0">
            <p className="font-body text-sm font-medium text-ink">
              {r.patient?.full_name || r.requested_name || 'Unknown sender'}
              {r.priority === 'high' && (
                <Badge variant="error" className="ml-2">
                  Urgent
                </Badge>
              )}
            </p>
            <p className="max-w-[260px] truncate font-body text-xs text-muted">
              {r.subject} · {r.from_address}
            </p>
          </div>
        </div>
      ),
    },
    {
      key: 'intent',
      label: 'Intent / status',
      render: (r) => {
        const s = requestStatus(r.status)
        return (
          <div className="flex flex-col items-start gap-1">
            <span className="text-sm whitespace-nowrap">{intentLabel(r.intent)}</span>
            <Badge variant={s.variant}>{s.label}</Badge>
          </div>
        )
      },
    },
    {
      key: 'confidence',
      label: 'AI · match',
      render: (r) => (
        <div className="flex flex-col gap-1.5" title="AI confidence, then patient-match confidence">
          <Confidence value={r.ai_confidence} />
          <Confidence value={r.match_confidence} />
        </div>
      ),
    },
    {
      key: 'reasons',
      label: 'Review triggers',
      render: (r) => (
        <div className="flex max-w-[260px] flex-wrap gap-1">
          {uniqueReasons(r.review_reasons).map((x) => (
            <Badge key={x.code} variant={reasonVariant(x.code)}>
              {reasonLabel(x.code)}
            </Badge>
          ))}
        </div>
      ),
    },
  ]

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="AI email automation"
        title="Request inbox"
        subtitle="Scheduling emails converted into portal requests, matched to charts and analysed by AI."
        actions={
          <Button variant="outline" onClick={() => navigate('/demo')} className="gap-2">
            <Send size={15} className="text-muted" />
            Send a test email
          </Button>
        }
      />

      <div className="flex flex-col gap-3.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Tabs tabs={TABS.map((t) => ({ ...t, count: counts[t.id] }))} value={tab} onChange={(id) => update({ tab: id, reason: '' })} />
          {reason && (
            <div className="flex items-center gap-2 font-body text-xs text-muted">
              Trigger: <Badge variant={reasonVariant(reason)}>{reasonLabel(reason)}</Badge>
              <button className="link cursor-pointer" onClick={() => update({ reason: '' })}>
                clear
              </button>
            </div>
          )}
        </div>
        <SearchBox
          value={q}
          onChange={setQ}
          onSubmit={(v) => update({ q: v })}
          placeholder="Search by reference, patient name, MRN, subject or sender…"
        />
      </div>

      <ErrorNote error={error} />
      {selected.size > 0 && <BulkBar ids={[...selected]} onDone={afterBulk} onClear={() => setSelected(new Set())} />}
      <Toast message={toast} />
      {loading && !data ? (
        <Spinner />
      ) : !data?.length ? (
        <EmptyState icon={Inbox} title="No requests in this view">
          Try another tab, clear the search, or send a sample email from the Demo lab.
        </EmptyState>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block">
            <Table
              columns={columns}
              rows={data}
              onRowClick={(r) => navigate(`/requests/${r.id}`)}
              rowClassName={(r) => (r.priority === 'high' ? 'shadow-[inset_3px_0_0_var(--semantic-error)]' : '')}
            />
          </div>

          {/* Mobile card list */}
          <div className="flex flex-col gap-3 md:hidden">
            {data.map((r) => (
              <RequestCard key={r.id} r={r} onOpen={() => navigate(`/requests/${r.id}`)} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
