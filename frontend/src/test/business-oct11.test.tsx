import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { RequestCalculations } from '../pages/RequestCalculations';

vi.mock('../app/Auth', () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
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
