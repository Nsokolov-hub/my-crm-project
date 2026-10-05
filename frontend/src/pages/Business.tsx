import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { RecordForm, DirectorySelect } from '../components/Form';
import { Collection } from '../components/Collection';
import {
  Badge,
  Button,
  DataTable,
  DetailPairs,
  ErrorBox,
  Modal,
  PageHeading,
  Section,
} from '../components/ui';
import { download } from '../lib/api';
import { useApi, useCommand } from '../lib/hooks';
import { date, decimal, today } from '../lib/format';
import type { Entity, Field, Page } from '../lib/types';

const calendarFields: Field[] = [
  {
    name: 'direction',
    label: 'Тип платежа',
    type: 'select',
    required: true,
    options: [
      { value: 'income', label: 'Приход' },
      { value: 'expense', label: 'Расход' },
    ],
  },
  { name: 'planned_date', label: 'Плановая дата', type: 'date', required: true },
  { name: 'amount', label: 'Сумма', type: 'decimal', required: true },
  { name: 'currency', label: 'Валюта', required: true },
  { name: 'purpose', label: 'Назначение платежа', type: 'textarea', required: true },
  { name: 'counterparty_id', label: 'Контрагент', type: 'select', source: '/counterparties' },
  { name: 'responsible_id', label: 'Ответственный', type: 'select', source: '/users' },
  {
    name: 'payment_kind',
    label: 'Условия клиента',
    type: 'select',
    options: [
      { value: 'prepayment', label: 'Предоплата' },
      { value: 'deferred', label: 'Отсрочка' },
      { value: 'other', label: 'Прочее' },
    ],
  },
  {
    name: 'recurrence',
    label: 'Повторение',
    type: 'select',
    options: [
      { value: 'none', label: 'Не повторять' },
      { value: 'weekly', label: 'Еженедельно' },
      { value: 'monthly', label: 'Ежемесячно' },
    ],
  },
];
type CalendarPage = Page & { summary: Entity[]; daily: Entity[]; parties: Entity[] };

