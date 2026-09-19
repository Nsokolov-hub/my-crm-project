import { createContext, useContext, useEffect, useState } from 'react';
import { api, authenticate, clearSession, setCsrf } from '../lib/api';
import type { Session } from '../lib/types';
const Context = createContext<{
  session?: Session;
  loading: boolean;
  login: (email: string, password: string, otp?: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
}>({ loading: true, login: async () => {}, logout: async () => {}, can: () => false });
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session>();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    api<Session>('/auth/me')
      .then((value) => {
        if (active) {
          setCsrf(value.csrf_token);
          setSession(value);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false);
      });
    const expired = () => {
      clearSession();
      setSession(undefined);
    };
    window.addEventListener('session-expired', expired);
    return () => {
      active = false;
      window.removeEventListener('session-expired', expired);
    };
  }, []);
  async function login(email: string, password: string, otp?: string) {
    const next = await authenticate(email, password, otp);
    const full = await api<Session>('/auth/me').catch(() => next);
    setSession(full);
    setCsrf(full.csrf_token);
  }
  async function logout() {
    await api('/auth/logout', { method: 'POST' });
    clearSession();
    setSession(undefined);
  }
  function can(code: string) {
    const grants = session?.permissions || session?.user.permissions || {};
    return Boolean(grants[code] || grants['*']);
  }
  return (
    <Context.Provider value={{ session, loading, login, logout, can }}>{children}</Context.Provider>
  );
}
export const useAuth = () => useContext(Context);
