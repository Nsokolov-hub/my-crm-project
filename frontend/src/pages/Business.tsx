import { Download } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { RecordForm, DirectorySelect } from '../components/Form';
import { Collection } from '../components/Collection';
import {
  Badge,
  Button,
  DataTable,
  DetailPairs,
  ErrorBox,
  Loading,
  Modal,
  PageHeading,
  Section,
} from '../components/ui';
import { BarChart } from '../components/BarChart';
import { api, download } from '../lib/api';
import { useApi, useCommand } from '../lib/hooks';
import { date, decimal, today } from '../lib/format';
import { FilesPanel } from './Communication';
import type { Entity, Field, Page } from '../lib/types';

export { PaymentCalendar } from './PaymentCalendar';
import { paymentDecisionFields } from './PaymentCalendar';

// Delivery of an ordered position: the logistician moves it on, procurement may also cancel it.
const lineStatuses = [
  { value: 'waiting', label: 'Ожидаем', tone: 'gray' },
  { value: 'delivery', label: 'В пути', tone: 'purple' },
  { value: 'arrived_ru', label: 'Прибыло в РФ', tone: 'amber' },
  { value: 'delivered', label: 'Доставлено клиенту', tone: 'green' },
  { value: 'cancelled', label: 'Отменено', tone: 'red' },
];
function OrderStatus({ value }: { value: unknown }) {
  const status = lineStatuses.find((row) => row.value === value);
  return <Badge value={status?.label || value} tone={status?.tone} />;
}
const STATUS_KEY = 'supplier-orders:statuses';
function savedStatuses(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(STATUS_KEY) || '[]') as unknown;
    return Array.isArray(saved) ? saved.filter((value) => typeof value === 'string') : [];
  } catch {
    return [];
  }
}
const withoutCancelled = lineStatuses
  .filter((row) => row.value !== 'cancelled')
  .map((row) => row.value);
/** Several statuses at once, for instance everything but the cancelled positions. */
function StatusPicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const summary = !value.length
    ? 'Все'
    : value.length === withoutCancelled.length &&
        withoutCancelled.every((code) => value.includes(code))
      ? 'Все, кроме отменённых'
      : lineStatuses
          .filter((row) => value.includes(row.value))
          .map((row) => row.label)
          .join(', ');
  return (
    <details className="status-picker">
      <summary aria-label="Статусы">{summary}</summary>
      <div className="status-picker-menu">
        <div className="inline-actions compact">
          <Button type="button" variant="ghost" onClick={() => onChange([])}>
            Все
          </Button>
          <Button type="button" variant="ghost" onClick={() => onChange(withoutCancelled)}>
            Все, кроме отменённых
          </Button>
        </div>
        {lineStatuses.map((row) => (
          <label key={row.value}>
            <input
              type="checkbox"
              checked={value.includes(row.value)}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...value, row.value]
                    : value.filter((code) => code !== row.value),
                )
              }
            />
            {row.label}
          </label>
        ))}
      </div>
    </details>
  );
}
const plainAmount = (value: unknown) => {
  const raw = String(value ?? '0');
  return raw.includes('.') ? raw.replace(/\.?0+$/, '') : raw;
};

