import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'

const SIZES = { md: 'max-w-[560px]', lg: 'max-w-[820px]', xl: 'max-w-[1100px]' }

/**
 * Large dialog (the UI kit's Modal is a 400px confirm box). Escape / scrim click close it,
 * focus moves inside on open and returns to the opener on close.
 */
export function Dialog({ title, subtitle, onClose, size = 'lg', headerTone = 'dark', children, footer }) {
  const panelRef = useRef(null)
  const restoreRef = useRef(null)

  useEffect(() => {
    restoreRef.current = document.activeElement
    panelRef.current?.focus()
    const onKey = (e) => e.key === 'Escape' && onClose?.()
    document.addEventListener('keydown', onKey)
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      restoreRef.current?.focus?.()
    }
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-60 flex items-start justify-center overflow-y-auto bg-[rgba(12,10,9,0.4)] p-3 sm:p-6"
      onClick={(e) => e.target === e.currentTarget && onClose?.()}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        tabIndex={-1}
        className={cn('my-4 flex w-full flex-col overflow-hidden rounded-xl bg-card shadow-elevated outline-none', SIZES[size])}
      >
        <div
          className={cn(
            'flex items-center justify-between gap-3 px-5 py-3.5',
            headerTone === 'dark' ? 'bg-canvas-deep text-on-dark' : 'border-b border-hairline',
          )}
        >
          <div className="min-w-0">
            <p className="truncate font-display text-xl">{title}</p>
            {subtitle && <p className={cn('font-body text-xs', headerTone === 'dark' ? 'text-on-dark-soft' : 'text-muted')}>{subtitle}</p>}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className={cn('flex size-8 cursor-pointer items-center justify-center rounded-md',
              headerTone === 'dark' ? 'hover:bg-dark-elevated' : 'text-muted hover:bg-strong')}
          >
            <X size={18} />
          </button>
        </div>
        <div className="flex flex-col gap-4 p-5">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2.5 border-t border-hairline px-5 py-3.5">{footer}</div>}
      </div>
    </div>
  )
}
