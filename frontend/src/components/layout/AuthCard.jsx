import { ShieldCheck } from 'lucide-react'
import { Orb } from '@/components/ui/Atmosphere'

/** Centered auth card on the atmospheric canvas, shared by sign-in and password reset. */
export function AuthCard({ title, subtitle, children, onSubmit }) {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-canvas p-4 font-body text-ink sm:p-6">
      <Orb tone="mint" className="top-[-10%] left-[5%] size-120" opacity={0.5} />
      <Orb tone="lavender" className="right-[5%] bottom-[-15%] size-130" opacity={0.45} />
      <Orb tone="sky" className="top-[30%] right-[-10%] size-90" opacity={0.3} />

      <div className="relative z-10 w-full max-w-[420px]">
        <form
          onSubmit={onSubmit}
          noValidate={false}
          className="box-border flex flex-col gap-5 rounded-2xl border border-hairline/80 bg-card p-8 shadow-elevated"
        >
          <div className="flex flex-col items-center text-center">
            <div className="mb-3 flex size-12 items-center justify-center rounded-xl bg-surface-strong text-ink shadow-subtle">
              <span className="font-display text-2xl font-normal">D</span>
            </div>
            <h1 className="font-display text-2xl font-normal tracking-tight text-ink">{title}</h1>
            {subtitle ? (
              <p className="mt-1 font-body text-sm text-muted">{subtitle}</p>
            ) : (
              <div className="mt-1 flex items-center gap-1.5">
                <span className="size-1.5 animate-pulse rounded-full bg-sage" />
                <p className="font-body text-xs font-semibold uppercase tracking-[0.9px] text-muted">Scheduling AI Portal</p>
              </div>
            )}
          </div>
          {children}
          <div className="flex items-center justify-center gap-1.5 text-[11px] text-muted">
            <ShieldCheck size={13} className="shrink-0 text-sage" />
            <span>Every action is written to the audit trail</span>
          </div>
        </form>
      </div>
    </div>
  )
}

export function Notice({ tone = 'success', children }) {
  const tones = {
    success: 'border-sage-border bg-sage-soft text-sage',
    info: 'border-slate-border bg-slate-soft text-slate-blue',
  }
  return <div role="status" className={`rounded-lg border px-3.5 py-2.5 font-body text-sm ${tones[tone]}`}>{children}</div>
}
