import { useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  CalendarDays,
  FlaskConical,
  History,
  Inbox,
  LayoutDashboard,
  LogOut,
  Mail,
  Menu,
  Plug,
  Settings,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { useAuth } from '@/lib/auth'
import { useApi } from '@/lib/api'
import { cn } from '@/lib/cn'
import { initials } from '@/lib/format'

const NAV = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/requests', label: 'Request inbox', icon: Inbox, count: 'review' },
  { to: '/patients', label: 'Patient charts', icon: Users },
  { to: '/schedule', label: 'Schedule', icon: CalendarDays },
  { to: '/intramail', label: 'Intramail', icon: Mail },
  { to: '/emails', label: 'Email log', icon: Mail },
  { to: '/audit', label: 'Audit trail', icon: History },
  { to: '/demo', label: 'Demo lab', icon: FlaskConical },
  { to: '/settings', label: 'Settings', icon: Settings, end: true },
  { to: '/settings/integrations', label: 'Integrations', icon: Plug, adminOnly: true },
  { to: '/settings/clinical', label: 'Clinical reference', icon: Settings, adminOnly: true },
]

function Brand({ compact = false }) {
  return (
    <div className="flex items-center gap-3 px-2">
      <div
        className={cn(
          'flex items-center justify-center rounded-lg bg-surface-strong text-ink shadow-subtle',
          compact ? 'size-7' : 'size-9',
        )}
      >
        <span className={cn('font-display font-normal', compact ? 'text-base' : 'text-lg')}>D</span>
      </div>
      <div>
        <p className="font-display text-[17px] leading-snug font-normal tracking-tight text-ink">Docs Health</p>
        {!compact && (
          <div className="mt-0.5 flex items-center gap-1.5">
            <span className="size-1.5 animate-pulse rounded-full bg-sage" />
            <p className="font-body text-[10px] font-semibold uppercase tracking-[0.9px] text-muted">
              Scheduling AI Portal
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function NavItems({ counts, onNavigate, role }) {
  return NAV.filter((item) => !item.adminOnly || role === 'admin').map((item) => {
    const Icon = item.icon
    const count = item.count ? counts[item.count] : 0
    return (
      <NavLink
        key={item.to}
        to={item.to}
        end={item.end}
        onClick={onNavigate}
        className={({ isActive }) =>
          cn(
            'group flex items-center justify-between gap-3 rounded-lg px-3 py-2.5 font-body text-sm font-medium transition-all duration-150 hover:no-underline',
            isActive ? 'bg-strong text-ink shadow-subtle' : 'text-body hover:bg-surface-subtle hover:text-ink',
          )
        }
      >
        <div className="flex items-center gap-2.5">
          <Icon size={18} strokeWidth={1.75} className="text-muted transition-colors group-hover:text-ink" />
          <span>{item.label}</span>
        </div>
        {count > 0 && (
          <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-warm-amber-soft px-1.5 py-0.5 font-body text-[11px] font-semibold text-warm-amber">
            {count}
          </span>
        )}
      </NavLink>
    )
  })
}

export function PortalShell() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [navOpen, setNavOpen] = useState(false)
  const { data: dash } = useApi('/dashboard/summary')
  const counts = { review: dash?.needs_review ?? 0 }

  const onLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="flex min-h-screen flex-col bg-canvas font-body text-ink antialiased selection:bg-sage-soft selection:text-sage md:flex-row">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-64 print:!hidden shrink-0 flex-col justify-between border-r border-hairline bg-card px-4 py-6 shadow-subtle md:flex">
        <div className="flex flex-col gap-6">
          <Brand />
          <nav className="flex flex-col gap-1">
            <NavItems counts={counts} role={user?.role} />
          </nav>
        </div>

        <div className="flex flex-col gap-3 border-t border-hairline pt-4">
          <div className="flex items-center gap-3 px-2">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-surface-strong font-body text-xs font-semibold text-ink">
              {initials(user?.full_name)}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate font-body text-[13px] leading-tight font-medium text-ink">{user?.full_name}</p>
              <p className="truncate font-body text-[11px] text-muted capitalize">{user?.role}</p>
            </div>
            <button
              onClick={onLogout}
              aria-label="Log out"
              title="Log out"
              className="flex size-7 cursor-pointer items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-subtle hover:text-ink"
            >
              <LogOut size={15} strokeWidth={1.75} />
            </button>
          </div>
          <div className="flex items-center justify-between rounded-lg bg-surface-subtle/70 px-2.5 py-1.5 text-[11px] text-muted">
            <span className="flex items-center gap-1.5">
              <ShieldCheck size={13} className="text-sage" />
              Audited PHI workspace
            </span>
            <span className="font-mono text-[10px] text-muted-soft">v0.2</span>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile header */}
        <header className="sticky top-0 z-20 flex items-center justify-between border-b border-hairline bg-card/95 px-4 py-3 backdrop-blur-sm md:hidden print:hidden">
          <Brand compact />
          <button
            onClick={() => setNavOpen((v) => !v)}
            aria-expanded={navOpen}
            aria-label="Toggle navigation"
            className="flex size-8 cursor-pointer items-center justify-center rounded-md text-ink hover:bg-surface-subtle"
          >
            {navOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </header>
        {navOpen && (
          <div className="fixed inset-x-0 top-13 z-30 flex flex-col gap-1 border-b border-hairline bg-card p-4 shadow-elevated md:hidden">
            <NavItems counts={counts} role={user?.role} onNavigate={() => setNavOpen(false)} />
            <div className="mt-2 border-t border-hairline pt-3">
              <button
                onClick={onLogout}
                className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-error hover:bg-red-50"
              >
                <LogOut size={16} />
                Log out ({user?.full_name})
              </button>
            </div>
          </div>
        )}

        <main className="mx-auto box-border w-full max-w-[1400px] flex-1 px-4 pt-6 pb-14 sm:px-6 md:px-10 md:pt-9 md:pb-20">
          <ErrorBoundary resetKey={pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
