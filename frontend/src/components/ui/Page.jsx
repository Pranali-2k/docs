import { Link } from 'react-router-dom'
import { AlertCircle, ArrowLeft, Search, X } from 'lucide-react'
import { Badge } from './Badge'
import { CardLabel } from './Card'
import { ConfidenceBar } from './DataViz'
import { cn } from '@/lib/cn'
import { confidenceMeta } from '@/lib/status'

/** Editorial page header: sage dot eyebrow, display title, muted subtitle, actions. */
export function PageHeader({ eyebrow, title, subtitle, back, actions, children }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline/60 pb-5">
      <div className="min-w-0">
        {back && (
          <Link
            to={back.to}
            className="mb-2 inline-flex items-center gap-1.5 font-body text-xs font-medium text-muted transition-colors hover:text-ink hover:no-underline"
          >
            <ArrowLeft size={14} />
            {back.label}
          </Link>
        )}
        {eyebrow && (
          <div className="mb-1 flex items-center gap-2">
            <span className="size-2 rounded-full bg-sage" />
            <p className="font-body text-xs font-semibold uppercase tracking-[0.9px] text-muted">{eyebrow}</p>
          </div>
        )}
        <h1 className="flex flex-wrap items-center gap-3 font-display text-2xl font-normal tracking-tight text-ink sm:text-3xl">
          {title}
        </h1>
        {subtitle && <p className="mt-1 font-body text-sm text-muted">{subtitle}</p>}
        {children}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2.5">{actions}</div>}
    </header>
  )
}

