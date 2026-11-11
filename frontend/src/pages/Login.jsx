import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { ArrowRight, Inbox, Sparkles, Stethoscope } from 'lucide-react'
import { AuthCard, Notice } from '@/components/layout/AuthCard'
import { Button } from '@/components/ui/Button'
import { TextInput } from '@/components/ui/Field'
import { ErrorNote } from '@/components/ui/Page'
import { useAuth } from '@/lib/auth'

const HIGHLIGHTS = [
  { icon: Inbox, label: 'Email → portal requests' },
  { icon: Stethoscope, label: 'Eligibility & network checks' },
  { icon: Sparkles, label: 'AI replies or staff review' },
]

export default function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState(location.state?.email ?? '')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const destination = location.state?.from?.pathname ?? '/dashboard'
  const notice = location.state?.notice

  if (user) return <Navigate to={destination} replace />

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
      navigate(destination, { replace: true })
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthCard title="Docs Health" onSubmit={submit}>
      {notice && <Notice>{notice}</Notice>}
      <div className="flex flex-col gap-3.5">
        <TextInput
          label="Staff email"
          type="email"
          placeholder="you@docshealth.local"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="username"
          required
        />
        <TextInput
          label="Password"
          type="password"
          placeholder="••••••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
      </div>

      <div className="flex justify-end">
        <Link to="/forgot-password" state={{ email }} className="font-body text-xs text-muted transition-colors hover:text-ink">
          Forgot password?
        </Link>
      </div>

      <ErrorNote error={error} />

      <Button type="submit" fullWidth disabled={busy} className="h-11 gap-2 shadow-subtle">
        {busy ? 'Signing in…' : 'Sign in to scheduling portal'}
        <ArrowRight size={15} />
      </Button>

      <div className="grid grid-cols-3 gap-2 border-t border-hairline pt-4">
        {HIGHLIGHTS.map(({ icon: Icon, label }) => (
          <div key={label} className="flex flex-col items-center gap-1 rounded-lg border border-hairline/80 bg-surface-subtle/80 p-2.5 text-center">
            <Icon size={16} className="text-muted" />
            <span className="font-body text-[11px] leading-tight font-medium text-body">{label}</span>
          </div>
        ))}
      </div>
    </AuthCard>
  )
}
