/**
 * Production calendar of Russia for the week picker of waves (business remark of 31 October 2026).
 *
 * Non-working holidays and transferred days off by Article 112 of the Labour Code and the decrees on transfers
 * of days off; short pre-holiday days; Saturdays made working days. 2026 — the decree of 2025 (Jan 3 → Jan 9,
 * Jan 4 → Dec 31); 2027 — the decree of 17.09.2026 No. 1187 (Jan 2 → Nov 5, Jan 3 → Dec 31, Feb 20 → Feb 22).
 * Years beyond the list show weekends only.
 */
const HOLIDAYS: Record<number, string[]> = {
  2026: [
    '2026-01-01',
    '2026-01-02',
    '2026-01-03',
    '2026-01-04',
    '2026-01-05',
    '2026-01-06',
    '2026-01-07',
    '2026-01-08',
    '2026-01-09',
    '2026-01-10',
    '2026-01-11',
    '2026-02-23',
    '2026-03-09',
    '2026-05-01',
    '2026-05-11',
    '2026-06-12',
    '2026-11-04',
    '2026-12-31',
  ],
  2027: [
    '2027-01-01',
    '2027-01-02',
    '2027-01-03',
    '2027-01-04',
    '2027-01-05',
    '2027-01-06',
    '2027-01-07',
    '2027-01-08',
    '2027-01-09',
    '2027-01-10',
    '2027-02-22',
    '2027-02-23',
    '2027-03-08',
    '2027-05-03',
    '2027-05-10',
    '2027-06-14',
    '2027-11-04',
    '2027-11-05',
    '2027-12-31',
  ],
};
const SHORT_DAYS: Record<number, string[]> = {
  2026: ['2026-04-30', '2026-05-08', '2026-06-11', '2026-11-03'],
  2027: ['2027-02-20', '2027-04-30', '2027-06-11', '2027-11-03'],
};
const WORKING_WEEKENDS: Record<number, string[]> = {
  2027: ['2027-02-20'],
};

const holidays = new Set(Object.values(HOLIDAYS).flat());
const shortDays = new Set(Object.values(SHORT_DAYS).flat());
const workingWeekends = new Set(Object.values(WORKING_WEEKENDS).flat());

export type DayKind = 'work' | 'short' | 'weekend' | 'holiday';

/** yyyy-mm-dd of a UTC date. */
export function isoDay(day: Date): string {
  return day.toISOString().slice(0, 10);
}

export function knownYear(year: number): boolean {
  return year in HOLIDAYS;
}

export function dayKind(day: Date): DayKind {
  const key = isoDay(day);
  if (holidays.has(key)) return 'holiday';
  if (shortDays.has(key)) return 'short';
  if (workingWeekends.has(key)) return 'work';
  const weekday = day.getUTCDay();
  return weekday === 0 || weekday === 6 ? 'weekend' : 'work';
}

/** ISO week and its year of a UTC date. */
export function isoWeek(day: Date): { week: number; year: number } {
  const thursday = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate()));
  thursday.setUTCDate(thursday.getUTCDate() + 4 - (thursday.getUTCDay() || 7));
  const year = thursday.getUTCFullYear();
  const start = new Date(Date.UTC(year, 0, 1));
  return { week: Math.ceil(((thursday.getTime() - start.getTime()) / 86400000 + 1) / 7), year };
}

/** Monday of an ISO week (UTC), or undefined when the ISO year has no such week. */
export function isoWeekMonday(year: number, week: number): Date | undefined {
  if (!Number.isInteger(year) || !Number.isInteger(week) || week < 1 || week > 53) return undefined;
  const january4 = new Date(Date.UTC(year, 0, 4));
  const monday = new Date(january4);
  monday.setUTCDate(january4.getUTCDate() - ((january4.getUTCDay() || 7) - 1) + (week - 1) * 7);
  const check = isoWeek(monday);
  return check.week === week && check.year === year ? monday : undefined;
}

/** «16–22 марта 2026» for an ISO week; working days of the week by the production calendar. */
export function weekSummary(year: number, week: number): string | undefined {
  const monday = isoWeekMonday(year, week);
  if (!monday) return undefined;
  const sunday = new Date(monday);
  sunday.setUTCDate(monday.getUTCDate() + 6);
  const format = (day: Date, options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat('ru-RU', { ...options, timeZone: 'UTC' }).format(day);
  const sameMonth = monday.getUTCMonth() === sunday.getUTCMonth();
  const range = sameMonth
    ? `${monday.getUTCDate()}–${format(sunday, { day: 'numeric', month: 'long', year: 'numeric' })}`
    : `${format(monday, { day: 'numeric', month: 'long' })} – ${format(sunday, { day: 'numeric', month: 'long', year: 'numeric' })}`;
  let working = 0;
  for (let index = 0; index < 7; index += 1) {
    const day = new Date(monday);
    day.setUTCDate(monday.getUTCDate() + index);
    const kind = dayKind(day);
    if (kind === 'work' || kind === 'short') working += 1;
  }
  const known = knownYear(monday.getUTCFullYear()) && knownYear(sunday.getUTCFullYear());
  return `${range.replace(/\s?г\.$/, '')} · рабочих дней: ${working}${known ? '' : ' (без праздников)'}`;
}
