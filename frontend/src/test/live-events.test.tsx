import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { usePolling } from '../lib/hooks';
import {
  connectLiveEvents,
  disconnectLiveEvents,
  useLive,
  useLiveEvents,
  type LiveEvent,
} from '../lib/liveEvents';

class FakeEventSource {
  static CLOSED = 2;
  static last: FakeEventSource | undefined;
  readyState = 0;
  onopen?: () => void;
  onerror?: () => void;
  listeners = new Map<string, (message: MessageEvent<string>) => void>();
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(kind: string, listener: (message: MessageEvent<string>) => void) {
    this.listeners.set(kind, listener);
  }
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
  send(kind: string, data: object) {
    this.listeners.get(kind)?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  vi.stubGlobal('EventSource', FakeEventSource);
  setVisibility('visible');
});
afterEach(() => {
  disconnectLiveEvents();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('live events', () => {
  it('announce a resync on connect, pass events on and report the connection', () => {
    const received: LiveEvent[] = [];
    const { result } = renderHook(() => {
      useLiveEvents((event) => received.push(event));
      return useLive();
    });
    connectLiveEvents();
    const stream = FakeEventSource.last!;
    expect(stream.url).toBe('/api/v1/events');
    act(() => stream.onopen?.());
    expect(result.current).toBe(true);
    act(() => stream.send('chat', { kind: 'chat', chat: 'c1' }));
    expect(received).toEqual([{ kind: 'resync' }, { kind: 'chat', chat: 'c1' }]);
    act(() => {
      stream.close();
      stream.onerror?.();
    });
    expect(result.current).toBe(false);
  });
});

describe('polling', () => {
  it('pauses in a hidden tab, refreshes when it is shown and slows down while live', () => {
    vi.useFakeTimers();
    const refresh = vi.fn();
    renderHook(() => usePolling(refresh, 1000));
    vi.advanceTimersByTime(3000);
    expect(refresh).toHaveBeenCalledTimes(3);

    act(() => setVisibility('hidden'));
    vi.advanceTimersByTime(5000);
    expect(refresh).toHaveBeenCalledTimes(3);
    act(() => setVisibility('visible'));
    expect(refresh).toHaveBeenCalledTimes(4);

    connectLiveEvents();
    act(() => FakeEventSource.last!.onopen?.());
    vi.advanceTimersByTime(5999);
    expect(refresh).toHaveBeenCalledTimes(4);
    vi.advanceTimersByTime(1);
    expect(refresh).toHaveBeenCalledTimes(5);
  });

  it('does nothing without an interval', () => {
    vi.useFakeTimers();
    const refresh = vi.fn();
    renderHook(() => usePolling(refresh, null));
    vi.advanceTimersByTime(60000);
    expect(refresh).not.toHaveBeenCalled();
  });
});
