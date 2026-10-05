import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { waveWeekInitial, waveWeekLabel } from '../lib/waves';
import { Waves } from '../pages/Fulfillment';
import { RequestCalculations } from '../pages/RequestCalculations';

vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
}));
vi.mock('../app/Auth', () => ({
  useAuth: () => ({ can: () => true, session: { user: { id: 'owner' } } }),
}));
beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

it('keeps ISO week years correct for legacy schedules at New Year', () => {
  const legacy = {
    id: 'wave',
    close_date: '2021-01-01',
    departure_date: '2021-01-04',
    arrival_date: '2021-01-11',
  };
  expect(waveWeekLabel(legacy, 'close')).toBe('Неделя 53, 2020');
  expect(waveWeekInitial(legacy)).toEqual({
    close_week: 53,
    close_year: 2020,
    departure_week: 1,
    departure_year: 2021,
    arrival_week: 2,
    arrival_year: 2021,
  });
  expect(waveWeekLabel({ id: 'wave', departure_week: 35, departure_year: 2026 }, 'departure')).toBe(
    'Неделя 35, 2026',
  );
});

it('sends the selected VAT deduction mode and common delivery term in preview and saved calculation', async () => {
  const quote = {
    id: 'quote',
    nomenclature_name: 'Стандарт',
    quantity: '2',
    currency_code: 'RUB',
    product_group_slug: 'standards',
    unit_price: '100',
    delivery_days: 14,
  };
  const snapshot = {
    wave: { financial_digest: 'a'.repeat(64) },
    wave_distribution: { digest: 'a'.repeat(64) },
    lines: [
      {
        line_id: 'line',
        description: 'Стандарт',
        quantity: '2',
        total: '300',
        detail: {
          import_vat: '44',
          customs_fee: '20',
          import_vat_base: '200',
          import_cost_with_vat: '244',
          profit: '100',
          profitability_percent: '33.33',
          margin_percent: '33.33',
          cost_profitability_percent: '50',
        },
      },
    ],
    totals: {
      import_vat: '44',
      total: '300',
      import_vat_base: '200',
      profit: '100',
      profitability_percent: '33.33',
      margin_percent: '33.33',
      cost_profitability_percent: '50',
    },
    base_version_id: null,
  };
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === 'POST')
      return path.endsWith('/preview') ? { snapshot } : { id: 'saved', snapshot };
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
    if (path.endsWith('/wave-expenses')) return { expenses: [], source_calculation_id: null };
    return { items: [] };
  });
  render(
    <MemoryRouter>
      <RequestCalculations
        request={{ id: 'request', number: '1', version: 1, wave_id: 'wave' }}
        launchQuoteItemIds={['quote']}
      />
    </MemoryRouter>,
  );
  await screen.findByRole('option', { name: /Профиль/ });
  fireEvent.change(screen.getByLabelText('Профиль расчёта'), {
    target: { value: 'profile' },
  });
  expect(screen.getByLabelText('Расчёт с вычетом НДС')).toBeChecked();
  fireEvent.change(screen.getByLabelText('Общий срок поставки, дней'), { target: { value: '35' } });
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/calculations/preview',
      expect.objectContaining({
        body: expect.objectContaining({ vat_deductible: true, delivery_days: 35 }),
      }),
    ),
  );
  expect(screen.getAllByText('Ввозной НДС, ₽').length).toBeGreaterThan(0);
  expect(screen.getByText('Валовая прибыль, ₽ ⓘ')).toHaveAttribute(
    'title',
    expect.stringContaining('Продажа без НДС'),
  );
  expect(screen.getByText('Рентабельность, % ⓘ')).toHaveAttribute(
    'title',
    'Валовая прибыль / продажа без НДС × 100%',
  );
  expect(screen.getByText('Доходность затрат, % ⓘ')).toHaveAttribute(
    'title',
    'Валовая прибыль / себестоимость × 100%',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Показать детали' }));
  expect(screen.getAllByText('База ввозного НДС (ННБ), ₽ ⓘ')).toHaveLength(2);
  expect(screen.getByText('Таможенная стоимость с пошлиной и ввозным НДС, ₽ ⓘ')).toHaveAttribute(
    'title',
    'ННБ + ввозной НДС',
  );
  expect(screen.queryByText('Маржа, %')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Записать версию' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/calculations',
      expect.objectContaining({
        body: expect.objectContaining({
          vat_deductible: true,
          delivery_days: 35,
          expected_wave_digest: 'a'.repeat(64),
        }),
      }),
    ),
  );
});

