import { Link } from 'react-router-dom'
import { cn } from '@/lib/cn'

/**
 * DS `actions/Button`.
 *
 * One primary action only — a near-black ink pill. There is no saturated CTA
 * colour anywhere in this system, so `variant` never introduces one: outline is
 * a transparent pill on a hairline, text is an underlined label.
 */
const VARIANTS = {
  primary: 'bg-primary text-on-primary border-transparent active:bg-primary-active hover:bg-primary-active',
  outline: 'bg-transparent text-ink border-hairline-strong hover:bg-strong',
  text: 'bg-transparent text-ink border-transparent px-0 h-auto underline underline-offset-[3px] hover:text-body-strong',
}

export function Button({
  variant = 'primary',
  to,
  href,
  type = 'button',
  disabled = false,
  fullWidth = false,
  className,
  children,
  ...props
}) {
  const classes = cn(
    'type-button inline-flex items-center justify-center gap-2 rounded-pill border px-5 h-10 box-border',
    'transition-colors duration-150 cursor-pointer no-underline',
    VARIANTS[variant],
    disabled && 'opacity-45 cursor-default pointer-events-none',
    fullWidth && 'w-full',
    className,
  )

  if (to) {
    return (
      <Link to={to} className={classes} aria-disabled={disabled || undefined} {...props}>
        {children}
      </Link>
    )
  }

  if (href) {
    return (
      <a href={href} className={classes} {...props}>
        {children}
      </a>
    )
  }

  return (
    <button type={type} disabled={disabled} className={classes} {...props}>
      {children}
    </button>
  )
}
