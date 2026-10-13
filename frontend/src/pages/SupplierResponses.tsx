import { useState } from 'react';
import { RecordForm } from '../components/Form';
import { Badge, Button, DataTable, ErrorBox, Loading, Modal, Section } from '../components/ui';
import { date, decimal, nowLocal } from '../lib/format';
import { useApi } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';

type Status = 'priced' | 'declined' | 'waiting';
type Cell = {
  status: Status;
  requested_at: string;
  answered_at: string | null;
  hours: number | null;
};
type SupplierColumn = {
  supplier_id: string;
  name: string;
  asked_at: string;
  answered_at: string | null;
  hours: number | null;
  mail_id: string;
  mail_version: number;
  replied_at: string | null;
  reply_note: string;
};
type Matrix = {
  suppliers: SupplierColumn[];
  items: (Entity & {
    number: number;
    description: string;
    manufacturer_name: string | null;
    cells: Record<string, Cell>;
  })[];
};
type Brand = Entity & {
  manufacturer_id: string | null;
  name: string;
  positions: number;
  priced: number;
  declined: number;
  waiting: number;
};
type SupplierRow = Entity & {
  supplier_id: string;
  name: string;
  requests: number;
  answered_requests: number;
  answer_rate: number | null;
  average_hours: number | null;
  median_hours: number | null;
  positions: number;
  priced: number;
  declined: number;
  waiting: number;
  declined_brands: Brand[];
};

const STATUS: Record<Status, { title: string; tone: string }> = {
  priced: { title: 'Цена', tone: 'green' },
  declined: { title: 'Без цены', tone: 'red' },
  waiting: { title: 'Ждём ответа', tone: 'amber' },
};

/** Hours as people say them: «5 ч», «1,5 дн.». */
export function duration(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return '—';
  if (hours < 1) return 'меньше часа';
  if (hours < 24) return `${Math.round(hours)} ч`;
  return `${decimal(String(Math.round((hours / 24) * 10) / 10))} дн.`;
}

