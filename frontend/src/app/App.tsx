import { lazy } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider, useAuth } from './Auth';
import { Layout } from './Layout';
import { Loading } from '../components/ui';
import { Login } from '../pages/Login';

// Sections load on their first visit, so signing in does not download the whole application.
const Dashboard = lazy(() => import('../pages/Dashboard').then((m) => ({ default: m.Dashboard })));
const Analytics = lazy(() => import('../pages/Analytics').then((m) => ({ default: m.Analytics })));
const AnalyticsReport = lazy(() =>
  import('../pages/Analytics').then((m) => ({ default: m.AnalyticsReport })),
);
const Clients = lazy(() => import('../pages/Clients').then((m) => ({ default: m.Clients })));
const Calls = lazy(() => import('../pages/Clients').then((m) => ({ default: m.Calls })));
const Tasks = lazy(() => import('../pages/Tasks').then((m) => ({ default: m.Tasks })));
const Contacts = lazy(() => import('../pages/Contacts').then((m) => ({ default: m.Contacts })));
const Requests = lazy(() => import('../pages/Requests').then((m) => ({ default: m.Requests })));
const RequestDetail = lazy(() =>
  import('../pages/RequestDetail').then((m) => ({ default: m.RequestDetail })),
);
const Catalog = lazy(() => import('../pages/Catalog').then((m) => ({ default: m.Catalog })));
const SiteRequests = lazy(() =>
  import('../pages/SiteRequests').then((m) => ({ default: m.SiteRequests })),
);
const Waves = lazy(() => import('../pages/Fulfillment').then((m) => ({ default: m.Waves })));
const Chats = lazy(() => import('../pages/Communication').then((m) => ({ default: m.Chats })));
const Notifications = lazy(() =>
  import('../pages/Communication').then((m) => ({ default: m.Notifications })),
);
const Registry = lazy(() => import('../pages/Registry').then((m) => ({ default: m.Registry })));
const PaymentCalendar = lazy(() =>
  import('../pages/Business').then((m) => ({ default: m.PaymentCalendar })),
);
const SupplierOrders = lazy(() =>
  import('../pages/Business').then((m) => ({ default: m.SupplierOrders })),
);
const EmployeeAbsences = lazy(() =>
  import('../pages/Business').then((m) => ({ default: m.EmployeeAbsences })),
);
const Settings = lazy(() => import('../pages/Settings').then((m) => ({ default: m.Settings })));
const Guide = lazy(() => import('../pages/Settings').then((m) => ({ default: m.Guide })));

function AuthenticatedApp() {
  const auth = useAuth();
  if (auth.loading) return <Loading />;
  if (!auth.session) return <Login />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="clients" element={<Clients />} />
        <Route path="contacts" element={<Contacts />} />
        <Route path="calls" element={<Calls />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="requests" element={<Requests />} />
        <Route path="requests/:id" element={<RequestDetail />} />
        <Route path="site-requests" element={<SiteRequests />} />
        <Route path="catalog" element={<Catalog />} />
        <Route path="documents" element={<Registry kind="documents" />} />
        <Route path="payments" element={<Registry kind="payments" />} />
        <Route path="approvals" element={<Registry kind="approvals" />} />
        <Route path="payment-calendar" element={<PaymentCalendar />} />
        <Route path="supplier-orders" element={<SupplierOrders />} />
        <Route path="employee-absences" element={<EmployeeAbsences />} />
        <Route path="waves" element={<Waves />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="analytics/:report" element={<AnalyticsReport />} />
        <Route path="chats" element={<Chats />} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="settings" element={<Settings />} />
        <Route path="guide" element={<Guide />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AuthenticatedApp />
      </AuthProvider>
    </BrowserRouter>
  );
}
