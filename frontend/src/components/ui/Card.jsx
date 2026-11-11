import { cn } from '@/lib/cn'

/**
 * Editorial Card: Clean white surface on subtle warm hairline border
 * with serene resting elevation and smooth interactive lift on hover.
 */
export function Card({
  as: Tag = 'div',
  hoverable = false,
  interactive = false,
  className,
  children,
  ...props
}) {
  return (
    <Tag
      className={cn(
        'rounded-xl border border-hairline/80 bg-card shadow-card',
        (hoverable || interactive) &&
          'transition-all duration-200 ease-out hover:border-hairline-strong hover:shadow-soft',
        interactive && 'cursor-pointer active:scale-[0.998]',
        className,
      )}
      {...props}
    >
      {children}
    </Tag>
  )
}

/** Uppercase caption header with tranquil letter-spacing. */
export function CardLabel({ className, children }) {
  return (
    <p
      className={cn(
        'font-body text-[11px] font-semibold uppercase tracking-[0.96px] text-muted',
        className,
      )}
    >
      {children}
    </p>
  )
}

/** Headline number card with calm display typography, icon container, and trend tag. */
export function StatCard({ label, value, icon: Icon, trend, trendLabel = 'vs last week' }) {
  return (
    <Card className="flex flex-col justify-between gap-3 px-5 py-4.5">
      <div className="flex items-center justify-between gap-2">
        <CardLabel>{label}</CardLabel>
        {Icon && (
          <div className="flex size-7 items-center justify-center rounded-lg bg-surface-subtle text-muted">
            <Icon size={16} strokeWidth={1.75} />
          </div>
        )}
      </div>
      <div>
        <p className="font-display text-[32px] font-normal leading-none tracking-tight text-ink">
          {value}
        </p>
        {trend && (
          <div className="mt-2 flex items-center gap-1.5 font-body text-xs text-muted">
            <span
              className={cn(
                'inline-flex items-center rounded px-1.5 py-0.2 font-medium',
                trend > 0
                  ? 'bg-sage-soft text-sage'
                  : 'bg-strong text-muted',
              )}
            >
              {trend > 0 ? `+${trend}%` : `${trend}%`}
            </span>
            <span>{trendLabel}</span>
          </div>
        )}
      </div>
    </Card>
  )
}

/** Bordered read-only block used for intake summaries. */
export function SummaryBlock({ label, children }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-hairline/70 bg-surface-subtle/50 px-5.5 py-4">
      <CardLabel>{label}</CardLabel>
      <div className="type-body-md text-ink">{children}</div>
    </div>
  )
}

/** Peaceful empty state with generous breathing room. */
export function EmptyState({ icon: Icon, title, children }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-hairline-strong/80 bg-surface-subtle/40 px-6 py-12 text-center text-muted">
      {Icon && (
        <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-strong/60 text-muted">
          <Icon size={20} strokeWidth={1.5} />
        </div>
      )}
      {title && <p className="font-body text-sm font-medium text-ink mb-1">{title}</p>}
      <p className="max-w-md type-body-sm text-muted">{children}</p>
    </div>
  )
}
