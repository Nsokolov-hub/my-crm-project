import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { DirectorySelect, RecordForm } from '../components/Form';
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
import { date, decimal, today } from '../lib/format';
import { useApi, useCommand } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';
import { FilesPanel } from './Communication';

const weekdayNames = [
  'Понедельник',
  'Вторник',
  'Среда',
  'Четверг',
  'Пятница',
  'Суббота',
  'Воскресенье',
];
export const paymentConfirmationFields: Field[] = [
  {
    name: 'actual_date',
    label: 'Фактическая дата оплаты',
    type: 'date',
    max: today(),
    help: 'Подтверждённый платёж меняет остаток на эту дату.',
  },
  { name: 'outside_payment_days', label: 'Платёж вне платёжных дней', type: 'checkbox' },
];
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
  { name: 'planned_date', label: 'Плановая дата оплаты', type: 'date', required: true },
  {
    name: 'outside_payment_days',
    label: 'Платёж вне платёжных дней',
    type: 'checkbox',
    help: 'Отметьте для расхода в день, который не входит в график платежей.',
  },
  { name: 'amount', label: 'Сумма', type: 'decimal', required: true },
  { name: 'currency', label: 'Валюта', required: true },
  { name: 'counterparty_id', label: 'Контрагент', type: 'select', source: '/counterparties' },
  { name: 'purpose', label: 'Назначение платежа', type: 'textarea', required: true },
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
const balanceFields: Field[] = [
  {
    name: 'balance_date',
    label: 'Дата сальдо (начало дня)',
    type: 'date',
    max: today(),
    required: true,
  },
  { name: 'currency', label: 'Валюта', required: true },
  {
    name: 'amount',
    label: 'Сальдо',
    required: true,
    help: 'Остаток на начало выбранного дня. Допускается отрицательная сумма.',
  },
  { name: 'reason', label: 'Основание', type: 'textarea', required: true },
];
type CalendarPage = Page & {
  summary: Entity[];
  daily: Entity[];
  parties: Entity[];
  global_balance: boolean;
  payment_days: { version: number; weekdays: number[] };
};
const iso = (value: Date) =>
  `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
const endOfMonth = () => {
  const now = new Date();
  return iso(new Date(now.getFullYear(), now.getMonth() + 1, 0));
};

function CashCharts({
  days,
  currency,
  grouping,
  showBalance,
}: {
  days: Entity[];
  currency: string;
  grouping: string;
  showBalance: boolean;
}) {
  const chartRef = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(720);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setWidth(Math.round(entry.contentRect.width));
    });
    observer.observe(chart);
    return () => observer.disconnect();
  }, []);
  const groups = new Map<string, Entity>();
  for (const day of days.filter((row) => row.currency === currency)) {
    const stamp = String(day.date);
    let key = stamp;
    if (grouping === 'month') key = stamp.slice(0, 7) + '-01';
    if (grouping === 'week') {
      const value = new Date(stamp + 'T12:00:00');
      value.setDate(value.getDate() - ((value.getDay() + 6) % 7));
      key = iso(value);
    }
    const row = groups.get(key) || {
      id: key,
      date: key,
      period_start: stamp,
      income: 0,
      expense: 0,
    };
    row.income = Number(row.income) + Number(day.income);
    row.expense = Number(row.expense) + Number(day.expense);
    row.balance = day.balance;
    row.period_end = stamp;
    groups.set(key, row);
  }
  const rows = [...groups.values()];
  const left = 110,
    right = 15,
    top = 20,
    bottom = 175;
  const step = (width - left - right) / Math.max(rows.length, 1);
  const maximum = Math.max(1, ...rows.flatMap((r) => [Number(r.income), Number(r.expense)]));
  const balances = rows.filter((r) => r.balance != null);
  const minimum = balances.length ? Math.min(...balances.map((r) => Number(r.balance))) : 0;
  const maximumBalance = balances.length ? Math.max(...balances.map((r) => Number(r.balance))) : 1;
  const padding = Math.max(1, (maximumBalance - minimum) * 0.1, Math.abs(maximumBalance) * 0.005);
  const low = minimum - padding;
  const high = maximumBalance + padding;
  const x = (index: number) => left + (index + 0.5) * step;
  const y = (value: number) => bottom - ((value - low) / (high - low)) * (bottom - top);
  const tickCount = Math.max(2, Math.min(7, Math.floor((width - left - right) / 50)));
  const tick = (index: number) =>
    index === 0 ||
    index === rows.length - 1 ||
    index % Math.max(1, Math.ceil(rows.length / tickCount)) === 0;
  return (
    <div className={`calendar-charts${showBalance ? '' : ' single'}`}>
      <figure>
        <figcaption>Фактические поступления и расходы, {currency}</figcaption>
        <svg
          ref={chartRef}
          viewBox={`0 0 ${width} 215`}
          height="215"
          role="img"
          aria-label={`График поступлений и расходов, ${currency}`}
        >
          <line
            x1={left}
            y1={bottom}
            x2={width - right}
            y2={bottom}
            className="calendar-chart-axis"
          />
          {[0, 0.5, 1].map((part) => (
            <g key={part}>
              <text x={left - 8} y={bottom - part * (bottom - top) + 4} textAnchor="end">
                {decimal(maximum * part)}
              </text>
              <line
                x1={left}
                x2={width - right}
                y1={bottom - part * (bottom - top)}
                y2={bottom - part * (bottom - top)}
                className="calendar-chart-grid"
              />
            </g>
          ))}
          {rows.map((row, i) => (
            <g key={row.id}>
              <title>
                {date(row.period_start)}
                {grouping !== 'day' ? ' — ' + date(row.period_end) : ''}: приход{' '}
                {decimal(row.income)}, расход {decimal(row.expense)} {currency}
              </title>
              <rect
                x={x(i) - Math.min(step * 0.35, 12)}
                y={bottom - (Number(row.income) / maximum) * (bottom - top)}
                width={Math.min(step * 0.3, 10)}
                height={(Number(row.income) / maximum) * (bottom - top)}
                className="calendar-chart-income"
              />
              <rect
                x={x(i) + 1}
                y={bottom - (Number(row.expense) / maximum) * (bottom - top)}
                width={Math.min(step * 0.3, 10)}
                height={(Number(row.expense) / maximum) * (bottom - top)}
                className="calendar-chart-expense"
              />
              {tick(i) && (
                <text x={x(i)} y={bottom + 22} textAnchor="middle">
                  {String(row.date).slice(8, 10)}.{String(row.date).slice(5, 7)}
                </text>
              )}
            </g>
          ))}
        </svg>
        <p className="calendar-legend">
          <span className="income">Приход</span>
          <span className="expense">Расход</span>
        </p>
      </figure>
      {showBalance && (
        <figure>
          <figcaption>
            Остаток на конец{' '}
            {grouping === 'week' ? 'недели' : grouping === 'month' ? 'месяца' : 'дня'}, {currency}
          </figcaption>
          {balances.length ? (
            <svg
              viewBox={`0 0 ${width} 215`}
              height="215"
              role="img"
              aria-label={`График остатка денежных средств, ${currency}`}
            >
              {[low, (low + high) / 2, high].map((value, index) => (
                <g key={index}>
                  <text x={left - 8} y={y(value) + 4} textAnchor="end">
                    {decimal(value)}
                  </text>
                  <line
                    x1={left}
                    x2={width - right}
                    y1={y(value)}
                    y2={y(value)}
                    className="calendar-chart-grid"
                  />
                </g>
              ))}
              <polyline
                points={rows
                  .flatMap((row, i) =>
                    row.balance == null ? [] : [`${x(i)},${y(Number(row.balance))}`],
                  )
                  .join(' ')}
                className="calendar-chart-balance"
              />
              {rows.map((row, i) => (
                <g key={row.id}>
                  {row.balance != null && (
                    <circle
                      cx={x(i)}
                      cy={y(Number(row.balance))}
                      r="3"
                      className="calendar-chart-dot"
                    >
                      <title>
                        {date(row.period_end)}: {decimal(row.balance)} {currency}
                      </title>
                    </circle>
                  )}
                  {tick(i) && (
                    <text x={x(i)} y={bottom + 22} textAnchor="middle">
                      {String(row.date).slice(8, 10)}.{String(row.date).slice(5, 7)}
                    </text>
                  )}
                </g>
              ))}
            </svg>
          ) : (
            <p className="calendar-empty-chart">
              Зафиксируйте сальдо на начало дня, чтобы видеть остаток средств.
            </p>
          )}
        </figure>
      )}
    </div>
  );
}

export function PaymentCalendar() {
  const auth = useAuth();
  const [params] = useSearchParams();
  const [from, setFrom] = useState(today().slice(0, 8) + '01');
  const [to, setTo] = useState(endOfMonth());
  const [status, setStatus] = useState('');
  const [direction, setDirection] = useState('');
  const [kind, setKind] = useState('');
  const [detailed, setDetailed] = useState(false);
  const [currency, setCurrency] = useState('');
  const [counterparty, setCounterparty] = useState('');
  const [dateBasis, setDateBasis] = useState('effective');
  const [grouping, setGrouping] = useState('day');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Entity>();
  const [creating, setCreating] = useState(params.get('create') === '1');
  const [editing, setEditing] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [balancesOpen, setBalancesOpen] = useState(false);
  const [balanceEdit, setBalanceEdit] = useState<Entity>();
  const [exportDate, setExportDate] = useState(today());
  const [exportBasis, setExportBasis] = useState('actual');
  const [error, setError] = useState<unknown>();
  const command = useCommand();
  const query = new URLSearchParams({
    from_date: from,
    to_date: to,
    page: String(page),
    page_size: '50',
    date_basis: dateBasis,
  });
  Object.entries({
    status,
    direction,
    currency,
    counterparty_id: counterparty,
    payment_kind: kind,
  }).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  const list = useApi<CalendarPage>('/payment-calendar?' + query);
  const balances = useApi<Page>(
    balancesOpen && list.data?.global_balance ? '/payment-calendar/balances' : null,
  );
  const rules = list.data?.payment_days || { version: 1, weekdays: [1, 3] };
  function refresh() {
    setSelected(undefined);
    setEditing(false);
    setDeciding(false);
    setConfirming(false);
    list.refresh();
  }
  function preset(period: string) {
    const now = new Date();
    const start = new Date(now);
    let end = new Date(now);
    if (period === 'week') start.setDate(start.getDate() - 6);
    if (period === 'month') {
      start.setDate(1);
      end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
    }
    if (period === 'quarter') {
      start.setMonth(Math.floor(now.getMonth() / 3) * 3, 1);
      end = new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3 + 3, 0);
    }
    if (period === 'year') {
      start.setMonth(0, 1);
      end = new Date(now.getFullYear(), 11, 31);
    }
    setFrom(iso(start));
    setTo(iso(end));
    setPage(1);
  }
  async function submit(row: Entity) {
    try {
      await command.run(
        `/payment-calendar/${row.id}/submit`,
        { version: row.version },
        'POST',
        true,
      );
      refresh();
    } catch {
      /* visible error */
    }
  }
  const sources = Object.fromEntries(
    ['request_id', 'document_id', 'supplier_order_id'].flatMap((key) =>
      params.get(key) ? [[key, params.get(key)]] : [],
    ),
  );
  const initial = {
    id: 'new',
    direction: params.get('direction') || 'expense',
    planned_date: params.get('planned_date') || today(),
    amount: params.get('amount') || '',
    currency: params.get('currency') || 'RUB',
    purpose: params.get('purpose') || '',
    counterparty_id: params.get('counterparty_id'),
    payment_kind: params.get('payment_kind') || 'other',
    recurrence: 'none',
    outside_payment_days: false,
  };
  return (
    <>
      <PageHeading
        title="Платёжный календарь"
        description="Планируйте платежи, прикладывайте счета и направляйте руководителю. Остаток учитывает только подтверждённые операции по фактической дате."
        actions={
          <>
            <Button onClick={() => setCreating(true)}>Создать платёж</Button>
            {auth.can('approvals.decide') && (
              <Button variant="secondary" onClick={() => setRulesOpen(true)}>
                Платёжные дни
              </Button>
            )}
            {list.data?.global_balance && (
              <Button variant="secondary" onClick={() => setBalancesOpen(true)}>
                Зафиксировать сальдо
              </Button>
            )}
          </>
        }
      />
      <ErrorBox error={error || list.error || command.error} />
      <p className="calendar-policy">
        Платёжные дни расходов:{' '}
        <strong>{rules.weekdays.map((day) => weekdayNames[day]).join(', ')}</strong>. Для исключения
        отметьте «Платёж вне платёжных дней».
      </p>
      <div className="calendar-period">
        <div className="inline-actions">
          {[
            ['week', '7 дней'],
            ['month', 'Месяц'],
            ['quarter', 'Квартал'],
            ['year', 'Год'],
          ].map(([key, title]) => (
            <Button key={key} variant="secondary" onClick={() => preset(key)}>
              {title}
            </Button>
          ))}
        </div>
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
          График по
          <select value={grouping} onChange={(e) => setGrouping(e.target.value)}>
            <option value="day">Дням</option>
            <option value="week">Неделям</option>
            <option value="month">Месяцам</option>
          </select>
        </label>
      </div>
      {!list.data?.global_balance && list.data && (
        <p className="muted">
          Показаны доступные вам платежи. Общее сальдо организации доступно руководителю.
        </p>
      )}
      {list.data?.summary.map((row) => (
        <Section key={String(row.currency)} title={`Денежные средства — ${row.currency}`}>
          <div className="calendar-metrics">
            <div>
              <span>Приход за период</span>
              <strong className="income">{decimal(row.income)}</strong>
            </div>
            <div>
              <span>Расход за период</span>
              <strong className="expense">{decimal(row.expense)}</strong>
            </div>
            {list.data?.global_balance && (
              <>
                <div>
                  <span>Остаток на начало периода</span>
                  <strong>{decimal(row.opening_balance)}</strong>
                </div>
                <div>
                  <span>Остаток на конец периода</span>
                  <strong>{decimal(row.closing_balance)}</strong>
                </div>
                <div>
                  <span>Фактический остаток на сегодня</span>
                  <strong>{decimal(row.current_balance)}</strong>
                </div>
              </>
            )}
            <div>
              <span>План прихода</span>
              <strong>{decimal(row.planned_income)}</strong>
            </div>
            <div>
              <span>План расхода</span>
              <strong>{decimal(row.planned_expense)}</strong>
            </div>
          </div>
          <p className="calendar-note">
            Факт и остаток учитывают подтверждённые платежи. План учитывает платежи на согласовании
            и согласованные. Черновики и отклонённые платежи не меняют остаток. Итоги относятся ко
            всему доступному календарю за выбранный период; фильтры ниже меняют список.
          </p>
          {detailed && (
            <DetailPairs
              values={{
                'Поступлений с предоплатой': row.prepayment_count,
                'Поступлений с отсрочкой': row.deferred_count,
              }}
            />
          )}
          <CashCharts
            days={list.data?.daily || []}
            currency={String(row.currency)}
            grouping={grouping}
            showBalance={Boolean(list.data?.global_balance)}
          />
        </Section>
      ))}
      <Section title="Платежи">
        <div className="calendar-list-filters">
          <label>
            Даты в списке
            <select
              value={dateBasis}
              onChange={(e) => {
                setDateBasis(e.target.value);
                setPage(1);
              }}
            >
              <option value="effective">Факт для оплаченных, план для остальных</option>
              <option value="planned">Плановая дата</option>
              <option value="actual">Фактическая дата</option>
            </select>
          </label>
          <label>
            Статус
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(1);
              }}
            >
              <option value="">Все</option>
              {[
                ['draft', 'Черновик'],
                ['pending', 'На согласовании'],
                ['approved', 'Согласовано'],
                ['confirmed', 'Подтверждено'],
                ['rejected', 'Отклонено'],
              ].map(([value, title]) => (
                <option key={value} value={value}>
                  {title}
                </option>
              ))}
            </select>
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
            Валюта
            <input
              value={currency}
              placeholder="Все валюты"
              maxLength={3}
              onChange={(e) => {
                setCurrency(e.target.value.toUpperCase());
                setPage(1);
              }}
            />
          </label>
          <label>
            Контрагент
            <DirectorySelect
              field={{ name: 'calendar_party', label: 'Контрагент', source: '/counterparties' }}
              value={counterparty}
              onChange={(value) => {
                setCounterparty(String(value || ''));
                setPage(1);
              }}
            />
          </label>
          <label>
            Оплата клиента
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setPage(1);
              }}
            >
              <option value="">Все</option>
              <option value="prepayment">Предоплата</option>
              <option value="deferred">Отсрочка</option>
              <option value="other">Прочее</option>
            </select>
          </label>
          <Button variant="secondary" onClick={() => setDetailed((value) => !value)}>
            {detailed ? 'Краткий вид' : 'Детальный вид'}
          </Button>
        </div>
        <DataTable
          className={`calendar-table${detailed ? ' detailed' : ''}`}
          rows={list.data?.items || []}
          onRow={setSelected}
          rowClassName={(row) => `calendar-row-${row.status}`}
          columns={[
            { key: 'counterparty_name', label: 'Контрагент' },
            { key: 'purpose', label: 'Назначение' },
            {
              key: 'amount',
              label: 'Сумма',
              render: (row) => (
                <strong className={String(row.direction)}>
                  {row.direction === 'expense' ? '−' : '+'}
                  {decimal(row.amount)} {String(row.currency)}
                </strong>
              ),
            },
            {
              key: 'planned_date',
              label: 'Плановая дата',
              render: (row) => date(row.planned_date),
            },
            {
              key: 'actual_date',
              label: 'Фактическая дата',
              render: (row) => date(row.actual_date),
            },
            {
              key: 'status',
              label: 'Статус',
              render: (row) => (
                <Badge
                  value={row.status}
                  tone={
                    row.status === 'rejected'
                      ? 'red'
                      : row.status === 'confirmed'
                        ? 'green'
                        : row.status === 'pending'
                          ? 'amber'
                          : 'gray'
                  }
                />
              ),
            },
            {
              key: 'outside_payment_days',
              label: 'Вне графика',
              render: (row) => (row.outside_payment_days ? 'Да' : '—'),
            },
            ...(detailed
              ? [
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
        <Section title="Доля контрагентов в фактических поступлениях и расходах">
          <DataTable<Entity>
            rows={(list.data?.parties || []).map((row, i) => ({ ...row, id: String(i) }))}
            columns={[
              { key: 'counterparty_name', label: 'Контрагент' },
              { key: 'direction', label: 'Приход / расход' },
              { key: 'currency', label: 'Валюта' },
              { key: 'amount', label: 'Оборот', render: (row) => decimal(row.amount) },
              {
                key: 'share_percent',
                label: 'Доля, %',
                render: (row) => Number(row.share_percent).toFixed(2),
              },
            ]}
          />
        </Section>
      )}
      {auth.can('exports.download') && (
        <Section title="Реестр платежей за день">
          <div className="calendar-period">
            <label>
              Дата реестра
              <input
                type="date"
                value={exportDate}
                onChange={(e) => setExportDate(e.target.value)}
              />
            </label>
            <label>
              Отбирать по
              <select value={exportBasis} onChange={(e) => setExportBasis(e.target.value)}>
                <option value="actual">Фактической дате</option>
                <option value="planned">Плановой дате</option>
                <option value="effective">Факту оплаченных и плану остальных</option>
              </select>
            </label>
            <Button
              variant="secondary"
              onClick={() =>
                void download(
                  `/payment-calendar/export?on_date=${exportDate}&date_basis=${exportBasis}`,
                  `payments-${exportDate}.xlsx`,
                ).catch(setError)
              }
            >
              Выгрузить Excel
            </Button>
          </div>
        </Section>
      )}
      {creating && (
        <RecordForm
          title="Новый платёж"
          endpoint="/payment-calendar"
          fields={calendarFields}
          initial={initial}
          extra={sources}
          command
          onClose={() => setCreating(false)}
          onSuccess={(row) => {
            setCreating(false);
            setSelected(row);
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
          onSuccess={refresh}
        />
      )}
      {selected && !editing && !deciding && !confirming && (
        <Modal
          title={String(selected.purpose)}
          wide
          className="calendar-payment-modal"
          onClose={() => setSelected(undefined)}
        >
          <div className="form-body">
            <DetailPairs
              values={{
                'Плановая дата': date(selected.planned_date),
                'Фактическая дата': date(selected.actual_date),
                Сумма: `${decimal(selected.amount)} ${selected.currency}`,
                Контрагент: selected.counterparty_name,
                Статус: selected.status,
                'Вне платёжных дней': selected.outside_payment_days ? 'Да' : 'Нет',
                'Решение руководителя': selected.decision_reason,
              }}
            />
            <FilesPanel entityType="calendar_entry" entityId={selected.id} />
            <p className="calendar-note">
              Для расхода приложите счёт перед отправкой руководителю. Файл должен пройти проверку
              до подтверждения оплаты.
            </p>
            <div className="inline-actions">
              {['draft', 'rejected'].includes(String(selected.status)) && (
                <>
                  <Button variant="secondary" onClick={() => setEditing(true)}>
                    Изменить
                  </Button>
                  <Button busy={command.busy} onClick={() => void submit(selected)}>
                    Направить руководителю
                  </Button>
                </>
              )}
              {selected.status === 'pending' &&
                Boolean(selected.review_id) &&
                auth.can('approvals.decide') && (
                  <Button onClick={() => setDeciding(true)}>Согласовать или отклонить</Button>
                )}
              {selected.status === 'approved' && auth.can('approvals.decide') && (
                <Button onClick={() => setConfirming(true)}>Подтвердить оплату</Button>
              )}
            </div>
            <ErrorBox error={command.error} />
          </div>
        </Modal>
      )}
      {selected && deciding && (
        <RecordForm
          title="Решение по платежу"
          endpoint={`/workflow-approvals/${selected.review_id}/decision`}
          command
          extra={{ version: selected.review_version }}
          initial={{
            id: 'decision',
            decision: 'approved',
            actual_date: today(),
            outside_payment_days: selected.outside_payment_days,
          }}
          fields={[
            {
              name: 'decision',
              label: 'Решение',
              type: 'select',
              required: true,
              options: [
                { value: 'approved', label: 'Подтвердить оплату' },
                { value: 'rejected', label: 'Отклонить' },
              ],
            },
            ...paymentConfirmationFields,
            { name: 'reason', label: 'Комментарий', type: 'textarea', required: true },
          ]}
          onClose={() => setDeciding(false)}
          onSuccess={refresh}
        />
      )}
      {selected && confirming && (
        <RecordForm
          title="Подтвердить оплату"
          endpoint={`/payment-calendar/${selected.id}/confirm`}
          command
          extra={{ version: selected.version }}
          initial={{
            id: 'confirmation',
            actual_date: today(),
            outside_payment_days: selected.outside_payment_days,
          }}
          fields={paymentConfirmationFields.map((field) =>
            field.name === 'actual_date' ? { ...field, required: true } : field,
          )}
          onClose={() => setConfirming(false)}
          onSuccess={refresh}
        />
      )}
      {rulesOpen && (
        <RecordForm
          title="Платёжные дни"
          endpoint="/payment-calendar/rules"
          method="PUT"
          command
          extra={{ version: rules.version }}
          initial={{ id: 'rules', weekdays: rules.weekdays.map(String) }}
          fields={[
            {
              name: 'weekdays',
              label: 'Дни недели',
              type: 'multiselect',
              required: true,
              options: weekdayNames.map((name, i) => ({ value: String(i), label: name })),
            },
          ]}
          transform={(body) => ({ ...body, weekdays: (body.weekdays as string[]).map(Number) })}
          onClose={() => setRulesOpen(false)}
          onSuccess={() => {
            setRulesOpen(false);
            list.refresh();
          }}
        />
      )}
      {balancesOpen && !balanceEdit && (
        <Modal title="Сальдо на фиксированную дату" wide onClose={() => setBalancesOpen(false)}>
          <div className="form-body">
            <p>
              Зафиксируйте остаток на начало дня. Подтверждённые платежи этой даты прибавляются или
              вычитаются после него. Новая фиксация на более позднюю дату задаёт новую точку
              отсчёта.
            </p>
            <ErrorBox error={balances.error} />
            <Button
              onClick={() =>
                setBalanceEdit({
                  id: 'new-balance',
                  balance_date: today(),
                  currency: 'RUB',
                  amount: '',
                  reason: '',
                })
              }
            >
              Добавить сальдо
            </Button>
            <DataTable
              rows={balances.data?.items || []}
              onRow={setBalanceEdit}
              columns={[
                {
                  key: 'balance_date',
                  label: 'На начало дня',
                  render: (row) => date(row.balance_date),
                },
                { key: 'currency', label: 'Валюта' },
                { key: 'amount', label: 'Сальдо', render: (row) => decimal(row.amount) },
                { key: 'reason', label: 'Основание' },
              ]}
            />
          </div>
        </Modal>
      )}
      {balanceEdit && (
        <RecordForm
          title={`Сальдо на начало дня${balanceEdit.version ? ' · ' + date(balanceEdit.balance_date) + ' ' + balanceEdit.currency : ''}`}
          endpoint="/payment-calendar/balances"
          method="PUT"
          command
          fields={
            balanceEdit.version
              ? balanceFields.filter((field) => !['balance_date', 'currency'].includes(field.name))
              : balanceFields
          }
          initial={balanceEdit}
          extra={
            balanceEdit.version
              ? {
                  version: balanceEdit.version,
                  balance_date: balanceEdit.balance_date,
                  currency: balanceEdit.currency,
                }
              : {}
          }
          onClose={() => setBalanceEdit(undefined)}
          onSuccess={() => {
            setBalanceEdit(undefined);
            balances.refresh();
            list.refresh();
          }}
        />
      )}
    </>
  );
}
