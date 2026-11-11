import { Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Badge, Button, DataTable, ErrorBox, Loading, Section } from '../components/ui';
import { api } from '../lib/api';
import { useApi, useCommand } from '../lib/hooks';
import type { Column, Entity } from '../lib/types';
import { waveKindLabel, waveWeekLabel } from '../lib/waves';

/** Business remark of 11 November 2026: the filters of the waves table are kept for the employee. */
const PREFERENCE = '/me/preferences/waves.filters';

export const WAVE_STATUSES: [string, string][] = [
  ['planned', 'Планируется'],
  ['assembling', 'Комплектуется'],
  ['closed', 'Закрыта для добавления'],
  ['shipped', 'Отправлена'],
  ['arrived', 'Прибыла'],
  ['completed', 'Завершена'],
  ['cancelled', 'Отменена'],
];
const STATUS_ORDER = Object.fromEntries(WAVE_STATUSES.map(([value], index) => [value, index]));
const PERIODS: [string, string][] = [
  ['arrival', 'Прибытие'],
  ['departure', 'Отправка'],
  ['close', 'Приём до'],
];

type Filters = {
  q: string;
  supplier: string;
  status: string;
  kind: string;
  period: string;
  from: string;
  to: string;
  sort: string;
  direction: string;
};
const EMPTY: Filters = {
  q: '',
  supplier: '',
  status: '',
  kind: '',
  period: 'arrival',
  from: '',
  to: '',
  sort: 'arrival_date',
  direction: 'asc',
};

function fromSaved(value: Record<string, unknown>): Filters {
  return Object.fromEntries(
    Object.entries(EMPTY).map(([key, fallback]) => [
      key,
      typeof value[key] === 'string' ? (value[key] as string) : fallback,
    ]),
  ) as Filters;
}

/** Monday and Sunday of the ISO week of a wave date, as yyyy-mm-dd. */
function weekRange(value: unknown): [string, string] | undefined {
  if (!value) return undefined;
  const day = new Date(String(value).slice(0, 10) + 'T00:00:00Z');
  if (!Number.isFinite(day.getTime())) return undefined;
  const monday = new Date(day);
  monday.setUTCDate(day.getUTCDate() - ((day.getUTCDay() || 7) - 1));
  const sunday = new Date(monday);
  sunday.setUTCDate(monday.getUTCDate() + 6);
  return [monday.toISOString().slice(0, 10), sunday.toISOString().slice(0, 10)];
}

/** What a column is sorted by: dates rather than week labels, the order of the states rather than their names. */
function sortValue(row: Entity, key: string): string | number {
  if (key === 'status') return STATUS_ORDER[String(row.status)] ?? 99;
  if (key.endsWith('_date')) return String(row[key] || '');
  if (key === 'kind') return waveKindLabel(row);
  return String(row[key] ?? '').toLocaleLowerCase('ru');
}

/**
 * The waves as a table (business remarks of 11 November 2026): every column sorts and the list filters by
 * supplier, state, kind and the weeks of a date. The API returns every wave in use, so this happens here.
 */
