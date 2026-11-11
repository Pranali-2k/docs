import { useId } from 'react'
import { cn } from '@/lib/cn'

/**
 * DS `forms/TextInput` and its select/textarea siblings.
 *
 * The DS input has no border — focus is expressed as an inset ring that goes
 * from 1px hairline to 2px ink, which keeps the field flush on the card
 * surface. `:focus-visible`'s global outline is suppressed here so the ring is
 * the only focus affordance.
 */
const CONTROL = cn(
  'type-body-md w-full box-border rounded-md bg-card px-4 py-3 text-ink',
  'border-none outline-none focus:outline-none',
  'shadow-[inset_0_0_0_1px_var(--hairline-strong)] focus:shadow-[inset_0_0_0_2px_var(--text-ink)]',
  'transition-shadow duration-150',
)

/** Compact bordered control used inside dense portal toolbars and tables. */
const COMPACT = cn(
  'box-border rounded-md border border-hairline-strong bg-card px-3 py-2.5 text-ink',
  'font-body text-sm outline-none focus:border-ink',
  'transition-colors duration-150',
)

export function TextInput({ label, className, id, ...props }) {
  const generated = useId()
  const inputId = id ?? generated
  return (
    <label htmlFor={inputId} className={cn('flex flex-col gap-1.5', className)}>
      {label && <span className="type-body-strong text-sm">{label}</span>}
      <input id={inputId} className={cn(CONTROL, 'h-11')} {...props} />
    </label>
  )
}

export function TextArea({ label, className, id, rows = 3, ...props }) {
  const generated = useId()
  const inputId = id ?? generated
  return (
    <label htmlFor={inputId} className={cn('flex flex-col gap-1.5', className)}>
      {label && <span className="type-body-strong text-sm">{label}</span>}
      <textarea id={inputId} rows={rows} className={cn(CONTROL, 'min-h-22 resize-y')} {...props} />
    </label>
  )
}

/** Labelled textarea in the denser note-editor style (small caps label). */
export function NoteField({ label, hint, id, rows = 3, ...props }) {
  const generated = useId()
  const inputId = id ?? generated
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={inputId} className="font-body text-xs font-semibold text-muted">
        {label}
        {hint && (
          <span
            title={hint}
            className="ml-1 cursor-help border-b border-dotted border-hairline-strong"
          >
            ?
          </span>
        )}
      </label>
      <textarea
        id={inputId}
        rows={rows}
        className={cn(COMPACT, 'resize-y leading-relaxed')}
        {...props}
      />
    </div>
  )
}

export function CompactInput({ className, ...props }) {
  return <input className={cn(COMPACT, className)} {...props} />
}

export function CompactSelect({ className, children, ...props }) {
  return (
    <select className={cn(COMPACT, className)} {...props}>
      {children}
    </select>
  )
}

export function CompactTextArea({ className, rows = 2, ...props }) {
  return <textarea rows={rows} className={cn(COMPACT, 'resize-y leading-relaxed', className)} {...props} />
}

/** Small caps label used above compact controls. */
export function FieldLabel({ htmlFor, children }) {
  return (
    <label htmlFor={htmlFor} className="mb-1 block font-body text-xs font-semibold text-muted">
      {children}
    </label>
  )
}
