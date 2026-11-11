import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronRight, UserPlus, Users } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/Card'
import { ErrorNote, PageHeader, SearchBox, Spinner, Table } from '@/components/ui/Page'
import { useApi } from '@/lib/api'
import { formatDate, initials } from '@/lib/format'

export default function Patients() {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const { data, error, loading } = useApi(`/patients?q=${encodeURIComponent(query)}`)

  const columns = [
    {
      key: 'name',
      label: 'Patient',
      render: (p) => (
        <div className="flex items-center gap-2.5">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-surface-strong font-body text-xs font-medium text-ink">
            {initials(p.full_name)}
          </div>
          <span className="font-medium">{p.full_name}</span>
        </div>
      ),
    },
    { key: 'mrn', label: 'MRN', cellClassName: 'text-muted tabular-nums' },
    { key: 'dob', label: 'Date of birth', render: (p) => formatDate(p.date_of_birth), cellClassName: 'whitespace-nowrap' },
    { key: 'phone', label: 'Phone', cellClassName: 'whitespace-nowrap' },
    { key: 'email', label: 'Email', cellClassName: 'text-muted' },
    {
      key: 'ins',
      label: 'Primary insurance',
      render: (p) =>
        p.primary_payer ? (
          <span>
            {p.primary_payer} <span className="text-xs text-muted">· {p.member_id}</span>
          </span>
        ) : (
          <Badge variant="error">No insurance</Badge>
        ),
    },
    { key: 'go', label: '', render: () => <ChevronRight size={14} className="text-muted" /> },
  ]

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Patient registration"
        title="Patient charts"
        subtitle="Demographics, insurance, eligibility history, documents and appointments."
        actions={
          <Button onClick={() => navigate('/patients/new')} className="gap-2 shadow-subtle">
            <UserPlus size={15} />
            Register patient
          </Button>
        }
      />
      <SearchBox
        value={q}
        onChange={setQ}
        onSubmit={setQuery}
        placeholder="Search by name, MRN, phone, email or insurance member ID…"
      />
      <ErrorNote error={error} />
      {loading && !data ? (
        <Spinner />
      ) : (
        <Table
          columns={columns}
          rows={data}
          onRowClick={(p) => navigate(`/patients/${p.id}`)}
          empty={
            <EmptyState icon={Users} title="No patients found">
              Try a different search, or register a new patient.
            </EmptyState>
          }
        />
      )}
    </div>
  )
}
