import { cn } from '@/lib/cn'

const VARIANTS = {
  neutral: 'bg-strong text-ink border-transparent',
  success: 'bg-sage-soft text-sage border-sage-border',
  warning: 'bg-warm-amber-soft text-warm-amber border-warm-amber-border',
  error: 'bg-[#fdf2f2] text-error border-[#f7d4d4]',
  info: 'bg-slate-soft text-slate-blue border-slate-border',
}

/**
 * Tranquil Pill Badge:
 * Supports status variants with calm pastel fills and crisp typography,
 * or direct color overrides for backwards compatibility.
 */
export function Badge({
  variant,
  color,
  dot = false,
  className,
  children,
  ...props
}) {
  // Infer variant if color matches common semantic tokens
  let toneClass = variant ? VARIANTS[variant] : 'bg-strong text-ink border-transparent'
  
  if (!variant && color) {
    if (color.includes('success') || color.includes('#16a34a') || color.includes('#2c6e49')) {
      toneClass = VARIANTS.success
    } else if (color.includes('error') || color.includes('#dc2626') || color.includes('#b33939')) {
      toneClass = VARIANTS.error
    } else if (color.includes('warning') || color.includes('amber') || color.includes('#b8860b')) {
      toneClass = VARIANTS.warning
    }
  }

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-pill border px-2.5 py-0.5 font-body text-[11px] font-medium tracking-[0.2px]',
        toneClass,
        className,
      )}
      style={!variant && color && toneClass === VARIANTS.neutral ? { color } : undefined}
      {...props}
    >
      {dot && (
        <span
          className="size-1.5 rounded-full"
          style={{ background: color || 'currentColor' }}
        />
      )}
      {children}
    </span>
  )
}
