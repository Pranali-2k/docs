import { useRef, useState } from 'react'
import { FileText, UploadCloud, X } from 'lucide-react'
import { cn } from '@/lib/cn'

export const DOC_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'application/pdf']
export const MAX_DOC_BYTES = 10 * 1024 * 1024

const kb = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`)

/** Check type and size before uploading; returns an error message or null. */
export function checkDocFile(f) {
  const type = f.type || (/\.heic$/i.test(f.name) ? 'image/heic' : '')
  if (!DOC_TYPES.includes(type)) return `${f.name}: only JPEG, PNG, WEBP, HEIC or PDF files are accepted.`
  if (f.size > MAX_DOC_BYTES) return `${f.name} is ${kb(f.size)}; the limit is 10 MB.`
  if (!f.size) return `${f.name} is empty.`
  return null
}

/**
 * Drag-and-drop area with a "browse" fallback (keyboard: Enter/Space opens the picker).
 * Controlled: `files` + `onChange(files)`; invalid files are reported through `onReject(messages)`.
 */
export function FileDropZone({ files, onChange, onReject, disabled, multiple = true }) {
  const inputRef = useRef(null)
  const [over, setOver] = useState(false)
  const depth = useRef(0) // dragenter/leave fire for child elements too

  const add = (list) => {
    const incoming = [...(list || [])]
    const errors = incoming.map(checkDocFile).filter(Boolean)
    const ok = incoming.filter((f) => !checkDocFile(f))
    const key = (f) => `${f.name}:${f.size}:${f.lastModified}`
    const merged = multiple ? [...files, ...ok.filter((f) => !files.some((x) => key(x) === key(f)))] : ok.slice(0, 1)
    onChange(merged)
    onReject?.(errors)
  }

  return (
    <div className="flex flex-col gap-2">
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        aria-label="Upload documents: drop files here or press Enter to browse"
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(e) => !disabled && (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), inputRef.current?.click())}
        onDragEnter={(e) => { e.preventDefault(); depth.current += 1; setOver(true) }}
        onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = disabled ? 'none' : 'copy' }}
        onDragLeave={() => { depth.current = Math.max(0, depth.current - 1); if (!depth.current) setOver(false) }}
        onDrop={(e) => { e.preventDefault(); depth.current = 0; setOver(false); if (!disabled) add(e.dataTransfer.files) }}
        className={cn(
          'flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors outline-none',
          'focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]',
          over ? 'border-[var(--color-primary)] bg-sage-soft' : 'border-hairline-strong bg-surface-subtle/60 hover:bg-surface-subtle',
          disabled && 'cursor-not-allowed opacity-60',
        )}
      >
        <UploadCloud size={22} className={over ? 'text-ink' : 'text-muted'} />
        <p className="font-body text-sm text-ink">
          {over ? 'Drop to add' : <>Drag &amp; drop files here, or <span className="font-semibold underline underline-offset-2">browse</span></>}
        </p>
        <p className="font-body text-[11px] text-muted">JPEG, PNG, WEBP, HEIC or PDF · up to 10 MB each</p>
        <input ref={inputRef} type="file" hidden multiple={multiple} accept={DOC_TYPES.join(',') + ',.heic'}
          onChange={(e) => { add(e.target.files); e.target.value = '' }} />
      </div>
      {files.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="flex items-center gap-2.5 rounded-md border border-hairline px-3 py-2">
              <FileText size={15} className="shrink-0 text-muted" />
              <span className="min-w-0 flex-1 truncate font-body text-xs text-ink">{f.name}</span>
              <span className="shrink-0 font-body text-[11px] text-muted tabular-nums">{kb(f.size)}</span>
              <button type="button" aria-label={`Remove ${f.name}`} disabled={disabled}
                onClick={() => onChange(files.filter((_, j) => j !== i))} className="cursor-pointer text-muted hover:text-error">
                <X size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