export function WavesTable({
  archive = false,
  refreshKey = 0,
  action,
  onOpen,
}: {
  archive?: boolean;
  refreshKey?: number;
  action?: ReactNode;
  onOpen: (row: Entity) => void;
}) {
  const list = useApi<{ items: Entity[] }>(
    `/waves${archive ? '?archived=true&' : '?'}ui_revision=${refreshKey}`,
  );
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [saved, setSaved] = useState<Filters | null>(null);
  const [notice, setNotice] = useState('');
  const command = useCommand();
  useEffect(() => {
    if (archive) return;
    let active = true;
    api<{ value: Record<string, unknown> | null }>(PREFERENCE)
      .then((result) => {
        if (!active || !result.value) return;
        const value = fromSaved(result.value);
        setSaved(value);
        setFilters(value);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [archive]);
  const rows = useMemo(() => list.data?.items || [], [list.data]);
  const suppliers = useMemo(
    () =>
      [...new Set(rows.map((row) => String(row.supplier_name || '')).filter(Boolean))].sort(
        (a, b) => a.localeCompare(b, 'ru'),
      ),
    [rows],
  );
  const shown = useMemo(() => {
    const text = filters.q.trim().toLocaleLowerCase('ru');
    const filtered = rows.filter((row) => {
      if (filters.supplier && row.supplier_name !== filters.supplier) return false;
      if (filters.status && row.status !== filters.status) return false;
      if (filters.kind && row.kind !== filters.kind) return false;
      if (filters.from || filters.to) {
        const range = weekRange(row[`${filters.period}_date`]);
        if (!range) return false;
        if (filters.from && range[1] < filters.from) return false;
        if (filters.to && range[0] > filters.to) return false;
      }
      if (
        text &&
        ![row.number, row.supplier_name, row.route, row.plan_number].some((value) =>
          String(value || '')
            .toLocaleLowerCase('ru')
            .includes(text),
        )
      )
        return false;
      return true;
    });
    const sign = filters.direction === 'desc' ? -1 : 1;
    return [...filtered].sort((a, b) => {
      const left = sortValue(a, filters.sort);
      const right = sortValue(b, filters.sort);
      if (left < right) return -sign;
      if (left > right) return sign;
      return String(a.number || '').localeCompare(String(b.number || ''), 'ru');
    });
  }, [rows, filters]);
  function change(patch: Partial<Filters>) {
    setFilters((current) => ({ ...current, ...patch }));
    setNotice('');
  }
  const changed = JSON.stringify(filters) !== JSON.stringify(saved || EMPTY);
  async function save() {
    try {
      await command.run(PREFERENCE, { value: filters }, 'PUT');
      setSaved(filters);
      setNotice('Фильтры сохранены: таблица волн будет открываться с ними.');
    } catch {
      /* ErrorBox shows the failure. */
    }
  }
  async function forget() {
    try {
      await command.run(PREFERENCE, {}, 'DELETE');
      setSaved(null);
      setFilters(EMPTY);
      setNotice('Сохранённые фильтры удалены.');
    } catch {
      /* ErrorBox shows the failure. */
    }
  }
  const columns: Column[] = [
    { key: 'number', label: 'Волна' },
    {
      key: 'kind',
      label: 'Вид',
      render: (r) => (
        <Badge value={waveKindLabel(r)} tone={r.kind === 'plan' ? 'gray' : 'purple'} />
      ),
    },
    { key: 'supplier_name', label: 'Поставщик' },
    { key: 'route', label: 'Маршрут' },
    { key: 'status', label: 'Состояние', render: (r) => <Badge value={r.status} /> },
    { key: 'close_date', label: 'Приём до', render: (r) => waveWeekLabel(r, 'close') },
    { key: 'departure_date', label: 'Отправка', render: (r) => waveWeekLabel(r, 'departure') },
    { key: 'arrival_date', label: 'Прибытие', render: (r) => waveWeekLabel(r, 'arrival') },
  ];
  const filtered =
    filters.q || filters.supplier || filters.status || filters.kind || filters.from || filters.to;
  return (
    <Section
      title={archive ? 'Архив волн' : 'Расписание волн'}
      description={
        archive
          ? 'Волны из архива не предлагаются в заявках и расчётах. Их можно вернуть.'
          : undefined
      }
      action={action}
    >
      <div className="wave-table-filters">
        <div className="search-input">
          <Search size={17} />
          <input
            aria-label="Поиск волны"
            placeholder="Волна, поставщик, маршрут…"
            value={filters.q}
            onChange={(event) => change({ q: event.target.value })}
          />
        </div>
        <select
          aria-label="Фильтр: поставщик"
          value={filters.supplier}
          onChange={(event) => change({ supplier: event.target.value })}
        >
          <option value="">Все поставщики</option>
          {suppliers.map((name) => (
            <option key={name}>{name}</option>
          ))}
        </select>
        <select
          aria-label="Фильтр: состояние"
          value={filters.status}
          onChange={(event) => change({ status: event.target.value })}
        >
          <option value="">Все состояния</option>
          {WAVE_STATUSES.map(([value, title]) => (
            <option key={value} value={value}>
              {title}
            </option>
          ))}
        </select>
        <select
          aria-label="Фильтр: вид волны"
          value={filters.kind}
          onChange={(event) => change({ kind: event.target.value })}
        >
          <option value="">Плановые и рабочие</option>
          <option value="plan">Плановые</option>
          <option value="work">Рабочие</option>
        </select>
        <div className="wave-period">
          <select
            aria-label="Фильтр: срок"
            value={filters.period}
            onChange={(event) => change({ period: event.target.value })}
          >
            {PERIODS.map(([value, title]) => (
              <option key={value} value={value}>
                {title}
              </option>
            ))}
          </select>
          <label>
            с
            <input
              type="date"
              aria-label="Срок с"
              value={filters.from}
              onChange={(event) => change({ from: event.target.value })}
            />
          </label>
          <label>
            по
            <input
              type="date"
              aria-label="Срок по"
              value={filters.to}
              onChange={(event) => change({ to: event.target.value })}
            />
          </label>
        </div>
        {filtered && (
          <button
            type="button"
            className="chip-button"
            onClick={() => change({ q: '', supplier: '', status: '', kind: '', from: '', to: '' })}
          >
            Сбросить
          </button>
        )}
        {!archive && (
          <span className="wave-table-save">
            <Button
              variant="secondary"
              busy={command.busy}
              disabled={!changed && Boolean(saved)}
              onClick={() => void save()}
            >
              Сохранить фильтры
            </Button>
            {saved && (
              <Button variant="ghost" busy={command.busy} onClick={() => void forget()}>
                Удалить сохранённые
              </Button>
            )}
          </span>
        )}
      </div>
      {(notice || (saved && !changed)) && (
        <p className="filter-notice">{notice || 'Таблица открыта с сохранёнными фильтрами.'}</p>
      )}
      <ErrorBox error={command.error} />
      {list.loading && !list.data ? (
        <Loading />
      ) : list.error ? (
        <ErrorBox error={list.error} retry={list.refresh} />
      ) : (
        <>
          <p className="muted wave-table-count">
            Волн: {shown.length}
            {shown.length !== rows.length ? ` из ${rows.length}` : ''}
          </p>
          <DataTable
            rows={shown}
            columns={columns}
            onRow={onOpen}
            sort={filters.sort}
            direction={filters.direction}
            onSort={(key) =>
              change(
                key === filters.sort
                  ? { direction: filters.direction === 'asc' ? 'desc' : 'asc' }
                  : { sort: key, direction: 'asc' },
              )
            }
            empty={<p className="muted form-body">Под эти фильтры волн нет.</p>}
          />
        </>
      )}
    </Section>
  );
}
