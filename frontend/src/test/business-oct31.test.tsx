import { describe, expect, it } from 'vitest';
import { phoneError } from '../components/Form';
import { dayKind, isoWeek, isoWeekMonday, weekSummary } from '../lib/productionCalendar';

// Business remarks of 31 October 2026: phones checked in the forms, ISO weeks picked on the production calendar.
describe('business remarks of 31 October 2026', () => {
  it('accepts real phone numbers and refuses made-up ones', () => {
    for (const good of [
      '+7 (495) 123-45-67 доб. 12',
      '8 912 345 67 89',
      '+998 90 123 45 67',
      '+7 495 123-45-67, +7 912 345-67-89',
    ])
      expect(phoneError(good)).toBeUndefined();
    for (const bad of [
      'нет',
      '12345',
      '+9987546428',
      '+7 999 123',
      '1111111111',
      '+7 999 1234567+1',
    ])
      expect(phoneError(bad)).toBeTruthy();
  });

  it('knows ISO weeks, holidays and short days', () => {
    expect(isoWeek(new Date(Date.UTC(2026, 0, 1)))).toEqual({ week: 1, year: 2026 });
    expect(isoWeek(new Date(Date.UTC(2027, 0, 1)))).toEqual({ week: 53, year: 2026 });
    expect(isoWeekMonday(2026, 12)?.toISOString().slice(0, 10)).toBe('2026-03-16');
    expect(isoWeekMonday(2025, 53)).toBeUndefined();
    expect(dayKind(new Date(Date.UTC(2026, 2, 9)))).toBe('holiday');
    expect(dayKind(new Date(Date.UTC(2027, 1, 20)))).toBe('short');
    expect(dayKind(new Date(Date.UTC(2026, 2, 14)))).toBe('weekend');
    expect(weekSummary(2026, 2)).toBe('5–11 января 2026 · рабочих дней: 0');
    expect(weekSummary(2026, 12)).toBe('16–22 марта 2026 · рабочих дней: 5');
  });
});
