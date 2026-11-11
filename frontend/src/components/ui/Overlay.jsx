import { useEffect, useRef } from 'react'
import { Button } from './Button'

/**
 * Modal dialog. Closes on Escape and on scrim click, restores focus to the
 * element that opened it, and moves focus inside on mount so keyboard users
 * are not left behind the scrim.
 */
export function Modal({ title, onClose, children, footer }) {
  const panelRef = useRef(null)
  const restoreRef = useRef(null)

  useEffect(() => {
    restoreRef.current = document.activeElement
    panelRef.current?.focus()

    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.()
    }
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
      className="fixed inset-0 z-60 flex items-center justify-center bg-[rgba(12,10,9,0.4)] p-6"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose?.()
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="flex w-full max-w-[400px] flex-col gap-4 rounded-xl bg-card p-7 outline-none"
      >
        <p className="type-display-sm">{title}</p>
        {children}
        {footer && <div className="flex flex-wrap justify-end gap-2.5">{footer}</div>}
      </div>
    </div>
  )
}

/** Destructive/irreversible action gate. */
export function ConfirmDialog({ title, body, cta, onConfirm, onCancel }) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onConfirm}>
            {cta}
          </Button>
        </>
      }
    >
      <p className="font-body text-sm leading-relaxed text-body">{body}</p>
    </Modal>
  )
}

/** Transient confirmation pill, centred at the bottom of the viewport. */
export function Toast({ message }) {
  if (!message) return null
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-6 left-1/2 z-70 box-border max-w-[calc(100vw-48px)] -translate-x-1/2 rounded-pill bg-canvas-deep px-5 py-3 font-body text-sm text-white"
      style={{ animation: 'toastIn .25s ease' }}
    >
      {message}
    </div>
  )
}
