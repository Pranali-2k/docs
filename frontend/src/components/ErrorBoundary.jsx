import { Component } from 'react'
import { AlertTriangle } from 'lucide-react'

/**
 * Catches render errors anywhere below it so one broken screen shows a recoverable
 * message instead of a blank page. `resetKey` (the route path) clears the error on
 * navigation.
 */
export class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('UI error', error, info?.componentStack)
  }

  componentDidUpdate(prev) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="mx-auto flex max-w-lg flex-col items-center gap-3 rounded-xl border border-hairline bg-card px-8 py-10 text-center shadow-card">
        <div className="flex size-10 items-center justify-center rounded-full bg-warm-amber-soft text-warm-amber">
          <AlertTriangle size={20} />
        </div>
        <p className="font-display text-xl text-ink">This screen hit a problem</p>
        <p className="font-body text-sm text-muted">
          Your data is safe. Try reloading, or go back to the dashboard. If it keeps happening, share this message with support:
        </p>
        <code className="max-w-full font-mono text-[11px] break-words text-muted">{String(this.state.error?.message || this.state.error)}</code>
        <div className="flex gap-2">
          <button
            onClick={() => window.location.reload()}
            className="type-button h-10 cursor-pointer rounded-pill bg-primary px-5 text-on-primary hover:bg-primary-active"
          >
            Reload
          </button>
          <a href="/dashboard" className="type-button inline-flex h-10 items-center rounded-pill border border-hairline-strong px-5 hover:bg-strong hover:no-underline">
            Dashboard
          </a>
        </div>
      </div>
    )
  }
}
