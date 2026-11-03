import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { queryClient } from '../lib/queryClient';
import { RequestCalculations } from '../pages/RequestCalculations';
import { Requests } from '../pages/Requests';

// Business remarks of 3 November 2026: extra freight of a quote, the columns fee of one calculation and the
// columns of the requests list.
vi.mock('../app/Auth', () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

const columnsQuote = {
  id: 'quote',
  quote_sheet_id: 'sheet',
  quote_number: 'Q-000042',
  nomenclature_name: 'Колонка C18',
  supplier_name: 'Agilent',
  quantity: '1.000000',
  request_packings: '5',
  currency_code: 'USD',
  product_group_id: 'columns-id',
  product_group_slug: 'columns',
  product_group_name: 'Колонки',
  unit_price: '100',
  delivery_days: 14,
  calculation_type: 'IMPORT',
  extra_freight: { amount: '1500', currency: 'USD', comment: 'Большой объём' },
};

type Body = { customs_fees?: unknown; customs_fee_comment?: string; expenses?: { name: string }[] };

function mockCalculator() {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.endsWith('/preview'))
      return {
        snapshot: {
          lines: [{ quote_item_id: 'quote', quantity: '5', detail: {} }],
          totals: { total: '1000' },
          wave_distribution: { total_quantity: '5', digest: 'c'.repeat(64) },
        },
      };
    if (path.includes('/quote-items')) return { items: [columnsQuote] };
    if (path.startsWith('/profiles'))
      return {
        items: [
          {
            id: 'profile',
            name: 'Профиль',
            status: 'published',
            definition: {
              methodology: 'itemized_v2',
              default_expenses: [],
              exchange_rates: [
                {
                  currency: 'USD',
                  management_per_unit: '90',
                  quoted_units: '1',
                  date: '2026-11-03',
                  source: 'ЦБ',
                },
              ],
              customs_fee_overrides: [{ product_group_slug: 'columns', amount: '73800' }],
            },
          },
        ],
      };
    if (path.endsWith('/wave-expenses'))
      return { expenses: [], complete_budget: true, wave_version: 1, planned_volume: [] };
    return { items: [] };
  });
}

const previews = () =>
  vi
    .mocked(api)
    .mock.calls.filter(([path]) => String(path).endsWith('/preview'))
    .map(([, options]) => options?.body as Body);

function openCalculation() {
  render(
    <MemoryRouter>
      <RequestCalculations
        request={{ id: 'request', version: 1, wave_id: 'plan' }}
        launchQuoteItemIds={['quote']}
      />
    </MemoryRouter>,
  );
}

it('shows the extra freight of the selected quote at the side and adds it to the expenses', async () => {
  mockCalculator();
  openCalculation();
  const note = await screen.findByRole('complementary', { name: 'Экстрафрахт' });
  expect(note).toHaveTextContent('Agilent отгрузит квоту Q-000042 экстрафрахтом');
  expect(note).toHaveTextContent('Рекомендуем заложить: 1 500 USD');
  expect(note).toHaveTextContent('Большой объём');
  fireEvent.change(await screen.findByLabelText('Профиль расчёта'), {
    target: { value: 'profile' },
  });
  await waitFor(() => expect(previews()).toHaveLength(1));
  fireEvent.click(within(note).getByRole('button', { name: 'Добавить в расходы сделки' }));
  expect(within(note).getByRole('button', { name: 'Добавлено в расходы' })).toBeDisabled();
  await waitFor(() => expect(previews()).toHaveLength(2));
  expect(previews()[1].expenses).toContainEqual(
    expect.objectContaining({
      name: 'Экстрафрахт — Agilent (Q-000042)',
      amount: '1500',
      currency: 'USD',
      stage: 'INTERNATIONAL_LOGISTICS',
      scope: 'REQUEST',
    }),
  );
  // The note can be folded away; it never closes the calculation.
  fireEvent.click(within(note).getByRole('button', { name: /Экстрафрахт/ }));
  expect(within(note).queryByText(/Рекомендуем заложить/)).not.toBeInTheDocument();
});

it('changes the columns fee of one calculation only with a comment', async () => {
  mockCalculator();
  openCalculation();
  fireEvent.change(await screen.findByLabelText('Профиль расчёта'), {
    target: { value: 'profile' },
  });
  const fee = await screen.findByLabelText('Таможенный сбор: Колонки');
  expect(fee).toHaveValue('73800');

  await waitFor(() => expect(previews()).toHaveLength(1));
  expect(previews()[0].customs_fees).toBeUndefined();

  fireEvent.change(fee, { target: { value: '0' } });
  await new Promise((resolve) => setTimeout(resolve, 600));
  expect(previews()).toHaveLength(1);
  fireEvent.change(screen.getByLabelText(/Комментарий к сбору/), {
    target: { value: 'Сбор оплачен клиентом ноябрьского заказа' },
  });
  await waitFor(() => expect(previews()).toHaveLength(2));
  expect(previews()[1]).toMatchObject({
    customs_fees: [{ product_group_id: 'columns-id', amount: '0' }],
    customs_fee_comment: 'Сбор оплачен клиентом ноябрьского заказа',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Вернуть сбор профиля' }));
  expect(fee).toHaveValue('73800');
});

it('shows the saved columns of the requests list and saves a change', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/me/preferences/requests.columns' && !options?.method)
      return { value: { columns: 'number,wave_number' } };
    if (path.startsWith('/requests?'))
      return {
        items: [
          {
            id: 'r1',
            number: '124',
            title: 'CTAB',
            client_id: 'c',
            commercial_stage: 'new',
            wave_number: 'PanReac — рабочая 2',
            contact_name: 'Анна',
          },
        ],
        total: 1,
      };
    if (path.startsWith('/me/preferences')) return { value: null };
    return { items: [] };
  });
  render(
    <MemoryRouter>
      <Requests />
    </MemoryRouter>,
  );
  expect(await screen.findByText('PanReac — рабочая 2')).toBeInTheDocument();
  const headers = () => screen.getAllByRole('columnheader').map((cell) => cell.textContent);
  await waitFor(() => expect(headers()).toEqual(['Заявка↕', 'Волна']));

  fireEvent.click(screen.getByRole('button', { name: /Столбцы/ }));
  fireEvent.click(screen.getByLabelText('Контактное лицо'));
  fireEvent.click(screen.getByLabelText('Ответственный'));
  expect(headers()).toEqual(['Заявка↕', 'Волна', 'Контакт', 'Ответственный']);
  // Both clicks are saved in one request with the latest choice.
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith('/me/preferences/requests.columns', {
      method: 'PUT',
      body: { value: { columns: 'number,wave_number,contact_name,owner_name' } },
    }),
  );
  expect(vi.mocked(api).mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(
    1,
  );
});
