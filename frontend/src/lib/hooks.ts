import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { queryClient } from './queryClient';
import { createRequestKey } from './requestKey';
/**
 * Server data at `path` (null: nothing to load). The data stays on screen while `refresh` reloads it,
 * and a page visited again shows the cached data at once while it is reloaded.
 */
export function useApi<T>(path: string | null) {
  const query = useQuery(
    {
      queryKey: ['api', path],
      queryFn: ({ signal }) => api<T>(path as string, { signal }),
      enabled: Boolean(path),
    },
    queryClient,
  );
  const { refetch } = query;
  const refresh = useCallback(() => void refetch(), [refetch]);
  return {
    data: query.data,
    error: (query.error ?? undefined) as unknown,
    loading: Boolean(path) && query.isPending,
    refresh,
  };
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
    pending.current = true;
    setBusy(true);
    setError(undefined);
    try {
      const signature = JSON.stringify({ path, body, method });
      if (operation.current?.signature !== signature)
        operation.current = { signature, key: createRequestKey() };
      const key = operation.current.key;
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
