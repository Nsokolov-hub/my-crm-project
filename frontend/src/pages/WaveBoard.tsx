import { useState } from 'react';
import { Badge, Empty, ErrorBox, Loading } from '../components/ui';
import { decimal, metric } from '../lib/format';
import { useApi } from '../lib/hooks';
import type { Entity } from '../lib/types';
import '../styles/waves.scss';

type Summary = {
  sales: string;
  sale_net: string;
  logistics: string;
  logistics_per_unit: string | null;
  accepted_quantity: string;
  accepted_sales: string;
  offered_quantity: string;
  offered_sales: string;
  potential_sales?: string;
  incomplete: boolean;
  profit: string | null;
  profitability_percent: string | null;
  offered_profitability_percent?: string | null;
};
export type BoardWave = Entity & {
  number: string;
  kind: 'plan' | 'work';
  status: string;
  route: string;
  supplier_name: string | null;
  plan_number: string | null;
  planned_quantity: string | null;
  requests: number;
  hidden_requests: number;
  requests_without_proposal: number;
  proposals: number;
  /** Real pieces: positions accepted by clients. */
  quantity: string | null;
  /** Pieces of proposals not accepted yet, shown apart from the sales. */
  offered_quantity?: string;
  summary: Summary | null;
  close_date: string;
  departure_date: string;
  arrival_date: string;
  close_week: number;
  departure_week: number;
  arrival_week: number;
};
type Board = { items: BoardWave[]; basis: string; amounts: boolean; profitability: boolean };
type Lane = 'plan' | 'work';
type DateKey = 'arrival' | 'departure' | 'close';
type Column = { type: 'month'; index: number } | { type: 'gap'; from: number; to: number };

const MONTHS = [
  'Январь',
  'Февраль',
  'Март',
  'Апрель',
  'Май',
  'Июнь',
  'Июль',
  'Август',
  'Сентябрь',
  'Октябрь',
  'Ноябрь',
  'Декабрь',
];
const OF_MONTHS = [
  'января',
  'февраля',
  'марта',
  'апреля',
  'мая',
  'июня',
  'июля',
  'августа',
  'сентября',
  'октября',
  'ноября',
  'декабря',
];
const LANES: { kind: Lane; title: string; note: string }[] = [
  { kind: 'plan', title: 'Плановые', note: 'План поставщика и КП по заявкам' },
  { kind: 'work', title: 'Рабочие', note: 'Подтверждённые заказы' },
];
const DATES: Record<DateKey, { option: string; title: string }> = {
  arrival: { option: 'Прибытию', title: 'Прибытие' },
  departure: { option: 'Отправке', title: 'Отправка' },
  close: { option: 'Приёму заказов', title: 'Приём до' },
};

/** Months since the start of the era, so that neighbouring months differ by one. */
function monthOf(day: string): number {
  const [year, month] = day.split('-').map(Number);
  return year * 12 + month - 1;
}
function monthName(index: number): string {
  return `${MONTHS[index % 12]} ${Math.floor(index / 12)}`;
}
function isoWeek(day: Date): number {
  const thursday = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate()));
  thursday.setUTCDate(thursday.getUTCDate() + 3 - ((thursday.getUTCDay() + 6) % 7));
  const firstThursday = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 4));
  return 1 + Math.round((thursday.getTime() - firstThursday.getTime()) / 604800000);
}
function monthWeeks(index: number): string {
  const year = Math.floor(index / 12);
  const month = index % 12;
  const first = isoWeek(new Date(Date.UTC(year, month, 1)));
  const last = isoWeek(new Date(Date.UTC(year, month + 1, 0)));
  return first < last ? `нед. ${first}–${last}` : `нед. ${first}, ${last}`;
}
function gapName(column: { from: number; to: number }): string {
  return column.from === column.to
    ? `${monthName(column.from)}: волн нет`
    : `${monthName(column.from)} — ${monthName(column.to)}: волн нет`;
}
function dateOf(wave: BoardWave, by: DateKey): string {
  return by === 'arrival'
    ? wave.arrival_date
    : by === 'departure'
      ? wave.departure_date
      : wave.close_date;
}
function weekOf(wave: BoardWave, by: DateKey): number {
  return by === 'arrival'
    ? wave.arrival_week
    : by === 'departure'
      ? wave.departure_week
      : wave.close_week;
}
function dayName(day: string): string {
  const [, month, date] = day.split('-').map(Number);
  return `${date} ${OF_MONTHS[month - 1]}`;
}
/** Large amounts in millions, the rest in whole rubles: a card is read at a glance. */
export function rubles(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const amount = Number(value);
  if (!Number.isFinite(amount)) return '—';
  if (Math.abs(amount) >= 1_000_000)
    return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(amount / 1_000_000)} млн ₽`;
  return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(amount)} ₽`;
}
function percent(value: string | null | undefined): string {
  return value === null || value === undefined ? '—' : `${metric(value)} %`;
}
function requestsNote(wave: BoardWave): string {
  if (!wave.requests && !wave.hidden_requests) return 'Заявок нет';
  const parts = [`Заявок: ${wave.requests + wave.hidden_requests}`];
  if (wave.requests_without_proposal) parts.push(`без КП: ${wave.requests_without_proposal}`);
  if (wave.hidden_requests) parts.push(`недоступно вам: ${wave.hidden_requests}`);
  return parts.join(' · ');
}

