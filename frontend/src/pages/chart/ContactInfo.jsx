import { useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { patch, useAction } from '@/lib/api'
import { Section } from '@/components/ui/Page'
import {
  HistoryButton,
  ProtectedField,
  SaveBar,
  SelectField,
  TextField,
  US_STATES,
  changedFields,
} from './shared'

const MARITAL = [
  { value: 'unspecified', label: 'Unspecified' }, { value: 'single', label: 'Single' },
  { value: 'married', label: 'Married' }, { value: 'domestic_partner', label: 'Domestic partner' },
  { value: 'separated', label: 'Separated' }, { value: 'divorced', label: 'Divorced' }, { value: 'widowed', label: 'Widowed' },
]
const PREFIXES = ['Mr.', 'Ms.', 'Mrs.', 'Mx.', 'Dr.']
const KEYS = [
  'prefix', 'first_name', 'middle_name', 'last_name', 'suffix', 'preferred_name', 'is_test_patient', 'marital_status',
  'previous_name', 'drivers_license_state', 'date_of_birth', 'sex', 'phone', 'home_phone', 'work_phone',
  'preferred_contact_method', 'email', 'address_line1', 'address_line2', 'city', 'state', 'postal_code',
]

function fromChart(c) {
  const f = {}
  KEYS.forEach((k) => (f[k] = c[k] ?? null))
  f.is_test_patient = !!c.is_test_patient
  return f
}

/** Contact Information: the EHR "Patient Data" form. */
export default function ContactInfo() {
  const { chart, setChart, notify } = useOutletContext()
  const original = useMemo(() => fromChart(chart), [chart])
  const [f, setF] = useState(original)
  const [ssn, setSsn] = useState(null)
  const [license, setLicense] = useState(null)
  const act = useAction()
  useEffect(() => {
    setF(original)
    setSsn(null)
    setLicense(null)
  }, [original])

  const set = (k) => (v) => setF((prev) => ({ ...prev, [k]: v }))
  const diff = changedFields(f, original, KEYS)
  const dirty = Object.keys(diff).length > 0 || !!ssn || !!license

  const save = () =>
    act.run(async () => {
      const body = { ...diff }
      if (ssn) body.ssn = ssn
      if (license) body.drivers_license_number = license
      setChart(await patch(`/patients/${chart.id}`, body))
      notify('Patient data saved.')
    })

  return (
    <div className="flex flex-col gap-4">
      <SaveBar dirty={dirty} busy={act.busy} error={act.error} onSave={save} onCancel={() => { setF(original); setSsn(null); setLicense(null) }} />

      <Section title="Patient data" actions={<HistoryButton patientId={chart.id} label="Show Amendments History"
        filter={(e) => ['demographics_updated', 'patient_registered', 'sensitive_identifier_viewed'].includes(e.action)} />}>
        <p className="font-body text-xs font-semibold uppercase tracking-[0.8px] text-muted">Patient information</p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-6">
          <SelectField label="Prefix" value={f.prefix} onChange={set('prefix')} options={PREFIXES} placeholder="—" />
          <TextField label="Last Name" required value={f.last_name} onChange={set('last_name')} className="xl:col-span-2" />
          <TextField label="First Name" required value={f.first_name} onChange={set('first_name')} className="xl:col-span-2" />
          <TextField label="Middle" value={f.middle_name} onChange={set('middle_name')} />
          <TextField label="Suffix" value={f.suffix} onChange={set('suffix')} placeholder="Jr., III" />
          <TextField label="Preferred Name" value={f.preferred_name} onChange={set('preferred_name')} className="xl:col-span-2" />
          <fieldset className="flex flex-col xl:col-span-3">
            <legend className="mb-1 font-body text-xs font-semibold text-muted">Test patient?<span className="text-error"> *</span></legend>
            <div className="flex h-10 items-center gap-5 font-body text-sm">
              {[['Yes', true], ['No', false]].map(([label, v]) => (
                <label key={label} className="flex cursor-pointer items-center gap-1.5">
                  <input type="radio" name="test-patient" className="accent-primary" checked={f.is_test_patient === v}
                    onChange={() => set('is_test_patient')(v)} />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <SelectField label="Marital Status" value={f.marital_status} onChange={set('marital_status')} options={MARITAL}
            placeholder="Unspecified" className="xl:col-span-2" />
          <TextField label="Previous Name" value={f.previous_name} onChange={set('previous_name')} className="xl:col-span-2" />
          <SelectField label="Sex" value={f.sex} onChange={set('sex')} options={[{ value: 'F', label: 'Female' }, { value: 'M', label: 'Male' }, { value: 'X', label: 'Other / X' }]} placeholder="—" className="xl:col-span-2" />
          <div className="xl:col-span-2">
            <ProtectedField label="Drivers License Number" field="drivers_license_number" patientId={chart.id}
              masked={chart.drivers_license_masked} value={license} onChange={setLicense} placeholder="Not on file" />
          </div>
          <SelectField label="State" value={f.drivers_license_state} onChange={set('drivers_license_state')} options={US_STATES} className="xl:col-span-2" />
          <div className="xl:col-span-2">
            <ProtectedField label="Social Security Number" field="ssn" patientId={chart.id}
              masked={chart.ssn_masked} value={ssn} onChange={setSsn} placeholder="###-##-####" />
          </div>
        </div>

        <p className="mt-2 border-t border-hairline pt-4 font-body text-xs font-semibold uppercase tracking-[0.8px] text-muted">Required for meaningful use</p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <TextField label="Date of Birth" required type="date" value={f.date_of_birth} onChange={set('date_of_birth')} />
        </div>
      </Section>

      <Section title="Contact">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <TextField label="Mobile Phone" value={f.phone} onChange={set('phone')} placeholder="(312) 555-0100" />
          <TextField label="Home Phone" value={f.home_phone} onChange={set('home_phone')} />
          <TextField label="Work Phone" value={f.work_phone} onChange={set('work_phone')} />
          <SelectField label="Preferred Contact" value={f.preferred_contact_method} onChange={set('preferred_contact_method')}
            options={[{ value: 'phone', label: 'Phone' }, { value: 'text', label: 'Text message' }, { value: 'email', label: 'Email' }, { value: 'mail', label: 'Mail' }]} />
          <TextField label="Email" type="email" value={f.email} onChange={set('email')} className="xl:col-span-2" />
          <TextField label="Address Line 1" value={f.address_line1} onChange={set('address_line1')} className="xl:col-span-2" />
          <TextField label="Address Line 2" value={f.address_line2} onChange={set('address_line2')} className="xl:col-span-2" />
          <TextField label="City" value={f.city} onChange={set('city')} />
          <SelectField label="State" value={f.state} onChange={set('state')} options={US_STATES} />
          <TextField label="ZIP" value={f.postal_code} onChange={set('postal_code')} />
        </div>
      </Section>
    </div>
  )
}