it('refreshes shared wave costs in a new version while retaining request costs and automatic delivery', async () => {
  const shared = {
    name: 'Логистика',
    amount: '150',
    currency: 'RUB',
    method: 'BY_QUANTITY',
    basis: 'Доставка',
    scope: 'WAVE',
    stage: 'INTERNATIONAL_LOGISTICS',
  };
  const own = {
    name: 'Доставка клиенту',
    amount: '7',
    currency: 'RUB',
    method: 'BY_QUANTITY',
    basis: 'Доставка',
    scope: 'REQUEST',
  };
  const previous = {
    id: 'old',
    version_number: 1,
    profile_id: 'profile',
    wave_stale: true,
    snapshot: {
      delivery_days: 14,
      input: {
        profile_id: 'profile',
        delivery_days: null,
        selections: [{ quote_item_id: 'quote', markup_coefficient: '1.5' }],
        expenses: [{ ...shared, amount: '100' }, own],
      },
      lines: [{ quote_item_id: 'quote' }],
    },
  };
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === 'POST')
      return { snapshot: { lines: [], totals: {}, wave: { financial_digest: 'b'.repeat(64) } } };
    if (path.split('?')[0].endsWith('/calculations')) return { items: [previous] };
    if (path.endsWith('/wave-expenses'))
      return { expenses: [shared], source_calculation_id: 'peer-new' };
    if (path.includes('/quote-items'))
      return {
        items: [
          {
            id: 'quote',
            nomenclature_name: 'Стандарт',
            quantity: '2',
            currency_code: 'RUB',
            product_group_slug: 'standards',
            unit_price: '100',
            delivery_days: 30,
          },
        ],
      };
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
    return { items: [] };
  });
  render(
    <MemoryRouter>
      <RequestCalculations request={{ id: 'request', version: 1, wave_id: 'wave' }} />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Версия №1'));
  expect(screen.getByText(/Состав или расходы волны изменились/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Создать новую версию' }));
  expect(await screen.findByLabelText('Общий срок поставки, дней')).toHaveValue(30);
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/calculations/preview',
      expect.objectContaining({
        body: expect.objectContaining({
          expenses: expect.arrayContaining([
            expect.objectContaining({ name: 'Логистика', amount: '150' }),
            expect.objectContaining({ name: 'Доставка клиенту', amount: '7' }),
          ]),
        }),
      }),
    ),
  );
  const body = vi
    .mocked(api)
    .mock.calls.find(([path]) => path.endsWith('/calculations/preview'))?.[1]?.body;
  expect(body).not.toHaveProperty('delivery_days');
});

it('edits waves using week numbers and displays recalculated costs for all orders', async () => {
  const wave = {
    id: 'wave',
    number: 'Поставщик 1',
    version: 2,
    status: 'planned',
    route: 'Москва',
    close_week: 35,
    close_year: 2026,
    departure_week: 36,
    departure_year: 2026,
    arrival_week: 37,
    arrival_year: 2026,
    allocations: [],
    financial_summary: {
      status: 'current',
      total_quantity: '3',
      customs_value: '900',
      customs_fee: '60',
      import_vat: '200',
      expenses_total: '160',
      allocations: [
        {
          request_number: '1',
          description: 'Заказ первый',
          quantity: '1',
          customs_fee: '20',
          import_vat: '60',
          expenses_total: '50',
        },
        {
          request_number: '2',
          description: 'Заказ второй',
          quantity: '2',
          customs_fee: '40',
          import_vat: '140',
          expenses_total: '110',
        },
      ],
    },
  };
  vi.mocked(api).mockImplementation(async (_path, options) =>
    options?.method === 'PATCH' ? wave : { items: [wave] },
  );
  render(
    <MemoryRouter>
      <Waves />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Поставщик 1'));
  expect(screen.getByText('Заказ первый')).toBeVisible();
  expect(screen.getByText('Заказ второй')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Изменить сроки и состояние' }));
  expect(screen.getByLabelText(/Неделя закрытия/)).toHaveValue(35);
  fireEvent.change(screen.getByLabelText(/Неделя закрытия/), { target: { value: '34' } });
  fireEvent.change(screen.getByLabelText(/Причина изменения/), {
    target: { value: 'Уточнили неделю' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/waves/wave',
      expect.objectContaining({
        method: 'PATCH',
        body: expect.objectContaining({
          close_week: 34,
          close_year: 2026,
          departure_week: 36,
          departure_year: 2026,
          arrival_week: 37,
          arrival_year: 2026,
        }),
      }),
    ),
  );
  const body = vi
    .mocked(api)
    .mock.calls.find(
      ([path, options]) => path === '/waves/wave' && options?.method === 'PATCH',
    )?.[1]?.body;
  expect(body).not.toHaveProperty('close_date');
});
