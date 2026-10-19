import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { Waves } from '../pages/Fulfillment';
import { rubles, WaveBoard, type BoardWave } from '../pages/WaveBoard';

let rights = new Set(['waves.write', 'finance.purchase.read']);
vi.mock('../app/Auth', () => ({ useAuth: () => ({ can: (code: string) => rights.has(code) }) }));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  rights = new Set(['waves.write', 'finance.purchase.read']);
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

// A plan holds proposals only: its real sales are zero and the proposals are shown apart.
const summary = {
  sales: '0.00',
  sale_net: '0.00',
  logistics: '0.00',
  logistics_per_unit: null,
  accepted_quantity: '0',
  accepted_sales: '0.00',
  offered_quantity: '120',
  offered_sales: '2400000.00',
  potential_sales: '2400000.00',
  incomplete: false,
  profit: '0.00',
  profitability_percent: null,
  offered_profitability_percent: '18.50',
};
const plan: BoardWave = {
  id: 'plan',
  version: 2,
  number: 'Плановая Pharmaffiliates',
  kind: 'plan',
  status: 'planned',
  route: 'Индия-РФ',
  supplier_name: 'Pharmaffiliates Pvt Ltd',
  plan_number: null,
  planned_quantity: '300',
  requests: 2,
  hidden_requests: 0,
  requests_without_proposal: 1,
  proposals: 1,
  quantity: '0',
  offered_quantity: '120',
  summary,
  close_date: '2026-12-14',
  departure_date: '2026-12-21',
  arrival_date: '2026-12-28',
  close_week: 51,
  departure_week: 52,
  arrival_week: 53,
};
const work: BoardWave = {
  ...plan,
  id: 'work',
  number: 'Asnail W44',
  kind: 'work',
  status: 'assembling',
  supplier_name: 'Beijing Asnail Biotechnology Co, Ltd.',
  plan_number: 'Плановая Аснаил',
  planned_quantity: null,
  requests: 1,
  requests_without_proposal: 0,
  quantity: '40',
  offered_quantity: '0',
  summary: {
    ...summary,
    sales: '800000.00',
    sale_net: '655737.70',
    logistics: '28000.00',
    accepted_quantity: '40',
    accepted_sales: '800000.00',
    offered_quantity: '0',
    offered_sales: '0.00',
    logistics_per_unit: '700.00',
    profit: '-1000.00',
    profitability_percent: '-0.15',
    offered_profitability_percent: null,
  },
  close_date: '2026-10-12',
  departure_date: '2026-10-26',
  arrival_date: '2026-10-26',
  close_week: 42,
  departure_week: 44,
  arrival_week: 44,
};
const board = {
  items: [work, plan],
  basis: 'Принятые позиции считаются в волне, где они распределены.',
  amounts: true,
  profitability: true,
};

function card(name: string) {
  return screen.getByRole('button', { name }).closest('article')!;
}

it('lays waves out by month in two lanes with sales, profitability, pieces and logistics', async () => {
  vi.mocked(api).mockResolvedValue(board);
  const open = vi.fn();
  render(<WaveBoard onOpen={open} />);
  expect(await screen.findByText('Октябрь 2026')).toBeVisible();
  expect(screen.getByText('Декабрь 2026')).toBeVisible();
  expect(screen.getByLabelText('Ноябрь 2026: волн нет')).toBeVisible();
  expect(screen.getByText('Плановые')).toBeVisible();
  expect(screen.getByText('Рабочие')).toBeVisible();

  // Business remark of 19 October 2026: sales are the real, accepted volume; proposals stand apart.
  const planned = card('Pharmaffiliates Pvt Ltd');
  expect(within(planned).getByText('0 ₽')).toBeVisible();
  expect(within(planned).getByText('В КП, не приняты')).toBeVisible();
  expect(within(planned).getByText('2,4 млн ₽')).toBeVisible();
  expect(within(planned).getByText(/· 120 шт\./)).toBeVisible();
  expect(within(planned).getByText(/из плана 300/)).toBeVisible();
  expect(within(planned).getByText('Прибытие: 28 декабря · нед. 53')).toBeVisible();
  expect(within(planned).getByText('Заявок: 2 · без КП: 1')).toBeVisible();

  const working = card('Beijing Asnail Biotechnology Co, Ltd.');
  expect(within(working).getByText('Комплектуется')).toBeVisible();
  expect(within(working).getByText('800 000 ₽')).toBeVisible();
  expect(within(working).queryByText('В КП, не приняты')).not.toBeInTheDocument();
  expect(within(working).getByText('-0,15 %').closest('div')).toHaveClass('loss');

  fireEvent.click(within(working).getByText('Asnail W44'));
  expect(open).toHaveBeenCalledWith('work');
});

