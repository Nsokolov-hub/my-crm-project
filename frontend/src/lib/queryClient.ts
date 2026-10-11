import { notifyManager, QueryClient } from '@tanstack/react-query';

// Deliver results as soon as the request resolves, like the fetch promise did before.
notifyManager.setScheduler((callback) => queueMicrotask(callback));

/**
 * Cache of the server data read with useApi. Reads are not retried and not refreshed on window focus,
 * as before; every completed request yields a new object (no structural sharing), so effects that
 * reset a form from the data run after each refresh.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, structuralSharing: false },
  },
});