/** Card section with an uppercase caption header and optional right-side actions. */
export function Section({ title, actions, className, bodyClassName, children }) {
  return (
    <section className={cn('rounded-xl border border-hairline/80 bg-card shadow-card', className)}>
      {(title || actions) && (
        <div className="flex items-center justify-between gap-3 border-b border-hairline px-5 py-3.5">
          <CardLabel>{title}</CardLabel>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={cn('flex flex-col gap-3.5 px-5 py-4.5', bodyClassName)}>{children}</div>
    </section>
  )
}

/** Read-only labelled value. */
export function Detail({ label, children, className }) {
  const empty = children === null || children === undefined || children === ''
  return (
    <div className={cn('flex min-w-0 flex-col gap-0.5', className)}>
      <span className="font-body text-[11px] font-semibold uppercase tracking-[0.8px] text-muted">{label}</span>
      <span className={cn('font-body text-sm break-words', empty ? 'text-muted-soft' : 'text-ink')}>
        {empty ? '—' : children}
      </span>
    </div>
  )
}

export function Confidence({ value }) {
  const m = confidenceMeta(value)
  if (value === null || value === undefined) return <span className="font-body text-xs text-muted-soft">—</span>
  return <ConfidenceBar pct={m.pct} label={m.label} color={m.color} />
}

export function ErrorNote({ error }) {
  if (!error) return null
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg border border-[#f7d4d4] bg-[#fdf2f2] px-3.5 py-2.5 font-body text-sm text-error"
    >
      <AlertCircle size={16} className="mt-0.5 shrink-0" />
      <span>{String(error.message || error)}</span>
    </div>
  )
}

export function Spinner({ className }) {
  return (
    <div className={cn('flex justify-center py-10', className)}>
      <div
        aria-label="Loading"
        className="size-7 rounded-full border-2 border-hairline border-t-ink"
        style={{ animation: 'spin .8s linear infinite' }}
      />
    </div>
  )
}

/** Segmented tab bar in the portal's filter style. */
export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="flex items-center gap-1.5 overflow-x-auto rounded-lg border border-hairline bg-surface-subtle p-1">
      {tabs.map((t) => {
        const active = value === t.id
        return (
          <button
            key={t.id}
            onClick={() => onChange(t.id)}
            className={cn(
              'flex cursor-pointer items-center gap-1.5 rounded-md px-3 py-1.5 font-body text-xs font-medium transition-all duration-150',
              active ? 'bg-card text-ink shadow-subtle' : 'text-muted hover:bg-card/50 hover:text-ink',
            )}
          >
            <span>{t.label}</span>
            {t.count !== undefined && (
              <span
                className={cn(
                  'rounded-full px-1.5 text-[10px] font-semibold',
                  active ? 'bg-strong text-ink' : 'text-muted',
                )}
              >
                {t.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export function SearchBox({ value, onChange, onSubmit, placeholder, className }) {
  return (
    <form
      className={cn('relative flex items-center', className)}
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit?.(value)
      }}
    >
      <Search size={16} className="pointer-events-none absolute left-3.5 text-muted" />
      <input
        type="text"
        placeholder={placeholder}
        aria-label={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-hairline bg-card py-2.5 pl-10 pr-9 font-body text-sm text-ink shadow-subtle transition-colors placeholder:text-muted focus:border-ink focus:outline-none"
      />
      {value && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => {
            onChange('')
            onSubmit?.('')
          }}
          className="absolute right-3 cursor-pointer text-muted hover:text-ink"
        >
          <X size={15} />
        </button>
      )}
    </form>
  )
}

/** Audit-style event list. */
export function rawDetails(details) {
  return Object.entries(details || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join('  ')
}

/** The sentence for an audit entry, with the raw data one click away. */
export function AuditText({ entry }) {
  const raw = rawDetails(entry.details)
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="font-body text-sm leading-relaxed text-body">{entry.description || entry.action.replace(/_/g, ' ')}</span>
      {raw && (
        <details className="group">
          <summary className="cursor-pointer list-none font-body text-[11px] text-muted hover:text-ink">
            <span className="group-open:hidden">Technical details</span>
            <span className="hidden group-open:inline">Hide technical details</span>
          </summary>
          <code className="mt-1 block font-mono text-[11px] leading-relaxed break-all text-muted">{raw}</code>
        </details>
      )}
    </div>
  )
}

export function Timeline({ events }) {
  if (!events?.length) return <p className="font-body text-sm text-muted">No events yet.</p>
  return (
    <ol className="flex flex-col divide-y divide-hairline">
      {events.map((t) => (
        <li key={t.id} className="grid grid-cols-1 gap-1 py-2.5 sm:grid-cols-[140px_180px_1fr] sm:gap-4">
          <span className="font-body text-xs text-muted tabular-nums">
            {new Date(t.created_at).toLocaleString('en-US', {
              month: 'short',
              day: 'numeric',
              hour: 'numeric',
              minute: '2-digit',
            })}
          </span>
          <span className="font-body text-sm font-medium capitalize text-ink">
            {t.action.replace(/_/g, ' ')}
            <span className="block font-body text-[11px] font-normal normal-case text-muted">{t.actor}</span>
          </span>
          <AuditText entry={t} />
        </li>
      ))}
    </ol>
  )
}

/** Simple data table in the portal style. */
export function Table({ columns, rows, onRowClick, rowKey = 'id', rowClassName, empty }) {
  if (!rows?.length) return empty ?? null
  return (
    <div className="overflow-x-auto rounded-xl border border-hairline bg-card shadow-card">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-hairline bg-surface-subtle">
            {columns.map((c) => (
              <th
                key={c.key}
                className={cn(
                  'px-5 py-3 text-left font-body text-[11px] font-semibold uppercase tracking-[0.8px] whitespace-nowrap text-muted',
                  c.className,
                )}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-hairline">
          {rows.map((r) => (
            <tr
              key={r[rowKey]}
              onClick={onRowClick ? () => onRowClick(r) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              onKeyDown={onRowClick ? (e) => e.key === 'Enter' && onRowClick(r) : undefined}
              className={cn(
                'align-top transition-colors',
                onRowClick && 'cursor-pointer hover:bg-surface-subtle/70',
                rowClassName?.(r),
              )}
            >
              {columns.map((c) => (
                <td key={c.key} className={cn('px-5 py-3.5 font-body text-sm text-ink', c.cellClassName)}>
                  {c.render ? c.render(r) : r[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export { Badge }
