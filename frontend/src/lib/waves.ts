import type { Entity } from './types';

export function waveWeekParts(
  row: Entity,
  prefix: string,
): { week: number; year: number } | undefined {
  const week = Number(row[`${prefix}_week`]);
  const year = Number(row[`${prefix}_year`]);
  if (week > 0 && year > 0) return { week, year };
  const value = row[`${prefix}_date`];
  if (!value) return undefined;
  const day = new Date(String(value).slice(0, 10) + 'T00:00:00Z');
  if (!Number.isFinite(day.getTime())) return undefined;
  day.setUTCDate(day.getUTCDate() + 4 - (day.getUTCDay() || 7));
  const isoYear = day.getUTCFullYear();
  const start = new Date(Date.UTC(isoYear, 0, 1));
  return { week: Math.ceil(((day.getTime() - start.getTime()) / 86400000 + 1) / 7), year: isoYear };
}

export function waveWeekLabel(row: Entity, prefix: string): string {
  const parts = waveWeekParts(row, prefix);
  return parts ? `Неделя ${parts.week}, ${parts.year}` : '—';
}

export function waveWeekInitial(row: Entity): Record<string, number> {
  return Object.fromEntries(
    ['close', 'departure', 'arrival'].flatMap((prefix) => {
      const parts = waveWeekParts(row, prefix);
      return parts
        ? [
            [`${prefix}_week`, parts.week],
            [`${prefix}_year`, parts.year],
          ]
        : [];
    }),
  );
}
