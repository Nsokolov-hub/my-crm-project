import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(Boolean(path));
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!path) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(undefined);
    setData(undefined);
    api<T>(path, { signal: controller.signal })
      .then(setData)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, revision]);
  const refresh = useCallback(() => setRevision((v) => v + 1), []);
  return { data, error, loading, refresh };
}
export function useCommand() {
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const operation = useRef<{ signature: string; key: string } | undefined>(undefined);
  async function run<T>(
    path: string,
    body: Record<string, unknown> = {},
    method = 'POST',
    bodyKey = false,
  ): Promise<T | undefined> {
    if (pending.current) return undefined;
    const signature = JSON.stringify({ path, body, method });
    if (operation.current?.signature !== signature)
      operation.current = { signature, key: crypto.randomUUID() };
    const key = operation.current.key;
    pending.current = true;
    setBusy(true);
    setError(undefined);
    try {
      const result = await api<T>(path, {
        method,
        body: bodyKey ? { ...body, idempotency_key: key } : body,
        key,
      });
      operation.current = undefined;
      return result;
    } catch (e) {
      setError(e);
      throw e;
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return { run, busy, error, setError };
}
export function useDebounced<T>(value: T, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}
export function useDirtyProtection(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);
}
