import { useEffect, useRef, useSyncExternalStore } from 'react';

/** What changed for the signed-in user: pages reload it through the usual API calls. */
export type LiveEvent = {
  kind: 'notification' | 'chat' | 'file' | 'resync';
  chat?: string;
  file?: string;
};
type Handler = (event: LiveEvent) => void;

const KINDS: LiveEvent['kind'][] = ['notification', 'chat', 'file', 'resync'];
const handlers = new Set<Handler>();
const watchers = new Set<() => void>();
let source: EventSource | undefined;
let live = false;

function setLive(value: boolean) {
  if (live === value) return;
  live = value;
  watchers.forEach((watch) => watch());
}

function emit(event: LiveEvent) {
  handlers.forEach((handler) => handler(event));
}

/**
 * Opens the user's event stream (GET /api/v1/events). The browser reconnects by itself after a
 * dropped connection; every (re)connect is announced as a resync, so pages reload what they may
 * have missed. A rejected stream (the session ended) stays closed until the next sign-in.
 */
export function connectLiveEvents() {
  if (source || typeof EventSource === 'undefined') return;
  const stream = new EventSource('/api/v1/events');
  source = stream;
  stream.onopen = () => {
    setLive(true);
    emit({ kind: 'resync' });
  };
  stream.onerror = () => {
    setLive(false);
    if (stream.readyState === EventSource.CLOSED && source === stream) source = undefined;
  };
  for (const kind of KINDS)
    stream.addEventListener(kind, (message) => {
      try {
        emit(JSON.parse((message as MessageEvent<string>).data) as LiveEvent);
      } catch {
        emit({ kind: 'resync' });
      }
    });
}

export function disconnectLiveEvents() {
  source?.close();
  source = undefined;
  setLive(false);
}

/** Whether live events arrive now; polling slows down while they do. */
export function useLive(): boolean {
  return useSyncExternalStore(
    (watch) => {
      watchers.add(watch);
      return () => watchers.delete(watch);
    },
    () => live,
    () => false,
  );
}

/** Calls `handler` for every live event while the component is mounted. */
export function useLiveEvents(handler: Handler) {
  const latest = useRef(handler);
  useEffect(() => {
    latest.current = handler;
  });
  useEffect(() => {
    const forward: Handler = (event) => latest.current(event);
    handlers.add(forward);
    return () => {
      handlers.delete(forward);
    };
  }, []);
}
