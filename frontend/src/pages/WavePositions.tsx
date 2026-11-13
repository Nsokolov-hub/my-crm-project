import { ArrowUpRight, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { SearchSelect } from '../components/SearchSelect';
import { Badge, Button, DataTable, ErrorBox, Loading, TextLink } from '../components/ui';
import { api } from '../lib/api';
import { date, decimal, metric } from '../lib/format';
import { useApi, useCommand, useDebounced } from '../lib/hooks';
import type { Entity } from '../lib/types';

type Positions = {
  items: Entity[];
  total: number;
  statuses: Record<string, string>;
  waves: { id: string; number: string }[];
  suppliers?: string[];
  manufacturers?: string[];
};

/** Business remark of 11 November 2026: the filters of the wave positions are kept for the employee. */
const PREFERENCE = '/me/preferences/wave-positions.filters';
type Filters = {
  q: string;
  wave: string;
  status: string;
  supplier: string;
  manufacturer: string;
  sort: string;
  direction: string;
};
const EMPTY: Filters = {
  q: '',
  wave: '',
  status: '',
  supplier: '',
  manufacturer: '',
  sort: 'calculated_at',
  direction: 'desc',
};
const NUMERIC = new Set([
  'quantity',
  'delivery_days',
  'purchase',
  'sale_unit_price',
  'expenses',
  'profitability_percent',
]);

const tones: Record<string, string> = { order: 'green', proposal: 'purple', calculation: 'gray' };

function sortValue(row: Entity, key: string): string | number {
  const value = row[key];
  if (NUMERIC.has(key))
    return value == null || value === '' ? Number.NEGATIVE_INFINITY : Number(value);
  if (key === 'request_number') return Number(value) || 0;
  return String(value ?? '').toLocaleLowerCase('ru');
}

/**
 * Business remark of 31 October 2026: the positions of the waves in a list — confirmed orders and positions of
 * calculations the buyer has not refused, with purchase, sale, expenses and profitability of each. Remarks of
 * 11 November 2026: filters by supplier and manufacturer, the term of the supplier's quote, the wave opens from
 * its position, columns sort and the filters can be saved.
 */
export function WavePositions({ onOpenWave }: { onOpenWave?: (id: string) => void }) {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [saved, setSaved] = useState<Filters | null>(null);
  const [notice, setNotice] = useState('');
  const command = useCommand();
  const search = useDebounced(filters.q);
  useEffect(() => {
    let active = true;
    api<{ value: Record<string, unknown> | null }>(PREFERENCE)
      .then((result) => {
        if (!active || !result.value) return;
        const value = Object.fromEntries(
          Object.entries(EMPTY).map(([key, fallback]) => [
            key,
            typeof result.value?.[key] === 'string' ? result.value[key] : fallback,
          ]),
        ) as Filters;
        setSaved(value);
        setFilters(value);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);
  const params = new URLSearchParams();
  if (search.trim()) params.set('q', search.trim());
  if (filters.wave) params.set('wave_id', filters.wave);
  if (filters.status) params.set('status', filters.status);
  if (filters.supplier) params.set('supplier', filters.supplier);
  if (filters.manufacturer) params.set('manufacturer', filters.manufacturer);
  const list = useApi<Positions>(`/waves/positions?${params.toString()}`);
  const rows = useMemo(() => {
    const sign = filters.direction === 'desc' ? -1 : 1;
    return [...(list.data?.items || [])].sort((a, b) => {
      const left = sortValue(a, filters.sort);
      const right = sortValue(b, filters.sort);
      return left < right ? -sign : left > right ? sign : 0;
    });
  }, [list.data, filters.sort, filters.direction]);
  const money = (value: unknown) => (value == null ? '—' : metric(value));
  function change(patch: Partial<Filters>) {
    setFilters((current) => ({ ...current, ...patch }));
    setNotice('');
  }
  const changed = JSON.stringify(filters) !== JSON.stringify(saved || EMPTY);
  async function save() {
    try {
      await command.run(PREFERENCE, { value: filters }, 'PUT');
      setSaved(filters);
      setNotice('Фильтры сохранены: позиции волн будут открываться с ними.');
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
  const filtered =
    filters.q || filters.wave || filters.status || filters.supplier || filters.manufacturer;
  const waveName = list.data?.waves.find((row) => row.id === filters.wave)?.number;
  return (
    <div className="panel">
      <p className="muted">
        Подтверждённые заказы и позиции расчётов по открытым заявкам, по которым не было отказа.
        Закрытые заявки и волны из архива не показываются. Суммы — из сохранённого расчёта позиции,
        в рублях. Нажмите на волну в строке, чтобы оставить только её позиции.
      </p>
      <div className="request-toolbar wave-positions-filters">
        <div className="search-input">
          <Search size={17} />
          <input
            aria-label="Поиск по позициям волн"
            placeholder="Наименование, артикул, клиент, заявка…"
            value={filters.q}
            onChange={(event) => change({ q: event.target.value })}
          />
        </div>
        <select
          aria-label="Волна"
          value={filters.wave}
          onChange={(event) => change({ wave: event.target.value })}
        >
          <option value="">Все волны</option>
          {(list.data?.waves || []).map((row) => (
            <option key={row.id} value={row.id}>
              {row.number}
            </option>
          ))}
        </select>
        <SearchSelect
          label="Фильтр: поставщик"
          allLabel="Все поставщики"
          value={filters.supplier}
          options={list.data?.suppliers || []}
          onChange={(supplier) => change({ supplier })}
        />
        <SearchSelect
          label="Фильтр: производитель"
          allLabel="Все производители"
          value={filters.manufacturer}
          options={list.data?.manufacturers || []}
          onChange={(manufacturer) => change({ manufacturer })}
        />
        <select
          aria-label="Статус позиции"
          value={filters.status}
          onChange={(event) => change({ status: event.target.value })}
        >
          <option value="">Все статусы</option>
          {Object.entries(list.data?.statuses || {}).map(([value, title]) => (
            <option key={value} value={value}>
              {title}
            </option>
          ))}
        </select>
      </div>
      <div className="wave-positions-actions">
        {filters.wave && onOpenWave && (
          <Button variant="secondary" onClick={() => onOpenWave(filters.wave)}>
            Открыть волну{waveName ? ` «${waveName}»` : ''}
          </Button>
        )}
        {filtered && (
          <button
            type="button"
            className="chip-button"
            onClick={() => change({ q: '', wave: '', status: '', supplier: '', manufacturer: '' })}
          >
            Сбросить фильтры
          </button>
        )}
        <span className="filter-spacer" />
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
      </div>
      {(notice || (saved && !changed)) && (
        <p className="filter-notice">{notice || 'Позиции открыты с сохранёнными фильтрами.'}</p>
      )}
      <ErrorBox error={command.error} />
      {list.loading && !list.data ? (
        <Loading />
      ) : list.error ? (
        <ErrorBox error={list.error} retry={list.refresh} />
      ) : (
        <>
          <p className="muted">Позиций: {rows.length}</p>
          <DataTable<Entity>
            rows={rows}
            sort={filters.sort}
            direction={filters.direction}
            onSort={(key) =>
              change(
                key === filters.sort
                  ? { direction: filters.direction === 'asc' ? 'desc' : 'asc' }
                  : { sort: key, direction: 'asc' },
              )
            }
            empty={<p className="muted form-body">Под эти фильтры позиций нет.</p>}
            columns={[
              {
                key: 'name',
                label: 'Наименование',
                render: (row) => (
                  <span className="stacked">
                    <strong>{String(row.name || '—')}</strong>
                    <small>
                      {[
                        row.article ? `Арт. ${String(row.article)}` : '',
                        row.packaging ? `Фасовка: ${String(row.packaging)}` : '',
                        row.manufacturer ? String(row.manufacturer) : '',
                        row.cas ? `CAS ${String(row.cas)}` : '',
                      ]
                        .filter(Boolean)
                        .join(' · ') || '—'}
                    </small>
                  </span>
                ),
              },
              {
                key: 'client_name',
                label: 'Клиент',
                render: (row) => String(row.client_name || '—'),
              },
              {
                key: 'request_number',
                label: 'Заявка',
                render: (row) => (
                  <TextLink to={`/requests/${String(row.request_id)}`}>
                    №{String(row.request_number || '')}
                  </TextLink>
                ),
              },
              {
                key: 'wave_number',
                label: 'Волна',
                render: (row) =>
                  row.wave_id ? (
                    <span className="wave-cell">
                      <button
                        type="button"
                        className="link-button"
                        title="Показать только позиции этой волны"
                        onClick={() => change({ wave: String(row.wave_id) })}
                      >
                        {String(row.wave_number || '—')}
                      </button>
                      {onOpenWave && (
                        <button
                          type="button"
                          className="icon-button"
                          aria-label={`Открыть волну ${String(row.wave_number || '')}`}
                          title="Открыть волну"
                          onClick={() => onOpenWave(String(row.wave_id))}
                        >
                          <ArrowUpRight size={14} />
                        </button>
                      )}
                    </span>
                  ) : (
                    '—'
                  ),
              },
              {
                key: 'supplier_name',
                label: 'Поставщик',
                render: (row) => String(row.supplier_name || '—'),
              },
              {
                key: 'delivery_days',
                label: 'Срок синтеза по квоте, дн.',
                render: (row) => (row.delivery_days == null ? '—' : String(row.delivery_days)),
              },
              {
                key: 'calculated_at',
                label: 'Дата расчёта',
                render: (row) => (
                  <span className="stacked">
                    <span>{date(row.calculated_at, true)}</span>
                    {Boolean(row.calculation_number) && (
                      <small>Расчёт {String(row.calculation_number)}</small>
                    )}
                  </span>
                ),
              },
              { key: 'quantity', label: 'Кол-во, шт.', render: (row) => decimal(row.quantity) },
              { key: 'purchase', label: 'Закуп, ₽', render: (row) => money(row.purchase) },
              {
                key: 'sale_unit_price',
                label: 'Цена продажи за шт., ₽',
                render: (row) => (
                  <span className="stacked">
                    <span>{money(row.sale_unit_price)}</span>
                    {row.sale_total != null && <small>Сумма {money(row.sale_total)}</small>}
                  </span>
                ),
              },
              {
                key: 'expenses',
                label: 'Расход по позиции, ₽',
                render: (row) => money(row.expenses),
              },
              {
                key: 'profitability_percent',
                label: 'Рентабельность, %',
                render: (row) => money(row.profitability_percent),
              },
              {
                key: 'status',
                label: 'Статус',
                render: (row) => (
                  <span className="stacked">
                    <Badge value={row.status_label} tone={tones[String(row.status)] || 'gray'} />
                    {Boolean(row.proposal_number) && (
                      <small>КП №{String(row.proposal_number)}</small>
                    )}
                  </span>
                ),
              },
            ]}
          />
        </>
      )}
    </div>
  );
}
