import { useEffect, useRef, useState } from 'react'
import { Search } from 'lucide-react'
import { CompactInput } from '@/components/ui/Field'
import { api } from '@/lib/api'
import { FormField } from './shared'

/** Typeahead popover list shared by payer and plan lookups. */
function Typeahead({ id, value, onQuery, results, onPick, placeholder, render, disabled }) {
  const [open, setOpen] = useState(false)
  const box = useRef(null)
  useEffect(() => {
    const close = (e) => box.current && !box.current.contains(e.target) && setOpen(false)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])
  return (
    <div ref={box} className="relative">
      <Search size={14} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" />
      <CompactInput
        id={id}
        className="w-full pl-8"
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        autoComplete="off"
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          onQuery(e.target.value)
          setOpen(true)
        }}
      />
      {open && results?.length > 0 && (
        <ul role="listbox" className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-hairline bg-card py-1 shadow-elevated">
          {results.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                className="flex w-full cursor-pointer flex-col items-start px-3 py-2 text-left hover:bg-surface-subtle"
                onClick={() => {
                  onPick(r)
                  setOpen(false)
                }}
              >
                {render(r)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** "Payer Look-up": search by payer name, alias or electronic payer ID (e.g. 94036). */
export function PayerLookup({ value, onChange, required }) {
  const [q, setQ] = useState(value?.label ?? '')
  const [results, setResults] = useState([])
  useEffect(() => setQ(value?.label ?? ''), [value])
  useEffect(() => {
    let live = true
    const t = setTimeout(async () => {
      try {
        const rows = await api(`/payers?q=${encodeURIComponent(q && q !== value?.label ? q : '')}`)
        if (live) setResults(rows)
      } catch {
        if (live) setResults([])
      }
    }, 200)
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [q, value])
  return (
    <FormField label="Payer" required={required} htmlFor="payer-lookup" hint={value?.financial_category ? `Financial category: ${value.financial_category}` : 'Type a payer name or electronic payer ID'}>
      <Typeahead
        id="payer-lookup"
        value={q}
        onQuery={setQ}
        results={results}
        placeholder="Payer look-up…"
        onPick={(p) => onChange(p)}
        render={(p) => (
          <>
            <span className="font-body text-sm text-ink">{p.label}</span>
            <span className="font-body text-[11px] text-muted">{p.financial_category}</span>
          </>
        )}
      />
    </FormField>
  )
}

/** Plan name lookup for the chosen payer ("Start typing a payer plan name..."). */
export function PlanLookup({ payerId, value, onChange }) {
  const [q, setQ] = useState(value ?? '')
  const [results, setResults] = useState([])
  useEffect(() => setQ(value ?? ''), [value])
  useEffect(() => {
    if (!payerId) return setResults([])
    let live = true
    const t = setTimeout(async () => {
      try {
        const rows = await api(`/payers/${payerId}/plans?q=${encodeURIComponent(q === value ? '' : q)}`)
        if (live) setResults(rows)
      } catch {
        if (live) setResults([])
      }
    }, 200)
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [payerId, q, value])
  return (
    <FormField label="Plan Name" htmlFor="plan-lookup">
      <Typeahead
        id="plan-lookup"
        value={q}
        disabled={!payerId}
        onQuery={(v) => {
          setQ(v)
          onChange(v || null, null)
        }}
        results={results}
        placeholder={payerId ? 'Start typing a payer plan name…' : 'Choose a payer first'}
        onPick={(p) => onChange(p.name, p.plan_type)}
        render={(p) => (
          <span className="flex w-full justify-between gap-3 font-body text-sm text-ink">
            {p.name} <span className="text-xs text-muted">{p.plan_type}</span>
          </span>
        )}
      />
    </FormField>
  )
}