/** Positions of the request by supplier asked: priced, left without a price, or still waiting. */
export function RequestSupplierResponses({
  requestId,
  refreshKey,
}: {
  requestId: string;
  refreshKey?: number;
}) {
  const [revision, setRevision] = useState(0);
  const [replying, setReplying] = useState<SupplierColumn>();
  const matrix = useApi<Matrix>(
    `/requests/${requestId}/supplier-responses?ui_revision=${(refreshKey || 0) + revision}`,
  );
  const suppliers = matrix.data?.suppliers || [];
  return (
    <Section
      title="Ответы поставщиков"
      description="Кому ушёл запрос по каждой позиции и что ответил поставщик. Цена — позиция есть в его квоте. Без цены — поставщик ответил по заявке, но эту позицию не оценил. Если поставщик ответил письмом без цен, отметьте это кнопкой «Ответил без цен»."
    >
      <ErrorBox error={matrix.error} retry={matrix.refresh} />
      {matrix.loading && !matrix.data ? (
        <Loading />
      ) : !suppliers.length ? (
        <p className="muted supplier-responses-empty">
          Запросы по почте ещё не отправлялись. После отправки здесь появится, кто ответил.
        </p>
      ) : (
        <div className="table-scroll">
          <table className="supplier-responses">
            <thead>
              <tr>
                <th>Позиция</th>
                {suppliers.map((column) => (
                  <th key={column.supplier_id}>
                    <strong>{column.name}</strong>
                    <small>Запрос {date(column.asked_at, true)}</small>
                    <small>
                      {column.answered_at
                        ? `Ответ через ${duration(column.hours)}`
                        : 'Ответа по заявке нет'}
                    </small>
                    {column.replied_at && <small title={column.reply_note}>Ответил без цен</small>}
                    {!column.answered_at && (
                      <Button variant="ghost" onClick={() => setReplying(column)}>
                        Ответил без цен
                      </Button>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(matrix.data?.items || []).map((row) => (
                <tr key={row.id}>
                  <td>
                    <span className="stacked">
                      <strong>
                        {row.number}. {row.description}
                      </strong>
                      {row.manufacturer_name && <small>{row.manufacturer_name}</small>}
                    </span>
                  </td>
                  {suppliers.map((column) => {
                    const cell = row.cells[column.supplier_id];
                    return (
                      <td key={column.supplier_id}>
                        {cell ? (
                          <span className="stacked">
                            <Badge
                              value={STATUS[cell.status].title}
                              tone={STATUS[cell.status].tone}
                            />
                            {cell.status === 'priced' && cell.hours !== null && (
                              <small>через {duration(cell.hours)}</small>
                            )}
                          </span>
                        ) : (
                          <span className="muted">не запрашивали</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {replying && (
        <RecordForm
          title={`${replying.name}: ответ без цен`}
          endpoint={`/supplier-mails/${replying.mail_id}/reply`}
          command
          extra={{ version: replying.mail_version }}
          fields={[
            {
              name: 'replied_at',
              label: 'Когда ответил',
              type: 'datetime-local',
              required: true,
              value: nowLocal(),
            },
            {
              name: 'note',
              label: 'Что ответил',
              type: 'textarea',
              required: true,
              minLength: 3,
              placeholder: 'Например: не работаем с брендом Merck',
            },
          ]}
          note="Позиции без квоты этого поставщика будут считаться оценёнными «без цены», а время ответа — до этой даты."
          onClose={() => setReplying(undefined)}
          onSuccess={() => {
            setReplying(undefined);
            setRevision((value) => value + 1);
          }}
        />
      )}
    </Section>
  );
}

function SupplierDetail({
  row,
  from,
  to,
  onClose,
}: {
  row: SupplierRow;
  from: string;
  to: string;
  onClose: () => void;
}) {
  const detail = useApi<{ brands: Brand[]; positions: Entity[] }>(
    `/analytics/suppliers/${row.supplier_id}?date_from=${from}&date_to=${to}`,
  );
  return (
    <Modal title={row.name} wide onClose={onClose}>
      <div className="form-body">
        <ErrorBox error={detail.error} retry={detail.refresh} />
        {detail.loading ? (
          <Loading />
        ) : (
          <>
            <h3>По брендам</h3>
            <DataTable<Brand>
              rows={detail.data?.brands || []}
              columns={[
                { key: 'name', label: 'Производитель' },
                { key: 'positions', label: 'Запрошено позиций' },
                { key: 'priced', label: 'С ценой' },
                { key: 'declined', label: 'Без цены' },
                { key: 'waiting', label: 'Ждём ответа' },
              ]}
            />
            <h3>Позиции</h3>
            <DataTable
              rows={detail.data?.positions || []}
              columns={[
                { key: 'request_number', label: 'Заявка' },
                {
                  key: 'description',
                  label: 'Позиция',
                  render: (item) => (
                    <span className="stacked">
                      <span>{String(item.description)}</span>
                      {Boolean(item.manufacturer_name) && (
                        <small>{String(item.manufacturer_name)}</small>
                      )}
                    </span>
                  ),
                },
                {
                  key: 'requested_at',
                  label: 'Запрос',
                  render: (item) => date(item.requested_at, true),
                },
                {
                  key: 'status',
                  label: 'Ответ',
                  render: (item) => {
                    const status = STATUS[item.status as Status];
                    return <Badge value={status.title} tone={status.tone} />;
                  },
                },
                {
                  key: 'hours',
                  label: 'Время ответа',
                  render: (item) => duration(item.hours as number | null),
                },
              ]}
            />
          </>
        )}
      </div>
    </Modal>
  );
}

/** Suppliers asked for prices in the analytics period: how many answered, how fast, which brands they skip. */
export function SupplierReport({ from, to }: { from: string; to: string }) {
  const [selected, setSelected] = useState<SupplierRow>();
  const report = useApi<Page<SupplierRow> & { basis: string }>(
    `/analytics/suppliers?date_from=${from}&date_to=${to}`,
  );
  return (
    <Section title="Ответы поставщиков" description={report.data?.basis}>
      <ErrorBox error={report.error} retry={report.refresh} />
      {report.loading ? (
        <Loading />
      ) : (
        <DataTable<SupplierRow>
          rows={report.data?.items || []}
          onRow={setSelected}
          columns={[
            { key: 'name', label: 'Поставщик', render: (row) => <strong>{row.name}</strong> },
            { key: 'requests', label: 'Запросов' },
            {
              key: 'answered_requests',
              label: 'Ответил',
              render: (row) =>
                `${row.answered_requests}${row.answer_rate === null ? '' : ` · ${decimal(String(row.answer_rate))} %`}`,
            },
            {
              key: 'average_hours',
              label: 'Время ответа',
              render: (row) =>
                row.average_hours === null
                  ? '—'
                  : `в среднем ${duration(row.average_hours)}, медиана ${duration(row.median_hours)}`,
            },
            { key: 'positions', label: 'Позиций' },
            { key: 'priced', label: 'С ценой' },
            { key: 'declined', label: 'Без цены' },
            { key: 'waiting', label: 'Ждём' },
            {
              key: 'declined_brands',
              label: 'Не даёт цены на',
              render: (row) =>
                row.declined_brands
                  .slice(0, 3)
                  .map((brand) => `${brand.name} (${brand.declined} из ${brand.positions})`)
                  .join(', ') || '—',
            },
          ]}
        />
      )}
      {selected && (
        <SupplierDetail row={selected} from={from} to={to} onClose={() => setSelected(undefined)} />
      )}
    </Section>
  );
}
