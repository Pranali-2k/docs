import { useState } from 'react'
import { Eye, EyeOff, History } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { CompactInput, CompactSelect, FieldLabel } from '@/components/ui/Field'
import { ErrorNote, Spinner, Timeline } from '@/components/ui/Page'
import { api, post, useApi } from '@/lib/api'
import { cn } from '@/lib/cn'

export const US_STATES = [
  'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME',
  'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI',
  'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
]

/** Labelled control in the EHR form grid. */
export function FormField({ label, required, htmlFor, className, children, hint }) {
  return (
    <div className={cn('flex min-w-0 flex-col', className)}>
      <FieldLabel htmlFor={htmlFor}>
        {label}
        {required && <span className="text-error"> *</span>}
      </FieldLabel>
      {children}
      {hint && <span className="mt-1 font-body text-[11px] text-muted">{hint}</span>}
    </div>
  )
}

export function TextField({ label, value, onChange, required, className, ...props }) {
  const id = `f-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <FormField label={label} required={required} htmlFor={id} className={className}>
      <CompactInput id={id} value={value ?? ''} onChange={(e) => onChange(e.target.value)} {...props} />
    </FormField>
  )
}

export function SelectField({ label, value, onChange, options, placeholder = 'Select one', className, required }) {
  const id = `f-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <FormField label={label} required={required} htmlFor={id} className={className}>
      <CompactSelect id={id} value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">{placeholder}</option>
        {options.map((o) => (typeof o === 'string' ? <option key={o} value={o}>{o}</option> : <option key={o.value} value={o.value}>{o.label}</option>))}
      </CompactSelect>
    </FormField>
  )
}

/**
 * Masked identifier (SSN / driver's license) with the eye toggle. Revealing calls the API,
 * which audits the view. Typing a new value replaces the stored one on save.
 */
export function ProtectedField({ label, masked, patientId, field, value, onChange, placeholder }) {
  const [revealed, setRevealed] = useState(null)
  const [error, setError] = useState(null)
  const id = `f-${field}`
  const toggle = async () => {
    if (revealed !== null) return setRevealed(null)
    try {
      setError(null)
      const r = await post(`/patients/${patientId}/reveal`, { field, reason: 'Viewed on chart' })
      setRevealed(r.value ?? '')
    } catch (e) {
      setError(e)
    }
  }
  const editing = value !== undefined && value !== null
  return (
    <FormField label={label} htmlFor={id}>
      <div className="relative">
        <CompactInput
          id={id}
          className="w-full pr-9"
          value={editing ? value : revealed ?? masked ?? ''}
          placeholder={placeholder}
          autoComplete="off"
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => !editing && onChange('')}
        />
        {masked && !editing && (
          <button
            type="button"
            onClick={toggle}
            aria-label={revealed !== null ? `Hide ${label}` : `Show ${label}`}
            title={revealed !== null ? 'Hide' : 'Show (audited)'}
            className="absolute top-1/2 right-2.5 -translate-y-1/2 cursor-pointer text-muted hover:text-ink"
          >
            {revealed !== null ? <EyeOff size={15} /> : <Eye size={15} />}
          </button>
        )}
      </div>
      {editing && <span className="mt-1 font-body text-[11px] text-muted">New value replaces the stored one when you save. Leave empty to keep it.</span>}
      <ErrorNote error={error} />
    </FormField>
  )
}

/** "View History" / "Show Amendments History" dialog. */
export function HistoryButton({ patientId, entity = 'patient', filter, label = 'View History' }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="inline-flex cursor-pointer items-center gap-1.5 font-body text-xs font-medium text-slate-blue hover:underline">
        <History size={13} /> {label}
      </button>
      {open && <HistoryDialog patientId={patientId} entity={entity} filter={filter} onClose={() => setOpen(false)} />}
    </>
  )
}

function HistoryDialog({ patientId, entity, filter, onClose }) {
  const { data, error } = useApi(`/patients/${patientId}/history?entity=${entity}`)
  const rows = (data || []).filter((e) => (filter ? filter(e) : true))
  return (
    <Dialog title="Amendment history" subtitle="Field-level changes, newest first" onClose={onClose}>
      <ErrorNote error={error} />
      {!data ? <Spinner /> : rows.length ? <ChangeList events={rows} /> : <p className="font-body text-sm text-muted">No changes recorded yet.</p>}
    </Dialog>
  )
}

export function ChangeList({ events }) {
  const withChanges = events.filter((e) => e.details?.changes)
  if (!withChanges.length) return <Timeline events={events} />
  return (
    <div className="flex flex-col divide-y divide-hairline">
      {events.map((e) => (
        <div key={e.id} className="flex flex-col gap-1.5 py-3">
          <p className="font-body text-xs text-muted">
            {new Date(e.created_at).toLocaleString()} · <span className="font-medium text-ink">{e.actor}</span> ·{' '}
            {e.action.replace(/_/g, ' ')}
          </p>
          {e.details?.changes ? (
            <table className="w-full font-body text-xs">
              <tbody>
                {Object.entries(e.details.changes).map(([field, ch]) => (
                  <tr key={field} className="align-top">
                    <td className="w-44 py-0.5 pr-3 font-medium capitalize text-ink">{field.replace(/_/g, ' ')}</td>
                    <td className="py-0.5 pr-3 text-muted line-through">{ch.from ?? '—'}</td>
                    <td className="py-0.5 text-ink">{ch.to ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <code className="font-mono text-[11px] text-muted">
              {Object.entries(e.details || {}).filter(([, v]) => v != null).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join('  ')}
            </code>
          )}
        </div>
      ))}
    </div>
  )
}

/** Save / Cancel bar shown at the top of editable sections (as in the EHR). */
export function SaveBar({ dirty, busy, onSave, onCancel, error }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <Button className="h-9 px-5 text-sm" disabled={!dirty || busy} onClick={onSave}>{busy ? 'Saving…' : 'Save'}</Button>
        <Button variant="outline" className="h-9 px-5 text-sm" disabled={!dirty || busy} onClick={onCancel}>Cancel</Button>
      </div>
      <ErrorNote error={error} />
    </div>
  )
}

/** Diff a form against the original record, returning only changed keys. */
export function changedFields(form, original, keys) {
  const out = {}
  for (const k of keys) {
    const a = form[k] === '' ? null : form[k]
    const b = original[k] === '' ? null : original[k]
    if ((a ?? null) !== (b ?? null)) out[k] = a
  }
  return out
}

export async function reloadChart(patientId, setChart) {
  setChart(await api(`/patients/${patientId}`))
}
