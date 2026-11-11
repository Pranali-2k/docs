import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { PortalShell } from '@/components/layout/PortalShell'
import { Spinner } from '@/components/ui/Page'
import { useAuth } from '@/lib/auth'

import Login from '@/pages/Login'
import ForgotPassword from '@/pages/ForgotPassword'
import ResetPassword from '@/pages/ResetPassword'
import Dashboard from '@/pages/Dashboard'
import Requests from '@/pages/Requests'
import RequestDetail from '@/pages/RequestDetail'
import Patients from '@/pages/Patients'
import RegisterPatient from '@/pages/RegisterPatient'
import ChartLayout from '@/pages/chart/ChartLayout'
import ChartHome from '@/pages/chart/Home'
import ContactInfo from '@/pages/chart/ContactInfo'
import Insurance from '@/pages/chart/Insurance'
import PolicyEdit from '@/pages/chart/PolicyEdit'
import { Appointments, ChartHistory, ChartRequests, Guarantor, Identity, PhiReleases } from '@/pages/chart/Sections'
import { ChartKind, Clipboard, LinkedAccounts } from '@/pages/chart/Extras'
import Intramail from '@/pages/Intramail'
import ClinicalSettings from '@/pages/ClinicalSettings'
import Schedule from '@/pages/Schedule'
import EmailLog from '@/pages/EmailLog'
import AuditTrail from '@/pages/AuditTrail'
import DemoLab from '@/pages/DemoLab'
import Settings from '@/pages/Settings'
import Integrations from '@/pages/Integrations'

/** Each navigation starts at the top of the new screen. */
function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [pathname])
  return null
}

function RequireAuth({ children }) {
  const { user, ready } = useAuth()
  const location = useLocation()
  if (!ready) return <Spinner className="min-h-screen items-center" />
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />
  return children
}

export default function App() {
  return (
    <>
      <ScrollToTop />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route
          element={
            <RequireAuth>
              <PortalShell />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="requests" element={<Requests />} />
          <Route path="requests/:id" element={<RequestDetail />} />
          <Route path="patients" element={<Patients />} />
          <Route path="patients/new" element={<RegisterPatient />} />
          <Route path="patients/:id" element={<ChartLayout />}>
            <Route index element={<ChartHome />} />
            <Route path="contact" element={<ContactInfo />} />
            <Route path="guarantor" element={<Guarantor />} />
            <Route path="identity" element={<Identity />} />
            <Route path="appointments" element={<Appointments />} />
            <Route path="phi" element={<PhiReleases />} />
            <Route path="insurance" element={<Insurance />} />
            <Route path="insurance/:policyId" element={<PolicyEdit />} />
            <Route path="requests" element={<ChartRequests />} />
            <Route path="history" element={<ChartHistory />} />
            <Route path="clipboard" element={<Clipboard />} />
            <Route path="links" element={<LinkedAccounts />} />
            {['alerts', 'allergies', 'notes', 'reminders', 'flags', 'tasks', 'medications', 'orders', 'procedures', 'consents'].map((k) => (
              <Route key={k} path={k} element={<ChartKind kind={k} />} />
            ))}
          </Route>
          <Route path="intramail" element={<Intramail />} />
          <Route path="schedule" element={<Schedule />} />
          <Route path="emails" element={<EmailLog />} />
          <Route path="audit" element={<AuditTrail />} />
          <Route path="demo" element={<DemoLab />} />
          <Route path="settings" element={<Settings />} />
          <Route path="settings/integrations" element={<Integrations />} />
          <Route path="settings/clinical" element={<ClinicalSettings />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </>
  )
}
