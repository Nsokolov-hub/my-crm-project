import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { Waves } from '../pages/Fulfillment';
import { RequestCalculations } from '../pages/RequestCalculations';

vi.mock('../app/Auth', () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  // These tests work with the table of waves; the board is covered in wave-board.test.tsx.
  localStorage.setItem('waves:view', 'table');
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

const quote = {
  id: 'quote',
  nomenclature_name: 'Буфер',
  manufacturer: 'Завод',
  supplier_name: 'Поставщик',
  quantity: '4.000000',
  request_quantity: '4.000000',
  currency_code: 'RUB',
  product_group_slug: 'standards',
  unit_price: '100',
  delivery_days: 14,
  calculation_type: 'RUSSIA',
};

type Body = { selections: { quantity: string; bonus_coefficient?: string }[] };

function mockCalculator() {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.endsWith('/preview')) {
      const [selection] = (options?.body as Body).selections;
      const quantity = Number(selection.quantity);
      const bonus = (quantity * 250 * (Number(selection.bonus_coefficient || '1') - 1)).toFixed(2);
      return {
        snapshot: {
          lines: [
            {
              quote_item_id: 'quote',
              quantity: String(quantity),
              unit_price: '305',
              total: String(quantity * 305),
              detail: { internal_bonus: bonus, profit: '0', profitability_percent: '10' },
            },
          ],
          totals: { total: String(quantity * 305), internal_bonus: bonus },
        },
      };
    }
    if (path.includes('/quote-items')) return { items: [quote] };
    if (path.startsWith('/profiles'))
      return {
        items: [
          {
            id: 'profile',
            name: 'Профиль',
            status: 'published',
            definition: { methodology: 'itemized_v2', default_expenses: [], exchange_rates: [] },
          },
        ],
      };
    if (path.endsWith('/wave-expenses')) return { expenses: [] };
    return { items: [] };
  });
}

it('shows the bonus in rubles under its coefficient once the bonus is switched on', async () => {
  mockCalculator();
  render(
    <MemoryRouter>
      <RequestCalculations request={{ id: 'request', version: 1 }} launchQuoteItemIds={['quote']} />
    </MemoryRouter>,
  );
  fireEvent.change(await screen.findByLabelText('Профиль расчёта'), {
    target: { value: 'profile' },
  });
  const quantity = screen.getByLabelText('Количество Буфер');
  expect(quantity).toHaveValue('4');
  const coefficient = screen.getByLabelText('Бонус Буфер');
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(expect.stringMatching(/preview$/), expect.anything()),
  );
  const cell = coefficient.closest('td')!;
  expect(within(cell).queryByText(/₽/)).not.toBeInTheDocument();
  expect(screen.queryByText('Бонус:', { exact: false })).not.toBeInTheDocument();

  fireEvent.change(coefficient, { target: { value: '1,14' } });
  expect(coefficient).toHaveValue('1.14');
  expect(await within(cell).findByText('140,00 ₽')).toHaveAttribute(
    'title',
    'Бонус за 4 шт.: 35,00 ₽ за штуку',
  );
  expect(screen.getByText('Бонус:', { exact: false }).textContent).toContain('140,00 ₽');
});

const plan = {
  id: 'plan',
  version: 3,
  kind: 'plan',
  number: 'Поставщик 1',
  supplier_name: 'Поставщик',
  route: 'Мумбаи — Москва',
  status: 'planned',
  archived: false,
  close_week: 50,
  close_year: 2027,
  departure_week: 51,
  departure_year: 2027,
  arrival_week: 52,
  arrival_year: 2027,
  forecasts: [
    {
      id: 'forecast',
      product_group_name: 'Реактивы',
      target_quantity: '10',
      remaining_quantity: '7',
      filled_quantity: '3',
      active: true,
    },
  ],
  working_waves: [
    { id: 'work', number: 'Поставщик 1 — рабочая 1', status: 'assembling', archived: false },
  ],
  allocations: [],
};

it('shows a plan with its working waves, without shipping stages, and archives it with a reason', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/waves/plan/archive') return { ...plan, archived: true };
    if (path.startsWith('/waves?archived=true')) return { items: [], total: 0 };
    if (path.startsWith('/waves')) return { items: [plan], total: 1 };
    return { items: [], method: options?.method };
  });
  render(
    <MemoryRouter>
      <Waves />
    </MemoryRouter>,
  );
  const row = (await screen.findByText('Поставщик 1')).closest('tr')!;
  expect(within(row).getByText('Плановая')).toBeVisible();
  fireEvent.click(within(row).getByText('Поставщик 1'));
  expect(screen.getByRole('heading', { name: 'Рабочие волны' })).toBeVisible();
  expect(screen.getByText('Поставщик 1 — рабочая 1')).toBeVisible();
  expect(screen.getByRole('columnheader', { name: 'В рабочей волне, шт.' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Распределённые позиции' })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Изменить сроки' }));
  expect(screen.queryByLabelText(/Состояние/)).not.toBeInTheDocument();
  expect(screen.getByLabelText(/Неделя отправки/)).toHaveValue(51);
  fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));

  fireEvent.click(screen.getByRole('button', { name: 'В архив' }));
  fireEvent.change(screen.getByLabelText(/Причина/), {
    target: { value: 'Новый план поставщика' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/waves/plan/archive',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          version: 3,
          archived: true,
          reason: 'Новый план поставщика',
        }),
      }),
    ),
  );

  fireEvent.click(screen.getByRole('button', { name: 'Архив' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      expect.stringMatching(/^\/waves\?archived=true&/),
      expect.anything(),
    ),
  );
  expect(screen.queryByRole('button', { name: /Новая волна/ })).not.toBeInTheDocument();
});

it('creates a planned wave by default', async () => {
  vi.mocked(api).mockResolvedValue({ items: [], total: 0 });
  render(
    <MemoryRouter>
      <Waves />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: /Новая волна/ }));
  expect(screen.getByLabelText(/Вид волны/)).toHaveValue('plan');
});

it('offers open waves grouped by kind and flags an archived wave the request still names', async () => {
  const work = { ...plan, id: 'work', kind: 'work', number: 'Поставщик 1 — рабочая 1' };
  const archived = { ...plan, id: 'old', number: 'Поставщик старый', archived: true };
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/waves?open=true') return { items: [plan, work], total: 2 };
    if (path === '/waves/old') return archived;
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <RequestCalculations request={{ id: 'request', version: 1, wave_id: 'old' }} />
    </MemoryRouter>,
  );
  const picker = screen.getByLabelText('Волна поставки');
  await waitFor(() => expect(picker).toHaveValue('old'));
  expect(within(picker).getByRole('option', { name: /Поставщик старый · в архиве/ })).toBeVisible();
  const groups = within(picker).getAllByRole('group');
  expect(groups.map((group) => group.getAttribute('label'))).toEqual(['Плановые', 'Рабочие']);
  expect(within(groups[1]).getByRole('option', { name: /рабочая 1/ })).toBeVisible();
  expect(screen.getByText(/Назначенная волна в архиве/)).toBeVisible();
});
