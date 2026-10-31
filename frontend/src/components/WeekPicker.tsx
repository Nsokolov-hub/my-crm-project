import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import {
  dayKind,
  isoDay,
  isoWeek,
  isoWeekMonday,
  knownYear,
  weekSummary,
} from '../lib/productionCalendar';

const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const KIND_TITLES = {
  holiday: 'Праздничный или перенесённый выходной',
  weekend: 'Выходной',
  short: 'Сокращённый предпраздничный день',
  work: 'Рабочий день',
};

/**
 * ISO week field with a month calendar: week numbers, weekends, holidays and short days of the production
 * calendar (business remark of 31 October 2026). Picking a day or a week number sets the week and its ISO year.
 */
export function WeekPicker({
  id,
  week,
  year,
  invalid,
  describedBy,
  onWeek,
  onPick,
}: {
  id: string;
  week: unknown;
  year: unknown;
  invalid?: boolean;
  describedBy?: string;
  onWeek: (value: string) => void;
  onPick: (week: number, year: number) => void;
}) {
  const chosenWeek = Number(week);
  const chosenYear = Number(year);
  const chosenMonday = isoWeekMonday(chosenYear, chosenWeek);
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(() => {
    const base = chosenMonday || new Date();
    return { year: base.getUTCFullYear(), month: base.getUTCMonth() };
  });
  const first = new Date(Date.UTC(month.year, month.month, 1));
  const start = new Date(first);
  start.setUTCDate(first.getUTCDate() - ((first.getUTCDay() || 7) - 1));
  const weeks: Date[][] = [];
  for (let row = 0; row < 6; row += 1) {
    const days = Array.from({ length: 7 }, (_, column) => {
      const day = new Date(start);
      day.setUTCDate(start.getUTCDate() + row * 7 + column);
      return day;
    });
    if (row > 0 && days[0].getUTCMonth() !== month.month) break;
    weeks.push(days);
  }
  const today = isoDay(
    new Date(Date.UTC(new Date().getFullYear(), new Date().getMonth(), new Date().getDate())),
  );
  const title = new Intl.DateTimeFormat('ru-RU', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  })
    .format(first)
    .replace(/\s?г\.$/, '');
  const summary = Number.isFinite(chosenYear) ? weekSummary(chosenYear, chosenWeek) : undefined;
  function shift(delta: number) {
    setMonth((current) => {
      const next = new Date(Date.UTC(current.year, current.month + delta, 1));
      return { year: next.getUTCFullYear(), month: next.getUTCMonth() };
    });
  }
  function pick(day: Date) {
    const parts = isoWeek(day);
    onPick(parts.week, parts.year);
    setOpen(false);
  }
  return (
    <div
      className="week-picker"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setOpen(false);
      }}
    >
      <div className="week-picker-input">
        <input
          id={id}
          type="number"
          min={1}
          max={53}
          inputMode="numeric"
          aria-invalid={invalid}
          aria-describedby={describedBy}
          value={String(week ?? '')}
          placeholder="Номер недели"
          onChange={(event) => onWeek(event.target.value)}
        />
        <button
          type="button"
          className="week-picker-toggle"
          aria-label="Выбрать неделю в календаре"
          aria-expanded={open}
          onClick={() => {
            if (!open && chosenMonday)
              setMonth({ year: chosenMonday.getUTCFullYear(), month: chosenMonday.getUTCMonth() });
            setOpen((value) => !value);
          }}
        >
          <CalendarDays size={18} />
        </button>
      </div>
      {summary && <small className="week-picker-summary">{summary}</small>}
      {open && (
        <div className="week-picker-popover" role="dialog" aria-label="Календарь недель">
          <header>
            <button type="button" aria-label="Предыдущий месяц" onClick={() => shift(-1)}>
              <ChevronLeft size={16} />
            </button>
            <strong>{title}</strong>
            <button type="button" aria-label="Следующий месяц" onClick={() => shift(1)}>
              <ChevronRight size={16} />
            </button>
          </header>
          <table>
            <thead>
              <tr>
                <th title="Номер недели ISO">Нед.</th>
                {WEEKDAYS.map((day) => (
                  <th key={day}>{day}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {weeks.map((days) => {
                const number = isoWeek(days[0]);
                const selected = number.week === chosenWeek && number.year === chosenYear;
                return (
                  <tr key={isoDay(days[0])} className={selected ? 'selected' : undefined}>
                    <th>
                      <button
                        type="button"
                        title={`Неделя ${number.week}, ${number.year}`}
                        onClick={() => pick(days[0])}
                      >
                        {number.week}
                      </button>
                    </th>
                    {days.map((day) => {
                      const kind = dayKind(day);
                      const outside = day.getUTCMonth() !== month.month;
                      return (
                        <td key={isoDay(day)}>
                          <button
                            type="button"
                            className={[
                              `day-${kind}`,
                              outside ? 'outside' : '',
                              isoDay(day) === today ? 'today' : '',
                            ]
                              .filter(Boolean)
                              .join(' ')}
                            title={`${new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', timeZone: 'UTC' }).format(day)} — ${KIND_TITLES[kind]}`}
                            onClick={() => pick(day)}
                          >
                            {day.getUTCDate()}
                            {kind === 'short' && <sup>*</sup>}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <footer>
            <span className="legend day-holiday">Праздник</span>
            <span className="legend day-weekend">Выходной</span>
            <span className="legend day-short">Сокращённый*</span>
            {!knownYear(month.year) && <span>Праздники этого года не заведены</span>}
          </footer>
        </div>
      )}
    </div>
  );
}