it('counts the planned rest of an assembling wave and shows the accepted positions alone apart', async () => {
  // «Аозеал 4 — рабочая 1»: 3 pieces accepted, 9 more planned share the freight, as in the proposal.
  const assembling: BoardWave = {
    ...work,
    quantity: '3',
    summary: {
      ...work.summary!,
      profit: '28870.79',
      profitability_percent: '20.73',
      planned_rest_quantity: '9',
      accepted_only_profitability_percent: '-27.19',
    },
  };
  vi.mocked(api).mockResolvedValue({ ...board, items: [assembling] });
  render(<WaveBoard onOpen={vi.fn()} />);
  await screen.findByText('Октябрь 2026');
  const working = card('Beijing Asnail Biotechnology Co, Ltd.');
  expect(within(working).getByText('20,73 %').closest('div')).not.toHaveClass('loss');
  expect(within(working).getByText(/\+ 9 по плану/)).toBeVisible();
  expect(within(working).getByText('Если отправить только принятые')).toBeVisible();
  expect(within(working).getByText('-27,19 %').closest('div')).toHaveClass('loss');
});

it('shows one lane in detail and places waves by the chosen date', async () => {
  vi.mocked(api).mockResolvedValue(board);
  render(<WaveBoard onOpen={vi.fn()} />);
  await screen.findByText('Октябрь 2026');
  fireEvent.click(screen.getByRole('button', { name: 'Рабочие подробно' }));
  expect(screen.queryByText('Pharmaffiliates Pvt Ltd')).not.toBeInTheDocument();
  const working = card('Beijing Asnail Biotechnology Co, Ltd.');
  expect(within(working).getByText('Логистика всего')).toBeVisible();
  expect(within(working).getByText('Индия-РФ')).toBeVisible();
  expect(within(working).getByText('Плановая Аснаил')).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: 'Обе ленты' }));
  fireEvent.change(screen.getByLabelText(/Расставить по/), { target: { value: 'close' } });
  expect(
    within(card('Pharmaffiliates Pvt Ltd')).getByText('Приём до: 14 декабря · нед. 51'),
  ).toBeVisible();
});

it('leaves money out for a role without finance rights', async () => {
  vi.mocked(api).mockResolvedValue({
    ...board,
    amounts: false,
    profitability: false,
    items: [{ ...plan, summary: null }],
  });
  render(<WaveBoard onOpen={vi.fn()} />);
  const planned = (await screen.findByRole('button', { name: 'Pharmaffiliates Pvt Ltd' })).closest(
    'article',
  )!;
  expect(within(planned).queryByText('Продажи')).not.toBeInTheDocument();
  expect(within(planned).queryByText('Рентабельность')).not.toBeInTheDocument();
  expect(within(planned).getByText('Штук')).toBeVisible();
});

it('opens the board by default and the wave from a card', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.startsWith('/waves/board')) return board;
    if (path === '/waves/plan')
      return { ...plan, forecasts: [], working_waves: [], allocations: [], archived: false };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <Waves />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Плановая Pharmaffiliates'));
  expect(
    await screen.findByRole('heading', { name: 'Волна Плановая Pharmaffiliates' }),
  ).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
  fireEvent.click(screen.getByRole('button', { name: 'Таблица' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(expect.stringMatching(/^\/waves\?page=/), expect.anything()),
  );
  expect(localStorage.getItem('waves:view')).toBe('table');
});

it('writes large amounts in millions and the rest in whole rubles', () => {
  expect(rubles('2400000.00')).toBe('2,4 млн ₽');
  expect(rubles('950.40')).toBe('950 ₽');
  expect(rubles(null)).toBe('—');
});
