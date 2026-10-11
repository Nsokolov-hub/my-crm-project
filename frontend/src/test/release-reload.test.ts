import { afterEach, describe, expect, it, vi } from 'vitest';
import { reloadOnMissingBuild } from '../lib/releaseReload';

describe('reload after a release removed the previous build', () => {
  afterEach(() => sessionStorage.clear());

  it('reloads once and not again within a minute', () => {
    const reload = vi.fn();
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, reload });
    const event = new Event('vite:preloadError', { cancelable: true });
    expect(reloadOnMissingBuild(event, 100_000)).toBe(true);
    expect(event.defaultPrevented).toBe(true);
    expect(reloadOnMissingBuild(new Event('vite:preloadError'), 130_000)).toBe(false);
    expect(reloadOnMissingBuild(new Event('vite:preloadError'), 170_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });
});
