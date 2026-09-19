import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider, useAuth } from './Auth';
import { Layout } from './Layout';
import { Loading } from '../components/ui';
import { Login } from '../pages/Login';
import { Dashboard, Analytics } from '../pages/Dashboard';
import { Clients, Calls, Tasks } from '../pages/Clients';
import { Requests } from '../pages/Requests';
import { RequestDetail } from '../pages/RequestDetail';
import { Catalog } from '../pages/Catalog';
import { Waves } from '../pages/Fulfillment';
import { Chats, Notifications } from '../pages/Communication';
import { Registry } from '../pages/Registry';
import { Settings, Guide } from '../pages/Settings';

function AuthenticatedApp() {
  const auth = useAuth();
  if (auth.loading) return <Loading />;
  if (!auth.session) return <Login />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="clients" element={<Clients />} />
        <Route path="calls" element={<Calls />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="requests" element={<Requests />} />
        <Route path="requests/:id" element={<RequestDetail />} />
        <Route path="catalog" element={<Catalog />} />
        <Route path="documents" element={<Registry kind="documents" />} />
        <Route path="payments" element={<Registry kind="payments" />} />
        <Route path="approvals" element={<Registry kind="approvals" />} />
        <Route path="waves" element={<Waves />} />
        <Route path="analytics" element={<Analytics />} />
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
