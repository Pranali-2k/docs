import { useCallback, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeft,
  Bell,
  CalendarPlus,
  ClipboardCheck,
  ClipboardList,
  FileSignature,
  FlaskConical,
  Flag,
  FileText,
  Link2,
  ListChecks,
  Pill,
  Stethoscope,
  StickyNote,
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
import { Button } from '@/components/ui/Button'
import { TextField, SelectField } from './shared'
import { patch, useAction, useApi } from '@/lib/api'
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
  { to: 'clipboard', label: 'Patient Clipboard', icon: ClipboardCheck },
  { to: 'alerts', label: 'Alerts', icon: AlertTriangle },
  { to: 'allergies', label: 'Allergies', icon: AlertTriangle },
  { to: 'notes', label: 'Notes', icon: StickyNote },
  { to: 'reminders', label: 'Reminders', icon: Bell },
  { to: 'flags', label: 'Patient Flags', icon: Flag },
  { to: 'tasks', label: 'Tasks', icon: ListChecks },
  { to: 'links', label: 'Linked Accounts', icon: Link2 },
  { to: 'medications', label: 'Medications (Rx)', icon: Pill },
  { to: 'orders', label: 'Non-visit Orders', icon: FlaskConical },
  { to: 'procedures', label: 'Procedure Logs', icon: Stethoscope },
  { to: 'consents', label: 'Consents', icon: FileSignature },
]

/** The extra header line: PCP, PMS id, portal status, alerts, allergies, flags (editable inline). */
function HeaderStrip({ patientId, base, notify }) {
  const { data: h, setData: setH, error } = useApi(`/patients/${patientId}/header`, [patientId])
  const [edit, setEdit] = useState(false)
  const [f, setF] = useState({})
  const act = useAction()
  if (error || !h) return null
  const open = () => { setF({ pcp_name: h.pcp_name || '', pms_id: h.pms_id || '', portal_status: h.portal_status }); setEdit(true) }
  const save = () => act.run(async () => {
    setH(await patch(`/patients/${patientId}/header`, { pcp_name: f.pcp_name || null, pms_id: f.pms_id || null, portal_status: f.portal_status }))
    setEdit(false)
    notify('Header updated.')
  })
  const unspecified = (list) => list.length === 1 && list[0] === 'Unspecified'
  return (
    <div className="rounded-xl border border-hairline bg-card px-5 py-3 font-body text-xs text-body shadow-subtle">
      {edit ? (
        <div className="flex flex-wrap items-end gap-3">
          <TextField label="PCP" value={f.pcp_name} onChange={(v) => setF((p) => ({ ...p, pcp_name: v }))} />
          <TextField label="PMS ID" value={f.pms_id} onChange={(v) => setF((p) => ({ ...p, pms_id: v }))} />
          <SelectField label="Patient portal" value={f.portal_status} onChange={(v) => setF((p) => ({ ...p, portal_status: v }))} options={['inactive', 'invited', 'active']} />
          <Button disabled={act.busy} onClick={save}>Save</Button>
          <Button variant="outline" onClick={() => setEdit(false)}>Cancel</Button>
          {act.error && <span className="text-error">{act.error.message}</span>}
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
          <span><b>PCP:</b> {h.pcp_name || '---'}</span>
          <span><b>PMS ID:</b> {h.pms_id || '---'}</span>
          <span><b>Patient Portal:</b> {h.portal_status}</span>
          <Link to={`${base}/alerts`} className={unspecified(h.alerts) ? '' : 'font-semibold text-error'}><b>Alerts:</b> {h.alerts.join('; ')}</Link>
          <Link to={`${base}/allergies`} className={unspecified(h.allergies) ? '' : 'font-semibold text-error'}><b>Allergies:</b> {h.allergies.join('; ')}</Link>
          {h.flags.map((fl) => <Badge key={fl} variant="warning">{fl}</Badge>)}
          {h.sticky_notes > 0 && <Link to={`${base}/notes`}><Badge variant="warning">{h.sticky_notes} sticky note(s)</Badge></Link>}
          <button type="button" onClick={open} className="ml-auto text-xs font-medium text-primary hover:underline print:hidden">Edit</button>
        </div>
      )}
    </div>
  )
}

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
  const { pathname } = useLocation()
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

      <HeaderStrip key={pathname} patientId={chart.id} base={base} notify={notify} />

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
        <ActionTile icon={ClipboardCheck} label="Patient Clipboard" to={`${base}/clipboard`} />
        <ActionTile icon={Bell} label="Add Reminder" to={`${base}/reminders?new=1`} />
        <ActionTile icon={StickyNote} label="Sticky Note" to={`${base}/notes?new=1`} />
        <ActionTile icon={Flag} label="Patient Flag" to={`${base}/flags?new=1`} />
        <ActionTile icon={ListChecks} label="Create Task" to={`${base}/tasks?new=1`} />
        <ActionTile icon={Pill} label="Patient Rx" to={`${base}/medications`} />
        <ActionTile icon={FlaskConical} label="Non-Visit Order" to={`${base}/orders?new=1`} />
        <ActionTile icon={FileSignature} label="Create Consents" to={`${base}/consents?new=1`} />
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
