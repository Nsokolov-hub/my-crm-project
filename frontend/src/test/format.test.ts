import { describe, it, expect } from 'vitest';
import { date } from '../lib/format';

describe('format/date', () => {
  it('formats valid date strings in Europe/Moscow timezone', () => {
    // 2026-09-21 12:00:00 UTC = 15:00:00 MSK
    const utcDate = '2026-09-21T12:00:00Z';
    expect(date(utcDate)).toMatch(/21 сент\. 2026/); // '21 сент. 2026 г.' or similar depending on Node version
    expect(date(utcDate, true)).toMatch(/15:00/);
  });

  it('handles invalid dates gracefully by returning the string', () => {
    expect(date('invalid-date')).toBe('invalid-date');
  });

  it('handles null/undefined', () => {
    expect(date(null)).toBe('—');
    expect(date(undefined)).toBe('—');
  });
});