function WaveCard({
  wave,
  by,
  detailed,
  board,
  onOpen,
}: {
  wave: BoardWave;
  by: DateKey;
  detailed: boolean;
  board: Board;
  onOpen: (id: string) => void;
}) {
  const summary = wave.summary;
  const loss =
    summary?.profit !== null && summary?.profit !== undefined && Number(summary.profit) < 0;
  // A wave without requests has nothing to sum up yet: only its plan is worth showing.
  const empty = !wave.requests && !wave.hidden_requests;
  return (
    // The title button takes the keyboard; a click anywhere on the card opens the wave as well.
    <article className={`wave-card ${wave.kind}`} onClick={() => onOpen(wave.id)}>
      <header>
        <button type="button" className="wave-card-title">
          {wave.supplier_name || 'Поставщик не указан'}
        </button>
        <span className="wave-card-number">{wave.number}</span>
        <span className="wave-card-date">
          {DATES[by].title}: {dayName(dateOf(wave, by))} · нед. {weekOf(wave, by)}
        </span>
        {wave.kind === 'work' && <Badge value={wave.status} />}
      </header>
      <dl>
        {empty && wave.planned_quantity && (
          <div>
            <dt>План, шт.</dt>
            <dd>{decimal(wave.planned_quantity)}</dd>
          </div>
        )}
        {!empty && board.amounts && (
          <div className="wave-card-sales">
            <dt>Продажи</dt>
            <dd>{summary ? rubles(summary.sales) : '—'}</dd>
          </div>
        )}
        {/* Business remark of 19 October 2026: proposals not accepted yet are not sales. */}
        {!empty && Number(wave.offered_quantity || 0) > 0 && (
          <div className="wave-card-offered">
            <dt>В КП, не приняты</dt>
            <dd>
              {summary ? rubles(summary.offered_sales) : '—'}
              <small> · {decimal(wave.offered_quantity)} шт.</small>
            </dd>
          </div>
        )}
        {!empty && board.profitability && (
          <div className={loss ? 'loss' : undefined}>
            <dt>Рентабельность</dt>
            <dd>{summary ? percent(summary.profitability_percent) : '—'}</dd>
          </div>
        )}
        {!empty && (
          <div>
            <dt>Штук</dt>
            <dd>
              {wave.quantity === null ? '—' : decimal(wave.quantity)}
              {wave.planned_quantity && <small> из плана {decimal(wave.planned_quantity)}</small>}
            </dd>
          </div>
        )}
        {!empty && board.amounts && (
          <div>
            <dt>Логистика на шт.</dt>
            <dd>{summary ? rubles(summary.logistics_per_unit) : '—'}</dd>
          </div>
        )}
        {detailed && !empty && summary && (
          <>
            {summary.offered_profitability_percent !== null &&
              summary.offered_profitability_percent !== undefined && (
                <div>
                  <dt>Рентабельность КП</dt>
                  <dd>{percent(summary.offered_profitability_percent)}</dd>
                </div>
              )}
            {summary.profit !== null && (
              <div>
                <dt>Прибыль</dt>
                <dd>{rubles(summary.profit)}</dd>
              </div>
            )}
            <div>
              <dt>Логистика всего</dt>
              <dd>{rubles(summary.logistics)}</dd>
            </div>
          </>
        )}
        {detailed && (
          <>
            <div>
              <dt>Маршрут</dt>
              <dd>{wave.route || '—'}</dd>
            </div>
            <div>
              <dt>Сроки</dt>
              <dd>
                приём до нед. {wave.close_week} · отправка нед. {wave.departure_week} · прибытие
                нед. {wave.arrival_week}
              </dd>
            </div>
            {wave.plan_number && (
              <div>
                <dt>Плановая</dt>
                <dd>{wave.plan_number}</dd>
              </div>
            )}
          </>
        )}
      </dl>
      <footer>
        {requestsNote(wave)}
        {board.amounts && !summary && wave.requests + wave.hidden_requests > 0 && (
          <span className="wave-card-warning">
            Суммы скрыты: в волне есть заявки, к которым у вас нет доступа.
          </span>
        )}
        {summary?.incomplete && (
          <span className="wave-card-warning">Часть позиций без расчёта в рублях.</span>
        )}
      </footer>
    </article>
  );
}

