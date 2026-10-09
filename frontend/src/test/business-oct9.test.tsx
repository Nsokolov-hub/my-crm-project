import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { RequestCalculations } from '../pages/RequestCalculations';
import { RequestDocuments } from '../pages/RequestDocuments';
import { RequestQuotes } from '../pages/RequestProcurement';
import { Waves } from '../pages/Fulfillment';

let canProfit = true;
vi.mock('../app/Auth', () => ({
  useAuth: () => ({ can: (code: string) => code !== 'finance.profit.read' || canProfit }),
}));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  canProfit = true;
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

const quote = {
  id: 'quote',
  nomenclature_name: 'Образец',
  manufacturer: 'Завод образцов',
  article: 'A123',
  supplier_name: 'Поставщик образцов',
  quantity: '2',
  request_quantity: '2',
  currency_code: 'RUB',
  product_group_slug: 'standards',
  unit_price: '100',
  delivery_days: 14,
  calculation_type: 'RUSSIA',
};

it('shows article and manufacturer as quote columns', async () => {
  vi.mocked(api).mockResolvedValue({ items: [quote], total: 1 });
  render(<RequestQuotes requestId="request" onCalculate={vi.fn()} />);
  expect(await screen.findByRole('columnheader', { name: /Артикул/ })).toBeVisible();
  expect(screen.getByRole('columnheader', { name: /Производитель/ })).toBeVisible();
  expect(await screen.findByText('A123')).toBeVisible();
  expect(screen.getByText('Завод образцов')).toBeVisible();
});

function mockCalculator() {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.endsWith('/preview')) {
      const quantity = Number(
        (options?.body as { selections: { quantity: string }[] }).selections[0].quantity,
      );
      return {
        snapshot: {
          lines: [
            {
              quote_item_id: 'quote',
              quantity: String(quantity),
              unit_price: '305',
              total: String(quantity * 305),
              detail: {
                profit: String(quantity * 50),
                profitability_percent: '20',
                cost_profitability_percent: '25',
              },
            },
          ],
          totals: {
            total: String(quantity * 305),
            profit: String(quantity * 50),
            profitability_percent: '20',
            cost_profitability_percent: '25',
          },
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

it('shows supplier, manufacturer, unit profit and total profitability next to quantity and updates totals', async () => {
  mockCalculator();
  render(
    <MemoryRouter>
      <RequestCalculations request={{ id: 'request', version: 1 }} launchQuoteItemIds={['quote']} />
    </MemoryRouter>,
  );
  fireEvent.change(await screen.findByLabelText('Профиль расчёта'), {
    target: { value: 'profile' },
  });
  await screen.findByText('50,00 ₽ / шт.');
  const table = screen.getByRole('columnheader', { name: 'Поставщик' }).closest('table')!;
  expect(within(table).getByText('Поставщик образцов')).toBeVisible();
  expect(within(table).getByText('Завод образцов')).toHaveClass('calculation-manufacturer');
  expect(within(table).getByText('20,00 %')).toBeVisible();
  expect(screen.getByText('Рентабельность расчёта:', { exact: false }).textContent).toContain(
    '20,00 %',
  );
  expect(screen.queryByText(/откат/i)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Количество Образец'), { target: { value: '3' } });
  await waitFor(() => expect(within(table).getByText('915,00')).toBeVisible());
  expect(within(table).getByText('50,00 ₽ / шт.')).toBeVisible();
});

it('does not display profitability columns without profit access', async () => {
  canProfit = false;
  mockCalculator();
  render(
    <MemoryRouter>
      <RequestCalculations request={{ id: 'request', version: 1 }} launchQuoteItemIds={['quote']} />
    </MemoryRouter>,
  );
  await screen.findByText('Образец');
  expect(screen.queryByRole('columnheader', { name: 'Рентабельность' })).not.toBeInTheDocument();
  expect(screen.queryByText('Рентабельность расчёта:', { exact: false })).not.toBeInTheDocument();
});

it('refreshes the parent request after issuing a proposal and selects by timestamp', async () => {
  const onChanged = vi.fn();
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === 'POST') return { id: 'proposal' };
    if (path.includes('/calculations'))
      return { items: [{ id: 'calc', selection_label: '№2 · 09.10.2026 14:54' }] };
    return { items: [] };
  });
  render(
    <MemoryRouter>
      <RequestDocuments requestId="request" onChanged={onChanged} />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Выпустить КП' }));
  fireEvent.focus(screen.getByLabelText(/^Расчёт/));
  fireEvent.click(await screen.findByRole('option', { name: '№2 · 09.10.2026 14:54' }));
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
});

it('displays remaining wave plan separately from initial target and committed orders', async () => {
  vi.mocked(api).mockResolvedValue({
    items: [
      {
        id: 'wave',
        number: 'Волна 1',
        status: 'assembling',
        forecasts: [
          {
            id: 'forecast',
            product_group_name: 'Стандарты',
            target_quantity: '10',
            remaining_quantity: '7',
            filled_quantity: '3',
            active: true,
          },
        ],
      },
    ],
    total: 1,
  });
  render(
    <MemoryRouter>
      <Waves />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Волна 1'));
  const header = screen.getByRole('columnheader', { name: 'План, шт.' });
  const table = header.closest('table')!;
  expect(within(table).getByText('7')).toBeVisible();
  expect(within(table).getByText('10')).toBeVisible();
  expect(within(table).getByText('3')).toBeVisible();
});