export function SupplierOrders() {
  const auth = useAuth();
  const canOrder = auth.can('procurement.write');
  const tabs = (
    [
      ['positions', 'Позиции к заказу'],
      ['ordered', 'Заказанные позиции'],
      ['orders', 'Статус заказов'],
    ] as const
  ).filter(([key]) => canOrder || key === 'ordered');
  const [tab, setTab] = useState<string>(tabs[0][0]);
  const [supplier, setSupplier] = useState('');
  const [statuses, setStatuses] = useState<string[]>(savedStatuses);
  function chooseStatuses(next: string[]) {
    setStatuses(next);
    setSelection([]);
    try {
      localStorage.setItem(STATUS_KEY, JSON.stringify(next));
    } catch {
      /* The choice is a convenience: without storage it lasts until the page closes. */
    }
  }
  const [selection, setSelection] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [logistics, setLogistics] = useState<Entity>();
  const [cancelling, setCancelling] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const [notice, setNotice] = useState('');
  const [selected, setSelected] = useState<Entity>();
  const [poWithoutLogistics, setPoWithoutLogistics] = useState(false);
  const poOptions = poWithoutLogistics ? '?without_logistics=true' : '';
  const [error, setError] = useState<unknown>();
  const command = useCommand();
  const query = new URLSearchParams();
  if (supplier) query.set('supplier_id', supplier);
  if (statuses.length && tab !== 'positions') query.set('status', statuses.join(','));
  if (tab === 'ordered') query.set('ordered', 'true');
  const list = useApi<Page>(
    (tab === 'orders' ? '/supplier-orders' : '/supplier-orders/positions') + '?' + query,
  );
  const rows = list.data?.items || [];
  const chosen = rows.filter((r) => selection.includes(r.id));
  const [prices, setPrices] = useState<Record<string, string>>({});
  const oneSupplier = new Set(chosen.map((r) => `${r.supplier_id}:${r.currency}`)).size === 1;
  const selectable = (row: Entity) => tab === 'positions' || row.status !== 'cancelled';
  const selectableIds = rows.filter(selectable).map((row) => row.id);
  const orderFields: Field[] = [
    {
      name: 'seller_id',
      label: 'Наша организация',
      type: 'select',
      source: '/sellers',
      required: true,
    },
    { name: 'expected_date', label: 'Ожидаемая дата поставки', type: 'date', required: true },
    { name: 'contract', label: 'Контракт' },
    { name: 'payment_terms', label: 'Условия оплаты' },
    { name: 'delivery_terms', label: 'Условия доставки' },
    {
      name: 'logistics_separately',
      label: 'Без стоимости доставки: логистика оплачивается отдельно, при консолидации заказов',
      type: 'checkbox',
      wide: true,
    },
    {
      name: 'logistics_amount',
      label: `Международная логистика, ${String(chosen[0]?.currency || '')}`,
      type: 'decimal',
      required: true,
      help:
        [
          logistics?.basis
            ? `Подставлено: ${String(logistics.basis)}.`
            : 'Сумма не найдена в волне, расчёте и карточке поставщика.',
          ...((logistics?.notes as string[] | undefined) || []),
        ].join(' ') + ' Сумма входит в итог заказа и в PO. Без доставки — 0.',
    },
  ];
  const priceOf = (row: Entity) => prices[row.id] ?? plainAmount(row.unit_price);
  const goodsTotal = chosen.reduce(
    (sum, row) => sum + Number(priceOf(row) || 0) * Number(row.quantity || 0),
    0,
  );
  function switchTab(key: string) {
    setTab(key);
    setSelection([]);
    setNotice('');
  }
  async function startOrder() {
    setError(undefined);
    try {
      const ids = new URLSearchParams();
      selection.forEach((id) => ids.append('execution_ids', id));
      setLogistics(await api<Entity>('/supplier-orders/logistics?' + ids));
      setCreating(true);
    } catch (cause) {
      setError(cause);
    }
  }
  async function moveLines(next: string) {
    const result = await command
      .run<{ count: number; finished_waves?: string[] }>(
        '/supplier-order-lines/status',
        { lines: chosen.map((row) => ({ id: row.id, version: row.version })), status: next },
        'POST',
        true,
      )
      .catch(() => undefined);
    if (!result) return;
    setNotice(
      `${lineStatuses.find((row) => row.value === next)?.label}: позиций ${result.count}.` +
        (result.finished_waves?.length
          ? ' Все заказы волны доставлены — волна отмечена выполненной и убрана в архив.'
          : ''),
    );
    setSelection([]);
    list.refresh();
  }
  async function cancelLines(event: React.FormEvent) {
    event.preventDefault();
    const result = await command
      .run<{ count: number }>(
        '/supplier-order-lines/cancel',
        {
          lines: chosen.map((row) => ({ id: row.id, version: row.version })),
          reason: cancelReason,
        },
        'POST',
        true,
      )
      .catch(() => undefined);
    if (!result) return;
    setNotice(`Отменено позиций: ${result.count}. Они вернулись во вкладку «Позиции к заказу».`);
    setCancelling(false);
    setCancelReason('');
    setSelection([]);
    list.refresh();
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(
        rows
          .map((r) =>
            [
              r.order_number,
              r.request_number,
              r.client_name,
              r.name,
              r.article,
              r.packaging,
              r.quantity,
              lineStatuses.find((row) => row.value === r.status)?.label || r.status,
            ].join('\t'),
          )
          .join('\n'),
      );
    } catch (cause) {
      setError(cause);
    }
  }
  const selectionColumn = {
    key: 'select',
    label: (
      <input
        aria-label="Выбрать все позиции"
        type="checkbox"
        checked={selectableIds.length > 0 && selectableIds.every((id) => selection.includes(id))}
        onChange={(e) => setSelection(e.target.checked ? selectableIds : [])}
      />
    ),
    sortable: false,
    render: (r: Entity) =>
      selectable(r) ? (
        <input
          aria-label={`Выбрать ${r.name}`}
          type="checkbox"
          checked={selection.includes(r.id)}
          onChange={(e) =>
            setSelection((ids) =>
              e.target.checked ? [...ids, r.id] : ids.filter((id) => id !== r.id),
            )
          }
        />
      ) : null,
  };
  return (
    <>
      <PageHeading
        title="Заказы поставщикам"
        description={
          canOrder
            ? 'Выберите принятые в работу позиции одного поставщика, проверьте цены и логистику и выпустите Purchase Order.'
            : 'Отмечайте заказанные позиции: «В пути», «Прибыло в РФ» и «Доставлено клиенту».'
        }
      />
      <div className="tabs">
        {tabs.map(([key, title]) => (
          <button key={key} className={tab === key ? 'active' : ''} onClick={() => switchTab(key)}>
            {title}
          </button>
        ))}
      </div>
      <div className="form-grid business-filters">
        <label>
          Поставщик
          <DirectorySelect
            field={{
              name: 'order_supplier',
              label: 'Поставщик',
              source: '/counterparties?kind=supplier',
            }}
            value={supplier}
            onChange={(v) => {
              setSupplier(String(v));
              setSelection([]);
            }}
          />
        </label>
        {tab !== 'positions' && (
          <div className="filter-field">
            <span>Статус</span>
            <StatusPicker value={statuses} onChange={chooseStatuses} />
          </div>
        )}
        {tab === 'positions' && (
          <Button disabled={!chosen.length || !oneSupplier} onClick={() => void startOrder()}>
            Создать заказ ({chosen.length})
          </Button>
        )}
        {tab === 'ordered' && (
          <>
            <Button variant="secondary" onClick={() => void copy()}>
              Скопировать список
            </Button>
            <Button
              variant="secondary"
              onClick={() =>
                void download(
                  '/supplier-orders/positions/export.xlsx?' + query,
                  'Заказанные_позиции.xlsx',
                ).catch(setError)
              }
            >
              Excel
            </Button>
          </>
        )}
      </div>
      {tab === 'ordered' && (
        <div className="tab-actions order-line-actions">
          <span className="selection-count">Выбрано позиций: {chosen.length}</span>
          {lineStatuses
            .filter((row) => row.value !== 'cancelled')
            .map((row) => (
              <Button
                key={row.value}
                variant={row.value === 'arrived_ru' ? 'primary' : 'secondary'}
                busy={command.busy}
                disabled={!chosen.length}
                onClick={() => void moveLines(row.value)}
              >
                {row.label}
              </Button>
            ))}
          {canOrder && (
            <Button
              variant="danger"
              disabled={!chosen.length || command.busy}
              onClick={() => {
                command.setError(undefined);
                setCancelling(true);
              }}
            >
              Отменить…
            </Button>
          )}
        </div>
      )}
      <ErrorBox error={error || list.error || (!cancelling && command.error)} />
      {notice && (
        <p role="status" className="info-note">
          {notice}
        </p>
      )}
      {chosen.length > 0 && tab === 'positions' && !oneSupplier && (
        <p className="field-error">Выберите позиции одного поставщика в одной валюте.</p>
      )}
      <DataTable
        rows={rows}
        onRow={tab === 'orders' ? setSelected : undefined}
        columns={
          tab === 'orders'
            ? [
                { key: 'number', label: 'Заказ' },
                { key: 'supplier_name', label: 'Поставщик' },
                {
                  key: 'total',
                  label: 'Сумма',
                  render: (r) => `${decimal(r.total)} ${r.currency}`,
                },
                {
                  key: 'logistics_amount',
                  label: 'В т. ч. логистика',
                  render: (r) =>
                    Number(r.logistics_amount)
                      ? `${decimal(r.logistics_amount)} ${r.currency}`
                      : '—',
                },
                { key: 'expected_date', label: 'Срок', render: (r) => date(r.expected_date) },
                {
                  key: 'status',
                  label: 'Состояние',
                  render: (r) => <OrderStatus value={r.status} />,
                },
              ]
            : [
                selectionColumn,
                ...(tab === 'ordered'
                  ? [
                      { key: 'order_number', label: 'Заказ' },
                      {
                        key: 'request_number',
                        label: 'Заявка',
                        render: (r: Entity) =>
                          r.request_id ? (
                            <Link to={`/requests/${r.request_id}?tab=fulfillment`}>
                              {String(r.request_number || '—')}
                            </Link>
                          ) : (
                            '—'
                          ),
                      },
                      { key: 'client_name', label: 'Клиент' },
                    ]
                  : []),
                { key: 'name', label: 'Наименование' },
                { key: 'supplier_name', label: 'Поставщик' },
                { key: 'manufacturer', label: 'Производитель' },
                { key: 'article', label: 'Артикул' },
                { key: 'packaging', label: 'Фасовка' },
                { key: 'quantity', label: 'Кол-во', render: (r) => decimal(r.quantity) },
                ...(rows.some((r) => r.unit_price !== undefined)
                  ? [
                      {
                        key: 'unit_price',
                        label: 'Цена',
                        render: (r: Entity) =>
                          r.unit_price === undefined
                            ? '—'
                            : `${decimal(r.unit_price)} ${r.currency}`,
                      },
                    ]
                  : []),
                { key: 'delivery_days', label: 'Срок, дней' },
                ...(tab === 'ordered'
                  ? [
                      {
                        key: 'status',
                        label: 'Состояние',
                        render: (r: Entity) => <OrderStatus value={r.status} />,
                      },
                      {
                        key: 'cancel_reason',
                        label: 'Причина отмены',
                        render: (r: Entity) => String(r.cancel_reason || ''),
                      },
                    ]
                  : []),
              ]
        }
      />
      {cancelling && (
        <Modal title="Отмена заказанных позиций" onClose={() => setCancelling(false)}>
          <form className="form-body" onSubmit={(event) => void cancelLines(event)}>
            <p>
              Позиций: {chosen.length}. Отменённые позиции вернутся во вкладку «Позиции к заказу» —
              их можно заказать снова, в том числе у другого поставщика.
            </p>
            <label className="field">
              Причина отмены
              <textarea
                required
                minLength={3}
                value={cancelReason}
                onChange={(e) => setCancelReason(e.target.value)}
              />
            </label>
            <ErrorBox error={command.error} />
            <div className="inline-actions">
              <Button
                type="submit"
                variant="danger"
                busy={command.busy}
                disabled={cancelReason.trim().length < 3}
              >
                Отменить позиции
              </Button>
              <Button type="button" variant="secondary" onClick={() => setCancelling(false)}>
                Не отменять
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {creating && (
        <RecordForm
          title="Заказ поставщику"
          endpoint="/supplier-orders"
          fields={orderFields}
          initial={{
            contract: chosen[0]?.contract,
            payment_terms: chosen[0]?.payment_terms,
            delivery_terms: chosen[0]?.delivery_terms,
            logistics_amount: plainAmount(logistics?.amount),
          }}
          command
          extra={{ execution_ids: selection, prices }}
          deriveValues={(values, changed) =>
            changed === 'logistics_separately'
              ? {
                  ...values,
                  logistics_amount: values.logistics_separately
                    ? '0'
                    : plainAmount(logistics?.amount),
                }
              : values
          }
          note={
            <div>
              <p>Проверьте закупочные цены выбранных позиций ({chosen.length}):</p>
              <div className="table-scroll">
                <table className="order-price-table">
                  <thead>
                    <tr>
                      <th>№</th>
                      <th>Наименование</th>
                      <th>Артикул</th>
                      <th>Фасовка</th>
                      <th>Кол-во</th>
                      <th>Цена за ед., {String(chosen[0]?.currency || '')}</th>
                      <th>Сумма</th>
                    </tr>
                  </thead>
                  <tbody>
                    {chosen.map((row, index) => (
                      <tr key={row.id}>
                        <td>{index + 1}</td>
                        <td className="order-price-name">{String(row.name)}</td>
                        <td>{String(row.article || '—')}</td>
                        <td>{String(row.packaging || '—')}</td>
                        <td>{decimal(row.quantity)}</td>
                        <td>
                          <input
                            aria-label={`Цена ${row.name}`}
                            inputMode="decimal"
                            value={priceOf(row)}
                            onChange={(e) =>
                              setPrices((p) => ({
                                ...p,
                                [row.id]: e.target.value.replace(',', '.'),
                              }))
                            }
                          />
                        </td>
                        <td>{decimal(Number(priceOf(row) || 0) * Number(row.quantity || 0))}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th colSpan={6}>Товары, {String(chosen[0]?.currency || '')}</th>
                      <th>{decimal(goodsTotal)}</th>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>
          }
          onClose={() => setCreating(false)}
          onSuccess={(row) => {
            setCreating(false);
            setSelection([]);
            setSelected(row);
            setTab('orders');
            list.refresh();
          }}
        />
      )}
      {selected && tab === 'orders' && (
        <Modal title={String(selected.number)} onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Поставщик:
                  selected.supplier_name ||
                  ((selected.snapshot as Entity)?.supplier as Entity)?.name,
                Сумма: `${decimal(selected.total)} ${selected.currency}`,
                'В т. ч. логистика': Number(selected.logistics_amount)
                  ? `${decimal(selected.logistics_amount)} ${selected.currency}${
                      selected.logistics_basis ? ` (${String(selected.logistics_basis)})` : ''
                    }`
                  : selected.logistics_separately
                    ? 'Оплачивается отдельно'
                    : 'Не включена',
                Срок: date(selected.expected_date),
                Контракт: selected.contract,
                'Условия оплаты': selected.payment_terms,
              }}
            />
            {Number(selected.logistics_amount) > 0 && (
              <label className="calculation-internal-toggle">
                <input
                  type="checkbox"
                  checked={poWithoutLogistics}
                  onChange={(event) => setPoWithoutLogistics(event.target.checked)}
                />{' '}
                PO без стоимости доставки — логистика будет оплачена отдельно
              </label>
            )}
            <div className="inline-actions">
              <Button
                onClick={() =>
                  void download(
                    `/supplier-orders/${selected.id}/po.pdf${poOptions}`,
                    `PO_${selected.number}.pdf`,
                  ).catch(setError)
                }
              >
                <Download size={16} />
                Скачать PO (PDF)
              </Button>
              <Button
                variant="secondary"
                onClick={() =>
                  void download(
                    `/supplier-orders/${selected.id}/po.xlsx${poOptions}`,
                    `PO_${selected.number}.xlsx`,
                  ).catch(setError)
                }
              >
                Excel
              </Button>
              <Link
                className="button secondary"
                to={
                  '/payment-calendar?' +
                  new URLSearchParams({
                    create: '1',
                    direction: 'expense',
                    supplier_order_id: selected.id,
                    counterparty_id: String(selected.supplier_id),
                    amount: String(selected.total),
                    currency: String(selected.currency),
                    planned_date: today(),
                    purpose: `Оплата заказа ${selected.number}`,
                  })
                }
              >
                Создать расход
              </Link>
            </div>
            <ErrorBox error={error} />
          </div>
        </Modal>
      )}
    </>
  );
}

export function WorkflowApprovals() {
  const auth = useAuth();
  const [selected, setSelected] = useState<Entity>();
  const [revision, setRevision] = useState(0);
  const [deciding, setDeciding] = useState(false);
  return (
    <>
      <Collection
        title="Согласования расчётов, задач и платежей"
        endpoint="/workflow-approvals"
        refreshKey={revision}
        onSelect={setSelected}
        columns={[
          { key: 'title', label: 'Вопрос' },
          { key: 'status', label: 'Состояние', render: (r) => <Badge value={r.status} /> },
          { key: 'created_at', label: 'Передано', render: (r) => date(r.created_at, true) },
        ]}
      />
      {selected && !deciding && (
        <Modal title={String(selected.title)} onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{ Тип: selected.kind, Состояние: selected.status, Решение: selected.reason }}
            />
            {Boolean(selected.request_id) && (
              <Link
                className="button secondary"
                to={`/requests/${selected.request_id}?tab=calculations`}
              >
                Открыть расчёт и заявку
              </Link>
            )}
            {selected.kind === 'calculation' && (
              <DataTable<Entity>
                rows={(((selected.snapshot as Entity)?.lines || []) as Entity[]).map((line, i) => ({
                  ...line,
                  id: String(i),
                }))}
                columns={[
                  { key: 'description', label: 'Позиция' },
                  {
                    key: 'markup_coefficient',
                    label: 'Наценка',
                    render: (row) => `${((Number(row.markup_coefficient) - 1) * 100).toFixed(2)}%`,
                  },
                ]}
              />
            )}
            {selected.kind === 'calculation' && (
              <p>
                Для исправления создайте новую версию расчёта в заявке. Согласование применяется к
                выбранной сохранённой версии.
              </p>
            )}
            {selected.kind === 'calendar' && (
              <DetailPairs
                values={{
                  Сумма: `${decimal((selected.snapshot as Entity)?.amount)} ${(selected.snapshot as Entity)?.currency}`,
                  Назначение: (selected.snapshot as Entity)?.purpose,
                  'Плановая дата': date((selected.snapshot as Entity)?.planned_date),
                }}
              />
            )}
            {selected.kind === 'calendar' && (
              <FilesPanel entityType="calendar_entry" entityId={String(selected.entity_id)} />
            )}
            {selected.kind === 'task' && (
              <p>Результат: {String((selected.snapshot as Entity)?.result || '—')}</p>
            )}
            {auth.can('approvals.decide') && selected.status === 'pending' && (
              <Button onClick={() => setDeciding(true)}>Принять решение</Button>
            )}
          </div>
        </Modal>
      )}
      {selected && deciding && (
        <RecordForm
          title="Решение руководителя"
          endpoint={`/workflow-approvals/${selected.id}/decision`}
          command
          extra={{ version: selected.version }}
          initial={
            selected.kind === 'calendar'
              ? {
                  id: 'calendar-decision',
                  actual_date: today(),
                  outside_payment_days: (selected.snapshot as Entity)?.outside_payment_days,
                }
              : undefined
          }
          fields={[
            {
              name: 'decision',
              label: 'Решение',
              type: 'select',
              required: true,
              options: [
                {
                  value: 'approved',
                  label: selected.kind === 'calendar' ? 'Согласовать оплату' : 'Согласовать',
                },
                { value: 'rejected', label: 'Отказать' },
              ],
            },
            ...(selected.kind === 'calendar' ? paymentDecisionFields : []),
            { name: 'reason', label: 'Комментарий', type: 'textarea', required: true },
          ]}
          onClose={() => setDeciding(false)}
          onSuccess={() => {
            setSelected(undefined);
            setDeciding(false);
            setRevision((r) => r + 1);
          }}
        />
      )}
    </>
  );
}

export function EmployeeAbsences() {
  const [revision, setRevision] = useState(0);
  return (
    <Collection
      title="Отсутствие сотрудников"
      endpoint="/employee-absences"
      command
      refreshKey={revision}
      onChanged={() => setRevision((r) => r + 1)}
      fields={[
        { name: 'user_id', label: 'Сотрудник', type: 'select', source: '/users', required: true },
        { name: 'starts_at', label: 'Начало', type: 'datetime-local', required: true },
        { name: 'ends_at', label: 'Окончание', type: 'datetime-local', required: true },
        { name: 'reason', label: 'Причина', required: true },
      ]}
      columns={[
        { key: 'employee_name', label: 'Сотрудник' },
        { key: 'starts_at', label: 'Начало', render: (r) => date(r.starts_at, true) },
        { key: 'ends_at', label: 'Окончание', render: (r) => date(r.ends_at, true) },
        { key: 'reason', label: 'Причина' },
      ]}
    />
  );
}

export function SalesManagerReport({
  from,
  to,
  charts = false,
}: {
  from: string;
  to: string;
  charts?: boolean;
}) {
  const list = useApi<Page & { basis: string }>(
    `/analytics/sales-managers?from_date=${from}&to_date=${to}`,
  );
  const rows = list.data?.items || [];
  return (
    <Section title="Продажи по менеджерам" description={list.data?.basis}>
      <ErrorBox error={list.error} retry={list.refresh} />
      {list.loading ? (
        <Loading />
      ) : (
        <>
          {charts && rows.length > 0 && (
            <BarChart
              title="Продажи без НДС"
              rows={rows.map((r) => ({
                id: `${String(r.manager)}-${String(r.currency)}`,
                label: `${String(r.manager)}${rows.some((x) => x.currency !== r.currency) ? ` · ${String(r.currency)}` : ''}`,
                value: Number(r.sale_net),
                display: `${decimal(r.sale_net)} ${String(r.currency)}`,
              }))}
            />
          )}
          <DataTable
            rows={rows}
            columns={[
              { key: 'manager', label: 'Менеджер' },
              { key: 'currency', label: 'Валюта' },
              { key: 'sales', label: 'Продажи с НДС', render: (r) => decimal(r.sales) },
              { key: 'sale_net', label: 'Продажи без НДС', render: (r) => decimal(r.sale_net) },
              {
                key: 'gross_profit',
                label: 'Валовая прибыль',
                render: (r) => decimal(r.gross_profit),
              },
              {
                key: 'cost_profitability_percent',
                label: 'Доходность затрат, %',
                render: (r) => Number(r.cost_profitability_percent).toFixed(2),
              },
              {
                key: 'profitability_percent',
                label: 'Рентабельность, %',
                render: (r) => Number(r.profitability_percent).toFixed(2),
              },
            ]}
          />
        </>
      )}
    </Section>
  );
}
