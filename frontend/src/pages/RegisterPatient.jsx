import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UserPlus } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { CompactInput, CompactSelect, CompactTextArea, FieldLabel } from '@/components/ui/Field'
import { ErrorNote, PageHeader, Section } from '@/components/ui/Page'
import { post, useAction, useApi } from '@/lib/api'
import { cn } from '@/lib/cn'

const EMPTY = {
  first_name: '', last_name: '', date_of_birth: '', sex: '', phone: '', email: '', address_line1: '', city: '',
  state: '', postal_code: '', preferred_provider_id: '', notes: '',
  payer_id: '', member_id: '', group_number: '', subscriber_name: '', relationship_to_subscriber: 'self',
}

function F({ label, id, className, children }) {
  return (
    <div className={cn('flex flex-col', className)}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      {children}
    </div>
  )
}

export default function RegisterPatient() {
  const navigate = useNavigate()
  const [f, setF] = useState(EMPTY)
  const [allowDup, setAllowDup] = useState(false)
  const { data: payers } = useApi('/payers')
  const { data: providers } = useApi('/providers')
  const act = useAction()
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const input = (k, props = {}) => <CompactInput id={k} value={f[k]} onChange={set(k)} className="w-full" {...props} />

  const submit = (e) => {
    e.preventDefault()
    act.run(async () => {
      const body = {
        first_name: f.first_name, last_name: f.last_name, date_of_birth: f.date_of_birth, sex: f.sex || null,
        phone: f.phone || null, email: f.email || null, address_line1: f.address_line1 || null, city: f.city || null,
        state: f.state || null, postal_code: f.postal_code || null, notes: f.notes || null,
        preferred_provider_id: f.preferred_provider_id ? Number(f.preferred_provider_id) : null,
        allow_duplicate: allowDup,
      }
      if (f.payer_id && f.member_id) {
        body.insurance = {
          payer_id: Number(f.payer_id), member_id: f.member_id, group_number: f.group_number || null,
          subscriber_name: f.subscriber_name || null, relationship_to_subscriber: f.relationship_to_subscriber,
        }
      }
      const chart = await post('/patients', body)
      navigate(`/patients/${chart.id}`)
    })
  }

  return (
    <div className="flex max-w-[960px] flex-col gap-6">
      <PageHeader
        back={{ to: '/patients', label: 'Back to patient charts' }}
        eyebrow="Patient registration"
        title="Register patient"
        subtitle="Capture demographics and primary medical insurance. Add photo ID and insurance card images on the chart next."
      />
      <form onSubmit={submit} className="flex flex-col gap-6">
        <Section title="Demographics">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <F label="First name *" id="first_name">{input('first_name', { required: true })}</F>
            <F label="Last name *" id="last_name">{input('last_name', { required: true })}</F>
            <F label="Date of birth *" id="date_of_birth">{input('date_of_birth', { type: 'date', required: true })}</F>
            <F label="Sex" id="sex">
              <CompactSelect id="sex" value={f.sex} onChange={set('sex')} className="w-full">
                <option value="">—</option>
                <option>F</option>
                <option>M</option>
                <option>X</option>
              </CompactSelect>
            </F>
            <F label="Phone" id="phone">{input('phone', { placeholder: '(312) 555-0100' })}</F>
            <F label="Email" id="email">{input('email', { type: 'email' })}</F>
            <F label="Address" id="address_line1" className="sm:col-span-2">{input('address_line1')}</F>
            <F label="City" id="city">{input('city')}</F>
            <F label="State" id="state">{input('state')}</F>
            <F label="ZIP" id="postal_code">{input('postal_code')}</F>
            <F label="Preferred provider" id="preferred_provider_id">
              <CompactSelect id="preferred_provider_id" value={f.preferred_provider_id} onChange={set('preferred_provider_id')} className="w-full">
                <option value="">—</option>
                {providers?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.full_name}
                  </option>
                ))}
              </CompactSelect>
            </F>
            <F label="Notes" id="notes" className="sm:col-span-3">
              <CompactTextArea id="notes" value={f.notes} onChange={set('notes')} className="w-full" />
            </F>
          </div>
        </Section>

        <Section title="Primary medical insurance">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <F label="Payer" id="payer_id">
              <CompactSelect id="payer_id" value={f.payer_id} onChange={set('payer_id')} className="w-full">
                <option value="">Self-pay / none</option>
                {payers?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </CompactSelect>
            </F>
            <F label={f.payer_id ? 'Member ID *' : 'Member ID'} id="member_id">{input('member_id', { required: !!f.payer_id })}</F>
            <F label="Group #" id="group_number">{input('group_number')}</F>
            <F label="Subscriber name" id="subscriber_name">{input('subscriber_name', { placeholder: 'If not the patient' })}</F>
            <F label="Relationship" id="relationship_to_subscriber">
              <CompactSelect id="relationship_to_subscriber" value={f.relationship_to_subscriber} onChange={set('relationship_to_subscriber')} className="w-full">
                <option>self</option>
                <option>spouse</option>
                <option>child</option>
                <option>other</option>
              </CompactSelect>
            </F>
          </div>
        </Section>

        <ErrorNote error={act.error} />
        {act.error?.status === 409 && (
          <label className="flex items-center gap-2 font-body text-sm text-body">
            <input type="checkbox" className="accent-primary" checked={allowDup} onChange={(e) => setAllowDup(e.target.checked)} />
            This is a different person. Register anyway.
          </label>
        )}
        <div className="flex gap-2.5">
          <Button type="submit" disabled={act.busy} className="gap-2 shadow-subtle">
            <UserPlus size={15} />
            Register patient
          </Button>
          <Button variant="outline" onClick={() => navigate('/patients')}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  )
}
