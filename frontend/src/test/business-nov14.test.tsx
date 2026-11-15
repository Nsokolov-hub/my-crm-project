import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { queryClient } from '../lib/queryClient';
import { RequestCalculations } from '../pages/RequestCalculations';
import { CalculationBreakdown } from '../pages/calculations/CalculationBreakdown';

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

const quote = {
  id: 'quote',
  nomenclature_name: 'Колонка C18',
  article: 'C18-100',
  packing_name: '1 шт.',
  supplier_name: 'Sabiha',
  quantity: '5',
  request_packings: '5',
  currency_code: 'RUB',
  product_group_id: 'columns',
  product_group_slug: 'columns',
  product_group_name: 'Колонки',
  unit_price: '100',
  delivery_days: 14,
  calculation_type: 'IMPORT',
};
type Input = { selections: { sale_unit_price?: string }[]; planned_volume?: unknown };

function mockCalculator(copy = false) {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.endsWith('/preview')) {
      const input = options?.body as Input;
      const unitPrice = input.selections[0].sale_unit_price || '1500';
      return {
        snapshot: {
          lines: [
            {
              quote_item_id: 'quote',
              quantity: '5',
              unit_price: unitPrice,
              total: String(Number(unitPrice) * 5),
              detail: { profitability_percent: '20' },
            },
          ],
          totals: { total: String(Number(unitPrice) * 5), profitability_percent: '20' },
          wave_distribution: {
            total_quantity: '28',
            existing_quantity: '23',
            selected_quantity: '5',
            digest: 'c'.repeat(64),
          },
        },
      };
    }
    if (path.includes('/sale-price-history'))
      return {
        items: [
          {
            execution_id: 'old-sale',
            request_id: 'old-request',
            request_number: '196',
            article: 'C18-100',
            description: 'Колонка C18',
            packing: '1 шт.',
            manufacturer: 'Agilent',
            unit_price: '1250',
            currency: 'RUB',
            sold_at: '2026-08-14T10:00:00Z',
            price_includes_vat: true,
          },
        ],
      };
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
    if (path.includes('/wave-expenses'))
      return {
        expenses: [],
        complete_budget: true,
        wave_version: 1,
        planned_volume: [
          { product_group_id: 'columns', product_group_name: 'Колонки', wave_quantity: '10' },
        ],
      };
    if (copy && path.startsWith('/requests/request/calculations') && !options?.method)
      return {
        items: [
          {
            id: 'previous',
            version_number: 1,
            snapshot: {
              input: {
                profile_id: 'profile',
                selections: [
                  {
                    quote_item_id: 'quote',
                    quantity: '5',
                    markup_coefficient: '1.5',
                    sale_unit_price: '1250',
                  },
                ],
              },
              lines: [{ quote_item_id: 'quote' }],
            },
          },
        ],
      };
    return { items: [] };
  });
}

function openCalculation(copy = false) {
  mockCalculator(copy);
  render(
    <MemoryRouter>
      <RequestCalculations
        request={{ id: 'request', version: 1, wave_id: 'work', client_name: 'Покупатель' }}
        launchQuoteItemIds={copy ? [] : ['quote']}
      />
    </MemoryRouter>,
  );
}

const previewInputs = () =>
  vi
    .mocked(api)
    .mock.calls.filter(([path]) => path.endsWith('/preview'))
    .map(([, options]) => options?.body as Input);

async function chooseProfile() {
  fireEvent.change(await screen.findByLabelText('Профиль расчёта'), {
    target: { value: 'profile' },
  });
  await waitFor(() =>
    expect(screen.getByLabelText('Цена продажи за шт. Колонка C18')).toHaveValue('1500'),
  );
}

it('recalculates a manually set sale price, saves it and can return to the formula', async () => {
  openCalculation();
  await chooseProfile();
  const price = screen.getByLabelText('Цена продажи за шт. Колонка C18');
  fireEvent.change(price, { target: { value: '1250,50' } });
  await waitFor(() =>
    expect(previewInputs().at(-1)?.selections[0].sale_unit_price).toBe('1250.50'),
  );
  expect(screen.getByText('Задана вручную')).toBeVisible();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Записать версию' })).toBeEnabled(),
  );
  expect(screen.getAllByText('6 252,50').length).toBeGreaterThan(0);

  fireEvent.click(screen.getByRole('button', { name: 'Вернуть расчётную цену Колонка C18' }));
  await waitFor(() => expect(price).toHaveValue('1500'));
  expect(previewInputs().at(-1)?.selections[0].sale_unit_price).toBeUndefined();

  fireEvent.change(price, { target: { value: '1250' } });
  await waitFor(() => expect(previewInputs().at(-1)?.selections[0].sale_unit_price).toBe('1250'));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Записать версию' })).toBeEnabled(),
  );
  fireEvent.change(screen.getByLabelText(/Общий срок поставки/), { target: { value: '14' } });
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Записать версию' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Записать версию' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/calculations',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          selections: [expect.objectContaining({ sale_unit_price: '1250' })],
        }),
      }),
    ),
  );
});

