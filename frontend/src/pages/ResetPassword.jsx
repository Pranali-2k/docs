import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Check, KeyRound, X } from 'lucide-react'
import { AuthCard } from '@/components/layout/AuthCard'
import { Button } from '@/components/ui/Button'
import { TextInput } from '@/components/ui/Field'
import { ErrorNote, Spinner } from '@/components/ui/Page'
import { api, post } from '@/lib/api'
import { cn } from '@/lib/cn'

/** Mirrors backend/app/services/password_reset.py so the user sees problems before submitting. */
const RULES = [
  { label: 'At least 10 characters', test: (p) => p.length >= 10 },
  { label: 'Upper- and lower-case letters', test: (p) => /[a-z]/.test(p) && /[A-Z]/.test(p) },
  { label: 'A number', test: (p) => /\d/.test(p) },
  { label: 'A symbol', test: (p) => /[^A-Za-z0-9]/.test(p) },
]

export default function ResetPassword() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const navigate = useNavigate()
  const [link, setLink] = useState({ loading: true })
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const r = await api(`/auth/reset-password/verify?token=${encodeURIComponent(token)}`)
        if (!cancelled) setLink({ loading: false, ...r })
      } catch (err) {
        if (!cancelled) setLink({ loading: false, error: err })
      }
    }
    if (token) check()
    else setLink({ loading: false, error: new Error('This reset link is incomplete. Open the link from your email again.') })
    return () => {
      cancelled = true
    }
  }, [token])

  const checks = useMemo(() => RULES.map((r) => ({ ...r, ok: r.test(password) })), [password])
  const mismatch = confirm.length > 0 && confirm !== password
  const ready = checks.every((c) => c.ok) && password === confirm

  const submit = async (e) => {
    e.preventDefault()
    if (!ready) return
    setBusy(true)
    setError(null)
    try {
      const r = await post('/auth/reset-password', { token, new_password: password })
      navigate('/login', { replace: true, state: { email: r.email, notice: r.message } })
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  if (link.loading) {
    return (
      <AuthCard title="Checking your link">
        <Spinner className="py-4" />
      </AuthCard>
    )
  }

  if (link.error) {
    return (
      <AuthCard title="Link can't be used" onSubmit={(e) => e.preventDefault()}>
        <ErrorNote error={link.error} />
        <Button to="/forgot-password" fullWidth className="h-11">
          Request a new link
        </Button>
        <Link to="/login" className="inline-flex items-center justify-center gap-1.5 font-body text-xs text-muted hover:text-ink">
          <ArrowLeft size={13} /> Back to sign in
        </Link>
      </AuthCard>
    )
  }

  return (
    <AuthCard title="Choose a new password" subtitle={`Identity verified for ${link.email}`} onSubmit={submit}>
      <div className="flex flex-col gap-3.5">
        <TextInput
          label="New password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          required
          autoFocus
        />
        <TextInput
          label="Confirm new password"
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
          required
        />
      </div>

      <ul className="grid grid-cols-1 gap-1.5 sm:grid-cols-2" aria-label="Password requirements">
        {checks.map((c) => (
          <li key={c.label} className={cn('flex items-center gap-1.5 font-body text-xs', c.ok ? 'text-sage' : 'text-muted')}>
            {c.ok ? <Check size={13} /> : <X size={13} />}
            {c.label}
          </li>
        ))}
        {mismatch && <li className="font-body text-xs text-error sm:col-span-2">Passwords don't match.</li>}
      </ul>

      <ErrorNote error={error} />

      <Button type="submit" fullWidth disabled={busy || !ready} className="h-11 gap-2 shadow-subtle">
        <KeyRound size={15} />
        {busy ? 'Saving…' : 'Set new password'}
      </Button>
      <p className="text-center font-body text-[11px] text-muted">
        Changing your password signs you out everywhere else.
      </p>
    </AuthCard>
  )
}
