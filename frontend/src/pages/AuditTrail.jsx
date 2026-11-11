import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { History } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/Card'
import { AuditText, ErrorNote, PageHeader, Spinner, Table, Tabs } from '@/components/ui/Page'
import { useApi } from '@/lib/api'
import { formatDateTime } from '@/lib/format'

const link = (t) => (t.entity_type === 'request' ? `/requests/${t.entity_id}` : t.entity_type === 'patient' ? `/patients/${t.entity_id}` : null)

export default function AuditTrail() {
  const navigate = useNavigate()
  const [type, setType] = useState('all')
  const { data, error, loading } = useApi(`/audit?limit=300${type !== 'all' ? `&entity_type=${type}` : ''}`)

  const columns = [
    { key: 'time', label: 'Time', render: (t) => formatDateTime(t.created_at), cellClassName: 'whitespace-nowrap text-xs text-muted tabular-nums' },
    { key: 'entity', label: 'Record', render: (t) => <Badge variant={t.entity_type === 'request' ? 'info' : t.entity_type === 'patient' ? 'success' : 'neutral'}>{t.record_label || `${t.entity_type} #${t.entity_id}`}</Badge> },
    { key: 'action', label: 'Action', render: (t) => <span className="font-medium capitalize">{t.action.replace(/_/g, ' ')}</span> },
    { key: 'actor', label: 'Actor', cellClassName: 'text-xs text-muted' },
    { key: 'details', label: 'Details', render: (t) => <AuditText entry={t} />, cellClassName: 'min-w-[320px]' },
  ]

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Compliance"
        title="Audit trail"
        subtitle="Every automated and staff action: AI decisions, chart updates, eligibility checks, bookings and emails."
      />
      <Tabs
        tabs={[
          { id: 'all', label: 'All events' },
          { id: 'request', label: 'Requests' },
          { id: 'patient', label: 'Patients' },
          { id: 'appointment', label: 'Appointments' },
        ]}
        value={type}
        onChange={setType}
      />
      <ErrorNote error={error} />
      {loading && !data ? (
        <Spinner />
      ) : (
        <Table
          columns={columns}
          rows={data}
          onRowClick={(t) => link(t) && navigate(link(t))}
          empty={<EmptyState icon={History} title="No events yet">Actions appear here as soon as they happen.</EmptyState>}
        />
      )}
    </div>
  )
}