export function PaymentCalendar() {
  const auth = useAuth();
  const [params] = useSearchParams();
  const [from, setFrom] = useState(today().slice(0, 8) + '01');
  const [to, setTo] = useState('');
  const [status, setStatus] = useState('');
  const [direction, setDirection] = useState('');
  const [kind, setKind] = useState('');
  const [currency, setCurrency] = useState('');
  const [counterparty, setCounterparty] = useState('');
  const [detailed, setDetailed] = useState(false);
  const [creating, setCreating] = useState(params.get('create') === '1');
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<Entity>();
  const [page, setPage] = useState(1);
  const [error, setError] = useState<unknown>();
  const command = useCommand();
  const query = new URLSearchParams({ page: String(page), page_size: '50' });
  Object.entries({
    from_date: from,
    to_date: to,
    status,
    direction,
    payment_kind: kind,
    currency,
    counterparty_id: counterparty,
  }).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  const list = useApi<CalendarPage>('/payment-calendar?' + query);
  async function action(row: Entity, name: string) {
    try {
      await command.run(
        `/payment-calendar/${row.id}/${name}`,
        { version: row.version },
        'POST',
        true,
      );
      setSelected(undefined);
      list.refresh();
    } catch {
      /* displayed below */
    }
  }
  const initial: Record<string, unknown> = {
    direction: params.get('direction') || 'income',
    planned_date: params.get('planned_date') || today(),
    amount: params.get('amount') || '',
    currency: params.get('currency') || 'RUB',
    purpose: params.get('purpose') || '',
    counterparty_id: params.get('counterparty_id'),
    payment_kind: params.get('payment_kind') || 'other',
    recurrence: 'none',
  };
  const sources = Object.fromEntries(
    ['request_id', 'document_id', 'supplier_order_id'].flatMap((key) =>
      params.get(key) ? [[key, params.get(key)]] : [],
    ),
  );
  return (
    <>
      <PageHeading
        title="Платёжный календарь"
        description="Поступления и расходы по датам. Платежи проводит руководитель после согласования."
        actions={<Button onClick={() => setCreating(true)}>Создать платёж</Button>}
      />
      <ErrorBox error={error || list.error || command.error} />
      <div className="form-grid business-filters">
        <label>
          С даты
          <input
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <label>
          По дату
          <input
            type="date"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <label>
          Тип
          <select
            value={direction}
            onChange={(e) => {
              setDirection(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Все</option>
            <option value="income">Приход</option>
            <option value="expense">Расход</option>
          </select>
        </label>
        <label>
          Состояние
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Все</option>
            {['draft', 'pending', 'approved', 'confirmed', 'rejected'].map((value) => (
              <option key={value} value={value}>
                {
                  (
                    {
                      draft: 'Черновик',
                      pending: 'На согласовании',
                      approved: 'Согласован',
                      confirmed: 'Подтверждён',
                      rejected: 'Отклонён',
                    } as Record<string, string>
                  )[value]
                }
              </option>
            ))}
          </select>
        </label>
        <label>
          Валюта
          <input
            value={currency}
            placeholder="Все валюты"
            onChange={(e) => {
              setCurrency(e.target.value.toUpperCase());
              setPage(1);
            }}
          />
        </label>
        <label>
          Оплата клиента
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Все</option>
            <option value="prepayment">Предоплата</option>
            <option value="deferred">Отсрочка</option>
          </select>
        </label>
        <label>
          Контрагент
          <DirectorySelect
            field={{ name: 'calendar_party', label: 'Контрагент', source: '/counterparties' }}
            value={counterparty}
            onChange={(v) => {
              setCounterparty(String(v));
              setPage(1);
            }}
          />
        </label>
        <Button variant="secondary" onClick={() => setDetailed((v) => !v)}>
          {detailed ? 'Краткий вид' : 'Детальный вид'}
        </Button>
      </div>
      {list.data?.summary.map((row) => (
        <Section key={String(row.currency)} title={`Оборот — ${row.currency}`}>
          <DetailPairs
            values={{
              Приход: decimal(row.income),
              Расход: decimal(row.expense),
              Сальдо: decimal(row.balance),
              'Поступлений с предоплатой': row.prepayment_count,
              'Поступлений с отсрочкой': row.deferred_count,
            }}
          />
          <p className="muted">
            Итоги учитывают платежи на согласовании, согласованные и подтверждённые. Черновики и
            отклонённые платежи исключены.
          </p>
          <div
            className="cash-chart"
            role="img"
            aria-label={`Приход и расход по дням, ${row.currency}`}
          >
            {list.data?.daily
              .filter((day) => day.currency === row.currency)
              .map((day) => {
                const maximum = Math.max(
                  1,
                  ...list
                    .data!.daily.filter((d) => d.currency === row.currency)
                    .flatMap((d) => [Number(d.income), Number(d.expense)]),
                );
                return (
                  <div
                    className="cash-chart-day"
                    key={String(day.date)}
                    title={`${date(day.date)}: приход ${decimal(day.income)}, расход ${decimal(day.expense)}`}
                  >
                    <div className="cash-chart-bars">
                      <i style={{ height: `${(Number(day.income) / maximum) * 100}%` }} />
                      <i style={{ height: `${(Number(day.expense) / maximum) * 100}%` }} />
                    </div>
                    <small>{date(day.date)}</small>
                  </div>
                );
              })}
          </div>
          <p>Зелёный — приход, фиолетовый — расход.</p>
        </Section>
      ))}
      <Section title="План и подтверждённые платежи">
        <DataTable
          rows={list.data?.items || []}
          onRow={setSelected}
          columns={[
            { key: 'planned_date', label: 'Дата', render: (r) => date(r.planned_date) },
            {
              key: 'direction',
              label: 'Тип',
              render: (r) => (r.direction === 'income' ? 'Приход' : 'Расход'),
            },
            { key: 'purpose', label: 'Назначение' },
            { key: 'amount', label: 'Сумма', render: (r) => `${decimal(r.amount)} ${r.currency}` },
            { key: 'status', label: 'Состояние', render: (r) => <Badge value={r.status} /> },
            ...(detailed
              ? [
                  { key: 'counterparty_name', label: 'Контрагент' },
                  { key: 'payment_kind', label: 'Условия' },
                  { key: 'recurrence', label: 'Повторение' },
                ]
              : []),
          ]}
        />
        <div className="inline-actions">
          <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            Назад
          </Button>
          <span>Страница {page}</span>
          <Button
            variant="secondary"
            disabled={page * 50 >= (list.data?.total || 0)}
            onClick={() => setPage((p) => p + 1)}
          >
            Далее
          </Button>
        </div>
      </Section>
      {detailed && (
        <Section title="Доля контрагентов в закупках и продажах">
          <DataTable<Entity>
            rows={(list.data?.parties || []).map((r, i) => ({ ...r, id: String(i) }))}
            columns={[
              { key: 'counterparty_name', label: 'Контрагент' },
              { key: 'direction', label: 'Приход / расход' },
              { key: 'currency', label: 'Валюта' },
              { key: 'amount', label: 'Оборот', render: (r) => decimal(r.amount) },
              {
                key: 'share_percent',
                label: 'Доля, %',
                render: (r) => Number(r.share_percent).toFixed(2),
              },
            ]}
          />
        </Section>
      )}
      {creating && (
        <RecordForm
          title="Новый платёж"
          endpoint="/payment-calendar"
          fields={calendarFields}
          initial={initial as Entity}
          extra={sources}
          command
          onClose={() => setCreating(false)}
          onSuccess={() => {
            setCreating(false);
            list.refresh();
          }}
        />
      )}
      {selected && editing && (
        <RecordForm
          title="Изменить платёж"
          endpoint={`/payment-calendar/${selected.id}`}
          fields={calendarFields}
          initial={selected}
          extra={{ version: selected.version }}
          method="PATCH"
          command
          onClose={() => setEditing(false)}
          onSuccess={() => {
            setSelected(undefined);
            setEditing(false);
            list.refresh();
          }}
        />
      )}
      {selected && !editing && (
        <Modal title={String(selected.purpose)} onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Дата: date(selected.planned_date),
                Сумма: `${decimal(selected.amount)} ${selected.currency}`,
                Контрагент: selected.counterparty_name,
                Статус: selected.status,
              }}
            />
            {['draft', 'rejected'].includes(String(selected.status)) && (
              <Button variant="secondary" onClick={() => setEditing(true)}>
                Изменить
              </Button>
            )}
            {['draft', 'rejected'].includes(String(selected.status)) && (
              <Button busy={command.busy} onClick={() => void action(selected, 'submit')}>
                Направить руководителю
              </Button>
            )}
            {selected.status === 'approved' && auth.can('approvals.decide') && (
              <Button busy={command.busy} onClick={() => void action(selected, 'confirm')}>
                Подтвердить платёж
              </Button>
            )}
            <ErrorBox error={command.error} retry={() => setError(undefined)} />
          </div>
        </Modal>
      )}
    </>
  );
}

export function SupplierOrders() {
  const [tab, setTab] = useState('positions');
  const [supplier, setSupplier] = useState('');
  const [status, setStatus] = useState('');
  const [selection, setSelection] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Entity>();
  const [error, setError] = useState<unknown>();
  const command = useCommand();
  const query = new URLSearchParams();
  if (supplier) query.set('supplier_id', supplier);
  if (status) query.set('status', status);
  if (tab === 'ordered') query.set('ordered', 'true');
  const list = useApi<Page>(
    (tab === 'orders' ? '/supplier-orders' : '/supplier-orders/positions') + '?' + query,
  );
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
  ];
  const chosen = (list.data?.items || []).filter((r) => selection.includes(r.id));
  const [prices, setPrices] = useState<Record<string, string>>({});
  const oneSupplier = new Set(chosen.map((r) => `${r.supplier_id}:${r.currency}`)).size === 1;
  async function copy() {
    try {
      await navigator.clipboard.writeText(
        (list.data?.items || [])
          .map((r) =>
            [r.order_number, r.name, r.article, r.packaging, r.quantity, r.status].join('\t'),
          )
          .join('\n'),
      );
    } catch (cause) {
      setError(cause);
    }
  }
  return (
    <>
      <PageHeading
        title="Заказы поставщикам"
        description="Выберите принятые в работу позиции одного поставщика, проверьте цены и выпустите Purchase Order."
      />
      <div className="tabs">
        {[
          ['positions', 'Позиции к заказу'],
          ['ordered', 'Заказанные позиции'],
          ['orders', 'Статус заказов'],
        ].map(([key, title]) => (
          <button
            key={key}
            className={tab === key ? 'active' : ''}
            onClick={() => {
              setTab(key);
              setSelection([]);
            }}
          >
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
          <label>
            Статус
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Все</option>
              <option value="waiting">Ожидаем</option>
              <option value="delivery">Доставка</option>
              <option value="delivered">Доставлено</option>
            </select>
          </label>
        )}
        {tab === 'positions' && (
          <Button disabled={!chosen.length || !oneSupplier} onClick={() => setCreating(true)}>
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
      <ErrorBox error={error || list.error || command.error} />
      {chosen.length > 0 && !oneSupplier && (
        <p className="field-error">Выберите позиции одного поставщика в одной валюте.</p>
      )}
      <DataTable
        rows={list.data?.items || []}
        onRow={tab === 'positions' ? undefined : setSelected}
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
                { key: 'expected_date', label: 'Срок', render: (r) => date(r.expected_date) },
                { key: 'status', label: 'Состояние', render: (r) => <Badge value={r.status} /> },
              ]
            : [
                ...(tab === 'positions'
                  ? [
                      {
                        key: 'select',
                        label: 'Выбор',
                        render: (r: Entity) => (
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
                        ),
                      },
                    ]
                  : []),
                { key: 'name', label: 'Наименование' },
                { key: 'supplier_name', label: 'Поставщик' },
                { key: 'manufacturer', label: 'Производитель' },
                { key: 'article', label: 'Артикул' },
                { key: 'packaging', label: 'Фасовка' },
                { key: 'quantity', label: 'Кол-во', render: (r) => decimal(r.quantity) },
                {
                  key: 'unit_price',
                  label: 'Цена',
                  render: (r) => `${decimal(r.unit_price)} ${r.currency}`,
                },
                { key: 'delivery_days', label: 'Срок, дней' },
                ...(tab === 'ordered'
                  ? [
                      {
                        key: 'status',
                        label: 'Состояние',
                        render: (r: Entity) => <Badge value={r.status} />,
                      },
                    ]
                  : []),
              ]
        }
      />
      {creating && (
        <RecordForm
          title="Заказ поставщику"
          endpoint="/supplier-orders"
          fields={orderFields}
          initial={{
            contract: chosen[0]?.contract,
            payment_terms: chosen[0]?.payment_terms,
            delivery_terms: chosen[0]?.delivery_terms,
          }}
          command
          extra={{ execution_ids: selection, prices }}
          note={
            <div>
              <p>Проверьте закупочные цены выбранных позиций:</p>
              {chosen.map((row) => (
                <label key={row.id}>
                  {String(row.name)} — {String(row.currency)}
                  <input
                    aria-label={`Цена ${row.name}`}
                    value={prices[row.id] ?? String(row.unit_price)}
                    onChange={(e) =>
                      setPrices((p) => ({ ...p, [row.id]: e.target.value.replace(',', '.') }))
                    }
                  />
                </label>
              ))}
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
      {selected && tab === 'ordered' && (
        <RecordForm
          title="Статус позиции"
          endpoint={`/supplier-order-lines/${selected.id}`}
          method="PATCH"
          command
          initial={selected}
          extra={{ version: selected.version }}
          fields={[
            {
              name: 'status',
              label: 'Статус',
              type: 'select',
              options: [
                { value: 'waiting', label: 'Ожидаем' },
                { value: 'delivery', label: 'Доставка' },
                { value: 'delivered', label: 'Доставлено' },
              ],
              required: true,
            },
          ]}
          onClose={() => setSelected(undefined)}
          onSuccess={() => {
            setSelected(undefined);
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
                Срок: date(selected.expected_date),
                Контракт: selected.contract,
                'Условия оплаты': selected.payment_terms,
              }}
            />
            <div className="inline-actions">
              <Button
                onClick={() =>
                  void download(
                    `/supplier-orders/${selected.id}/po.xlsx`,
                    `PO_${selected.number}.xlsx`,
                  ).catch(setError)
                }
              >
                Скачать PO
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
                }}
              />
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
          fields={[
            {
              name: 'decision',
              label: 'Решение',
              type: 'select',
              required: true,
              options: [
                { value: 'approved', label: 'Согласовать' },
                { value: 'rejected', label: 'Отказать' },
              ],
            },
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

export function SalesManagerReport() {
  const [from, setFrom] = useState(today().slice(0, 8) + '01');
  const [to, setTo] = useState(today());
  const list = useApi<Page & { basis: string }>(
    `/analytics/sales-managers?from_date=${from}&to_date=${to}`,
  );
  return (
    <Section title="Продажи по менеджерам">
      <div className="form-grid">
        <label>
          С даты
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          По дату
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
      </div>
      <ErrorBox error={list.error} />
      <p>{list.data?.basis}</p>
      <DataTable
        rows={list.data?.items || []}
        columns={[
          { key: 'manager', label: 'Менеджер' },
          { key: 'currency', label: 'Валюта' },
          { key: 'sales', label: 'Продажи с НДС', render: (r) => decimal(r.sales) },
          { key: 'sale_net', label: 'Продажи без НДС', render: (r) => decimal(r.sale_net) },
          { key: 'gross_profit', label: 'Валовая прибыль', render: (r) => decimal(r.gross_profit) },
          {
            key: 'margin_percent',
            label: 'Маржа, %',
            render: (r) => Number(r.margin_percent).toFixed(2),
          },
          {
            key: 'profitability_percent',
            label: 'Рентабельность, %',
            render: (r) => Number(r.profitability_percent).toFixed(2),
          },
        ]}
      />
    </Section>
  );
}
