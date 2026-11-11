import { cn } from '@/lib/cn'

const ORB = {
  mint: 'var(--gradient-mint)',
  peach: 'var(--gradient-peach)',
  lavender: 'var(--gradient-lavender)',
  sky: 'var(--gradient-sky)',
  rose: 'var(--gradient-rose)',
}

/**
 * The pastel radial orbs that drift behind hero copy.
 *
 * Decoration only — per the DS these never become button fills, text colours,
 * or card surfaces. Hidden from assistive tech and non-interactive.
 */
export function Orb({ tone = 'mint', className, opacity = 0.7 }) {
  return (
    <div
      aria-hidden="true"
      className={cn('pointer-events-none absolute rounded-full', className)}
      style={{
        background: `radial-gradient(circle, ${ORB[tone]}, transparent 70%)`,
        opacity,
      }}
    />
  )
}

/** DS `marketing/GradientOrbCard` — 24px radius, orb bleeding in from the top. */
export function GradientOrbCard({ tone = 'mint', eyebrow, title, children }) {
  return (
    <div className="relative overflow-hidden rounded-xxl border border-hairline-soft bg-canvas-soft p-8 text-center">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 top-[-30%] w-[70%] -translate-x-1/2 rounded-full pb-[70%]"
        style={{
          background: `radial-gradient(circle, ${ORB[tone]}, transparent 70%)`,
          opacity: 0.85,
        }}
      />
      <div className="relative flex flex-col items-center gap-2.5">
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        {title && <span className="type-display-sm">{title}</span>}
        {children && <div className="type-body-md max-w-[420px] text-body">{children}</div>}
      </div>
    </div>
  )
}

/** DS `marketing/FeatureCard`. */
export function FeatureCard({ icon, title, children }) {
  return (
    <div className="flex flex-col gap-2.5 rounded-xl border border-hairline bg-card p-6 transition-shadow duration-200 hover:shadow-soft">
      {icon && (
        <div className="flex size-8 items-center justify-center rounded-pill bg-strong text-body">
          {icon}
        </div>
      )}
      <span className="type-title-md">{title}</span>
      <div className="type-body-md text-body">{children}</div>
    </div>
  )
}

/** DS `voice/VoiceIcon` — neutral circle with initials. */
export function VoiceIcon({ initials, size = 32, className }) {
  return (
    <div
      className={cn(
        'type-caption-uppercase flex shrink-0 items-center justify-center rounded-pill bg-strong text-body-strong',
        className,
      )}
      style={{ width: size, height: size, letterSpacing: '0.5px' }}
    >
      {initials}
    </div>
  )
}

/** DS `voice/ChatBubble`. */
export function ChatBubble({ role = 'agent', children }) {
  const isUser = role === 'user'
  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'type-body-sm max-w-[75%] rounded-2xl px-4 py-2.5 leading-normal',
          isUser
            ? 'rounded-br-[4px] bg-primary text-on-primary'
            : 'rounded-bl-[4px] border border-hairline bg-card text-ink',
        )}
      >
        {children}
      </div>
    </div>
  )
}