it('rejects a zero, negative or overprecise manual price before sending the calculation', async () => {
  openCalculation();
  await chooseProfile();
  const sent = previewInputs().length;
  for (const value of ['0', '-1', '1250.501']) {
    fireEvent.change(screen.getByLabelText('Цена продажи за шт. Колонка C18'), {
      target: { value },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Предварительный расчёт' }));
    expect(screen.getByText(/Цена продажи позиции.*должна быть больше нуля/)).toBeVisible();
    expect(previewInputs()).toHaveLength(sent);
  }
});

it('shows this buyer’s previous price with its date, packing, VAT and request link', async () => {
  openCalculation();
  const history = await screen.findByRole('complementary', { name: 'История продаж клиенту' });
  expect(await within(history).findByText('C18-100')).toBeVisible();
  expect(within(history).getByText('14 авг. 2026 г.')).toBeVisible();
  expect(within(history).getByText('1 250 RUB')).toBeVisible();
  expect(within(history).getByText('с НДС')).toBeVisible();
  expect(within(history).getByRole('link', { name: '№196' })).toHaveAttribute(
    'href',
    '/requests/old-request',
  );
  expect(api).toHaveBeenCalledWith(
    '/requests/request/sale-price-history?quote_item_ids=quote',
    expect.anything(),
  );
});

it('keeps the manually set price when copying a saved calculation', async () => {
  openCalculation(true);
  fireEvent.click(await screen.findByRole('button', { name: 'Копировать' }));
  expect(await screen.findByLabelText('Цена продажи за шт. Колонка C18')).toHaveValue('1250');
  await waitFor(() => expect(previewInputs().at(-1)?.selections[0].sale_unit_price).toBe('1250'));
});

it('accepts a calculation volume below the 23 pieces already ordered in a working wave', async () => {
  openCalculation();
  await chooseProfile();
  fireEvent.change(screen.getByLabelText('Плановый объём в расчёте: Колонки'), {
    target: { value: '2' },
  });
  fireEvent.change(screen.getByLabelText(/Комментарий к плановому объёму/), {
    target: { value: 'Повторный заказ по прежней цене' },
  });
  await waitFor(() =>
    expect(previewInputs().at(-1)?.planned_volume).toEqual([
      { product_group_id: 'columns', quantity: '2' },
    ]),
  );
  // Re-entering the plan's number must keep an explicit basis, even when the working wave has grown past it.
  fireEvent.change(screen.getByLabelText('Плановый объём в расчёте: Колонки'), {
    target: { value: '10' },
  });
  await waitFor(() =>
    expect(previewInputs().at(-1)?.planned_volume).toEqual([
      { product_group_id: 'columns', quantity: '10' },
    ]),
  );
});

it('keeps the scenario basis separate from the actual number of pieces in the saved calculation', () => {
  render(
    <CalculationBreakdown
      snapshot={{
        id: 'saved',
        lines: [{ id: 'line', line_id: 'line', description: 'Колонка', quantity: '3' }],
        wave_distribution: {
          total_quantity: '1',
          offered_quantity: '3',
          selected_quantity: '0.115',
          existing_quantity: '23',
          forecast_quantity: '0',
        },
        wave: {
          planned_volume: {
            rows: [
              {
                product_group_name: 'Колонки',
                quantity: '1',
                wave_quantity: '10',
                offered_quantity: '3',
                counted_quantity: '0.115',
              },
            ],
            comment: 'Сценарий поставки',
          },
        },
      }}
    />,
  );
  expect(screen.getByText(/База распределения общих расходов волны:/)).toHaveTextContent(
    'База распределения общих расходов волны: 1 шт. В КП 3 шт.; ранее заказано 23 шт.; прогноз 0 шт.',
  );
  expect(screen.getByText(/Плановый объём изменён в расчёте:/)).toHaveTextContent(
    'в распределении учтено 0,115 из 3 шт. КП',
  );
  expect(screen.queryByText(/клиент возьмёт|шт. в этом расчёте \+/)).not.toBeInTheDocument();
});