export function WaveBoard({
  refreshKey,
  onOpen,
}: {
  refreshKey?: number;
  onOpen: (id: string) => void;
}) {
  const [lanes, setLanes] = useState<'both' | Lane>('both');
  const [by, setBy] = useState<DateKey>('arrival');
  const board = useApi<Board>(`/waves/board?ui_revision=${refreshKey || 0}`);
  if (board.loading && !board.data) return <Loading />;
  if (board.error) return <ErrorBox error={board.error} retry={board.refresh} />;
  const data = board.data;
  if (!data) return null;
  const shown = LANES.filter((lane) => lanes === 'both' || lane.kind === lanes);
  const waves = data.items.filter((wave) => shown.some((lane) => lane.kind === wave.kind));
  const months = [...new Set(waves.map((wave) => monthOf(dateOf(wave, by))))].sort((a, b) => a - b);
  const columns: Column[] = [];
  months.forEach((month, index) => {
    if (index && month - months[index - 1] > 1)
      columns.push({ type: 'gap', from: months[index - 1] + 1, to: month - 1 });
    columns.push({ type: 'month', index: month });
  });
  const detailed = lanes !== 'both';
  const inMonth = (lane: Lane, month: number) =>
    waves
      .filter((wave) => wave.kind === lane && monthOf(dateOf(wave, by)) === month)
      .sort(
        (a, b) =>
          dateOf(a, by).localeCompare(dateOf(b, by)) || a.number.localeCompare(b.number, 'ru'),
      );
  // A busy month gets up to three cards side by side instead of a long column.
  const spans = new Map(
    months.map((month) => {
      const most = Math.max(...shown.map((lane) => inMonth(lane.kind, month).length));
      return [month, Math.min(3, Math.max(1, Math.ceil(most / 2)))];
    }),
  );
  const template = [
    'minmax(112px, 136px)',
    ...columns.map((column) => {
      if (column.type === 'gap') return '44px';
      const span = spans.get(column.index) || 1;
      return `minmax(${span * (detailed ? 340 : 270)}px, ${span}fr)`;
    }),
  ].join(' ');
  const now = monthOf(new Date().toISOString().slice(0, 10));
  return (
    <section className="wave-board" aria-label="Доска волн">
      <div className="wave-board-toolbar">
        <div className="wave-board-lanes" role="group" aria-label="Ленты">
          {(
            [
              ['both', 'Обе ленты'],
              ['plan', 'Плановые подробно'],
              ['work', 'Рабочие подробно'],
            ] as const
          ).map(([value, title]) => (
            <button
              key={value}
              type="button"
              className={lanes === value ? 'active' : ''}
              aria-pressed={lanes === value}
              onClick={() => setLanes(value)}
            >
              {title}
            </button>
          ))}
        </div>
        <label>
          Расставить по{' '}
          <select value={by} onChange={(event) => setBy(event.target.value as DateKey)}>
            {(Object.keys(DATES) as DateKey[]).map((key) => (
              <option key={key} value={key}>
                {DATES[key].option}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="wave-board-basis">{data.basis}</p>
      {!waves.length ? (
        <Empty
          title="Действующих волн нет"
          description="Создайте волну кнопкой «Новая волна»: она появится здесь в своём месяце."
        />
      ) : (
        <div className="wave-board-scroll">
          <div className="wave-board-grid" style={{ gridTemplateColumns: template }}>
            <div className="wave-board-corner" />
            {columns.map((column) =>
              column.type === 'month' ? (
                <div
                  key={`month-${column.index}`}
                  className={`wave-board-month ${column.index === now ? 'current' : ''}`}
                >
                  <strong>{monthName(column.index)}</strong>
                  <span>
                    {monthWeeks(column.index)}
                    {column.index === now && ' · сейчас'}
                  </span>
                </div>
              ) : (
                <div
                  key={`gap-${column.from}`}
                  className="wave-board-gap"
                  title={gapName(column)}
                  aria-label={gapName(column)}
                >
                  ···
                </div>
              ),
            )}
            {shown.map((lane) => {
              const laneWaves = waves.filter((wave) => wave.kind === lane.kind);
              const sales = laneWaves.every((wave) => wave.summary)
                ? laneWaves.reduce((sum, wave) => sum + Number(wave.summary?.sales || 0), 0)
                : null;
              return [
                <div key={`lane-${lane.kind}`} className={`wave-board-lane ${lane.kind}`}>
                  <strong>{lane.title}</strong>
                  <span>{laneWaves.length ? `Волн: ${laneWaves.length}` : 'Волн нет'}</span>
                  {data.amounts && sales !== null && laneWaves.length > 0 && (
                    <span>
                      Продажи
                      <b>{rubles(String(sales))}</b>
                    </span>
                  )}
                  <small>{lane.note}</small>
                </div>,
                ...columns.map((column) =>
                  column.type === 'month' ? (
                    <div
                      key={`${lane.kind}-${column.index}`}
                      className={`wave-board-cell ${column.index === now ? 'current' : ''}`}
                      style={{
                        gridTemplateColumns: `repeat(${spans.get(column.index) || 1}, minmax(0, 1fr))`,
                      }}
                    >
                      {inMonth(lane.kind, column.index).map((wave) => (
                        <WaveCard
                          key={wave.id}
                          wave={wave}
                          by={by}
                          detailed={detailed}
                          board={data}
                          onOpen={onOpen}
                        />
                      ))}
                    </div>
                  ) : (
                    <div key={`${lane.kind}-gap-${column.from}`} className="wave-board-gap-cell" />
                  ),
                ),
              ];
            })}
          </div>
        </div>
      )}
    </section>
  );
}
