const KEY = 'crm-release-reload-at';
const PAUSE_MS = 60_000;

/**
 * After a release the files of the previous build are gone, so an open tab fails to load a section
 * it has not visited yet. Reload once to pick up the new build; a minute must pass before the next
 * reload, so a broken deployment does not reload the page in a loop.
 */
export function reloadOnMissingBuild(event: Event, now = Date.now()): boolean {
  try {
    const last = Number(sessionStorage.getItem(KEY) || 0);
    if (now - last < PAUSE_MS) return false;
    sessionStorage.setItem(KEY, String(now));
  } catch {
    return false;
  }
  event.preventDefault();
  window.location.reload();
  return true;
}
