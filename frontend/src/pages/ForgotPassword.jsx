import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ArrowLeft, MailCheck, Send } from 'lucide-react'
import { AuthCard, Notice } from '@/components/layout/AuthCard'
import { Button } from '@/components/ui/Button'
import { TextInput } from '@/components/ui/Field'
import { ErrorNote } from '@/components/ui/Page'
import { post } from '@/lib/api'

export default function ForgotPassword() {
  const location = useLocation()
  const [email, setEmail] = useState(location.state?.email ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [sent, setSent] = useState(null)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await post('/auth/forgot-password', { email })
      setSent(r.message)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthCard title="Reset your password" subtitle="We'll email a link to verify it's you." onSubmit={submit}>
      {sent ? (
        <>
          <div className="flex flex-col items-center gap-2 text-center">
            <div className="flex size-10 items-center justify-center rounded-full bg-sage-soft text-sage">
              <MailCheck size={20} />
            </div>
            <Notice>{sent}</Notice>
            <p className="font-body text-xs text-muted">
              The link expires soon and works once. Didn't get it? Check spam, or request another in a few minutes.
            </p>
          </div>
          <Button variant="outline" fullWidth onClick={() => setSent(null)}>
            Send another link
          </Button>
        </>
      ) : (
        <>
          <TextInput
            label="Registered staff email"
            type="email"
            placeholder="you@docshealth.local"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
            autoFocus
          />
          <ErrorNote error={error} />
          <Button type="submit" fullWidth disabled={busy || !email} className="h-11 gap-2 shadow-subtle">
            <Send size={15} />
            {busy ? 'Sending…' : 'Email me a reset link'}
          </Button>
        </>
      )}
      <Link to="/login" className="inline-flex items-center justify-center gap-1.5 font-body text-xs text-muted hover:text-ink">
        <ArrowLeft size={13} /> Back to sign in
      </Link>
    </AuthCard>
  )
}
