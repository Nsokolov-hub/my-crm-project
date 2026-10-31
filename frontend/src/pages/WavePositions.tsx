import { Search } from 'lucide-react';
import { useState } from 'react';
import { Badge, DataTable, ErrorBox, Loading, TextLink } from '../components/ui';
import { date, decimal, metric } from '../lib/format';
import { useApi, useDebounced } from '../lib/hooks';
import type { Entity } from '../lib/types';

type Positions = {
  items: Entity[];
  total: number;
  statuses: Record<string, string>;
  waves: { id: string; number: string }[];
};

const tones: Record<string, string> = { order: 'green', proposal: 'purple', calculation: 'gray' };

/**
 * Business remark of 31 October 2026: the positions of the waves in a list — confirmed orders and positions of
 * calculations the buyer has not refused, with purchase, sale, expenses and profitability of each.
 */
export function WavePositions() {
  const [q, setQ] = useState('');
  const [wave, setWave] = useState('');
  const [status, setStatus] = useState('');
  const search = useDebounced(q);
  const params = new URLSearchParams();
  if (search.trim()) params.set('q', search.trim());
  if (wave) params.set('wave_id', wave);
  if (status) params.set('status', status);
  const list = useApi<Positions>(`/waves/positions?${params.toString()}`);
  const rows = list.data?.items || [];
  const money = (value: unknown) => (value == null ? '—' : metric(value));
  return (
    <div className="panel">
      <p className="muted">
        Подтверждённые заказы и позиции расчётов по открытым заявкам, по которым не было отказа.
        Закрытые заявки и волны из архива не показываются. Суммы — из сохранённого расчёта позиции,
        в рублях.
      </p>
      <div className="request-toolbar">
        <div className="search-input">
          <Search size={17} />
          <input
            aria-label="Поиск по позициям волн"
            placeholder="Наименование, артикул, клиент, заявка…"
            value={q}
            onChange={(event) => setQ(event.target.value)}
          />
        </div>
        <select aria-label="Волна" value={wave} onChange={(event) => setWave(event.target.value)}>
          <option value="">Все волны</option>
          {(list.data?.waves || []).map((row) => (
            <option key={row.id} value={row.id}>
              {row.number}
            </option>
          ))}
        </select>
        <select
          aria-label="Статус позиции"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="">Все статусы</option>
          {Object.entries(list.data?.statuses || {}).map(([value, title]) => (
            <option key={value} value={value}>
              {title}
            </option>
          ))}
        </select>
      </div>
      {list.loading && !list.data ? (
        <Loading />
      ) : list.error ? (
        <ErrorBox error={list.error} retry={list.refresh} />
      ) : (
        <>
          <p className="muted">Позиций: {rows.length}</p>
          <DataTable<Entity>
            rows={rows}
            columns={[
              {
                key: 'name',
                label: 'Наименование',
                sortable: false,
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
                render: (row) => String(row.wave_number || '—'),
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
