import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import * as apiModule from '../lib/api';

vi.mock('../lib/api', () => ({
  api: vi.fn(),
}));

describe('hooks/useApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('handles API errors correctly', async () => {
    const error = new Error('API Error');
    vi.mocked(apiModule.api).mockRejectedValueOnce(error);

    const { result } = renderHook(() => useApi('/test'));

    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBe(error);
    expect(result.current.data).toBeUndefined();
  });
});

describe('hooks/useCommand', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uses same idempotency key for repeated commands if arguments are the same', async () => {
    let callCount = 0;
    let providedKey = '';

    vi.mocked(apiModule.api).mockImplementation(async (_path, init: Parameters<typeof apiModule.api>[1]) => {
      callCount++;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      providedKey = (init as any)?.key || '';
      if (callCount === 1) throw new Error('Network error');
      return { success: true };
    });

    const { result } = renderHook(() => useCommand());

    // First run fails
    await act(async () => {
      try {
        await result.current.run('/test', { foo: 'bar' });
      } catch {
        // expected
      }
    });

    const firstKey = providedKey;
    expect(firstKey).toBeTruthy();
    expect(result.current.error).toBeDefined();

    // Second run succeeds with same key
    await act(async () => {
      const res = await result.current.run('/test', { foo: 'bar' });
      expect(res).toEqual({ success: true });
    });

    expect(providedKey).toBe(firstKey);
  });
});

describe('hooks/useDirtyProtection', () => {
  it('adds and removes beforeunload event listener for draft saving', () => {
    const addEventListenerSpy = vi.spyOn(window, 'addEventListener');
    const removeEventListenerSpy = vi.spyOn(window, 'removeEventListener');

    const { rerender, unmount } = renderHook(({ dirty }) => useDirtyProtection(dirty), {
      initialProps: { dirty: false }
    });

    expect(addEventListenerSpy).not.toHaveBeenCalledWith('beforeunload', expect.any(Function));

    rerender({ dirty: true });
    expect(addEventListenerSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function));

    unmount();
    expect(removeEventListenerSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function));
  });
});
