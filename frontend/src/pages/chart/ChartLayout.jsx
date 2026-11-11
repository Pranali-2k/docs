import { useCallback, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  CalendarPlus,
  ClipboardList,
  FileText,
  History,
  Home,
  IdCard,
  Inbox,
  Mail,
  Paperclip,
  Printer,
  ShieldCheck,
  ShieldPlus,
  UserCog,
  UserRound,
  Users,
  Wallet,
} from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Toast } from '@/components/ui/Overlay'
import { ErrorNote, Spinner } from '@/components/ui/Page'
import { useApi } from '@/lib/api'
import { cn } from '@/lib/cn'
import { ageFrom, formatDate, initials } from '@/lib/format'

/** Left-hand chart sections (EHR "Patient Home / Contact Information / ..." menu). */
const SECTIONS = [
  { to: '', label: 'Patient Home', icon: Home, end: true },
  { to: 'contact', label: 'Contact Information', icon: UserRound },
  { to: 'guarantor', label: 'Guarantor', icon: Wallet },
  { to: 'identity', label: 'Patient Identity', icon: IdCard },
  { to: 'appointments', label: 'Appointment History', icon: CalendarPlus },
  { to: 'phi', label: 'Release Of PHI', icon: ShieldCheck },
  { to: 'insurance', label: 'Insurance', icon: ShieldPlus },
  { to: 'requests', label: 'Portal Requests', icon: Inbox },
  { to: 'history', label: 'Chart History', icon: History },
]

function ActionTile({ icon: Icon, label, onClick, to, href }) {
  const cls =
    'flex min-w-[88px] cursor-pointer flex-col items-center gap-1.5 rounded-lg px-3 py-2.5 text-center font-body text-[11px] leading-tight font-medium text-body transition-colors hover:bg-surface-subtle hover:text-ink hover:no-underline'
  const inner = (
    <>
      <span className="flex size-8 items-center justify-center rounded-lg bg-surface-strong text-ink">
        <Icon size={16} strokeWidth={1.75} />
      </span>
      {label}
    </>
  )
  if (to) return <Link to={to} className={cls}>{inner}</Link>
  if (href) return <a href={href} className={cls}>{inner}</a>
  return <button type="button" onClick={onClick} className={cls}>{inner}</button>
}

export default function ChartLayout() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { data: chart, setData: setChart, error, reload } = useApi(`/patients/${id}`, [id])
  const [toast, setToast] = useState('')
  const notify = useCallback((msg) => {
    setToast(msg)
    setTimeout(() => setToast(''), 2800)
  }, [])

  if (error) return <ErrorNote error={error} />
  if (!chart) return <Spinner />

  const primary = chart.policies.find((p) => p.is_active && p.coverage_order === 'primary')
  const base = `/patients/${chart.id}`
  const age = ageFrom(chart.date_of_birth)

  return (
    <div className="flex flex-col gap-5">
      <Link to="/patients" className="inline-flex items-center gap-1.5 font-body text-xs font-medium text-muted hover:text-ink print:hidden">
        <ArrowLeft size={14} /> Patient charts
      </Link>

      {/* Patient banner */}
      <header className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-hairline bg-card px-5 py-4 shadow-card">
        <div className="flex min-w-0 items-center gap-3.5">
          <div className="flex size-11 shrink-0 items-center justify-center rounded-full bg-surface-strong font-body text-sm font-semibold text-ink">
            {initials(chart.full_name)}
          </div>
          <div className="min-w-0">
            <h1 className="flex flex-wrap items-center gap-2 font-display text-2xl leading-tight text-ink">
              {chart.last_name?.toUpperCase()}, {chart.first_name}
              {chart.preferred_name && <span className="font-body text-sm text-muted">“{chart.preferred_name}”</span>}
              {chart.is_test_patient && <Badge variant="warning">Test patient</Badge>}
            </h1>
            <p className="font-body text-xs text-muted">
              MRN {chart.mrn} · DOB {formatDate(chart.date_of_birth)}
              {age !== null && ` (${age}y)`} · {chart.sex || '—'} · {chart.phone || 'no phone'}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {primary ? (
            <>
              <span className="font-body text-xs text-muted">{primary.payer_name}</span>
              <Badge variant={primary.eligibility_status === 'active' ? 'success' : primary.eligibility_status === 'inactive' ? 'error' : 'neutral'}>
                Eligibility: {primary.eligibility_status || 'not checked'}
              </Badge>
            </>
          ) : (
            <Badge variant="error">No insurance on file</Badge>
          )}
        </div>
      </header>

      {/* Action toolbar */}
      <nav aria-label="Chart actions" className="flex gap-1 overflow-x-auto print:hidden rounded-xl border border-hairline bg-card p-1.5 shadow-subtle">
        <ActionTile icon={ClipboardList} label="Patient Home" to={base} />
        <ActionTile icon={CalendarPlus} label="Create Visit" to={`/schedule?patient=${chart.id}`} />
        <ActionTile icon={UserCog} label="Edit Patient Data" to={`${base}/contact`} />
        <ActionTile icon={ShieldPlus} label="Insurance" to={`${base}/insurance`} />
        <ActionTile
          icon={ShieldCheck}
          label="Check Eligibility"
          onClick={() => navigate(primary ? `${base}/insurance/${primary.id}#eligibility` : `${base}/insurance`)}
        />
        <ActionTile icon={Paperclip} label="Manage Attachments" to={`${base}/identity`} />
        <ActionTile icon={Users} label="Release Of PHI" to={`${base}/phi`} />
        {chart.email && <ActionTile icon={Mail} label="Email Patient" href={`mailto:${chart.email}`} />}
        <ActionTile icon={Printer} label="Face Sheet" onClick={() => navigate(`${base}?print=1`)} />
        <ActionTile icon={FileText} label="Chart History" to={`${base}/history`} />
      </nav>

      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[220px_1fr] print:block">
        {/* Section menu */}
        <aside className="flex gap-1 overflow-x-auto print:hidden rounded-xl border border-hairline bg-card p-1.5 shadow-subtle lg:sticky lg:top-6 lg:flex-col">
          {SECTIONS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={label}
              to={to ? `${base}/${to}` : base}
              end={end}
              className={({ isActive }) =>
                cn(
                  'flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2.5 font-body text-[13px] font-medium whitespace-nowrap transition-colors hover:no-underline',
                  isActive ? 'bg-primary text-on-primary' : 'text-body hover:bg-surface-subtle hover:text-ink',
                )
              }
            >
              <Icon size={15} strokeWidth={1.75} />
              {label}
            </NavLink>
          ))}
        </aside>

        <section className="min-w-0">
          <Outlet context={{ chart, setChart, reload, notify }} />
        </section>
      </div>
      <Toast message={toast} />
    </div>
  )
}
