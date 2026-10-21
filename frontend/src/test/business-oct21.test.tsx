import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { useAuth } from '../app/Auth';
import { api, download } from '../lib/api';
import { RequestCalculations } from '../pages/RequestCalculations';
import { Requests } from '../pages/Requests';
import { SupplierPricesReport } from '../pages/SupplierPrices';

vi.mock('../app/Auth', () => ({ useAuth: vi.fn() }));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
  download: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(useAuth).mockReturnValue({
    can: () => true,
  } as unknown as ReturnType<typeof useAuth>);
  vi.mocked(download).mockResolvedValue(undefined);
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

it('shows how many suppliers gave a quote out of those asked', async () => {
  vi.mocked(api).mockResolvedValue({
    items: [
      {
        id: 'r59',
        number: '59',
        title: 'Пик-Фарма / TRC',
        client_name: 'ООО "ПИК-ФАРМА"',
        commercial_stage: 'quoted',
        owner_name: 'Тимур',
        quoted_suppliers: 3,
        asked_suppliers: 4,
      },
      {
        id: 'r69',
        number: '69',
        title: 'Пик Фарма / Оригиналы',
        client_name: 'ООО "ПИК-ФАРМА"',
        commercial_stage: 'new',
        owner_name: 'Тимур',
        quoted_suppliers: 0,
        asked_suppliers: 0,
      },
    ],
    total: 2,
  });
  render(
    <MemoryRouter>
      <Requests />
    </MemoryRouter>,
  );
  expect(await screen.findByText('КП поставщиков')).toBeVisible();
  expect(screen.getByText('3 из 4')).toBeVisible();
  const column = screen
    .getAllByRole('columnheader')
    .findIndex((header) => header.textContent?.includes('КП поставщиков'));
  const quiet = screen.getByText('Пик Фарма / Оригиналы').closest('tr')!;
  expect(within(quiet).getAllByRole('cell')[column]).toHaveTextContent(/^—$/);
});

function mockCalculation() {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.includes('/quote-items'))
      return {
        items: [
          {
            id: 'quote',
            nomenclature_name: 'Avilamycin A',
            packing_name: '250 mg',
            currency_code: 'CNY',
            unit_price: '4675',
            calculation_type: 'IMPORT',
            request_packings: '5',
          },
        ],
      };
    return { items: [], total: 0 };
  });
}

it('takes the quantity of the request position into the calculation', async () => {
  mockCalculation();
  render(
    <MemoryRouter>
      <RequestCalculations request={{ id: 'request', version: 1 }} launchQuoteItemIds={['quote']} />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByLabelText(/Количество Avilamycin A/)).toHaveValue('5'));
  expect(screen.getByLabelText('Расчёт с вычетом НДС')).toBeEnabled();
});

it('sells to a client in Belarus with VAT 0 % and without VAT deduction', async () => {
  mockCalculation();
  render(
    <MemoryRouter>
      <RequestCalculations
        request={{ id: 'request', version: 1, client_in_belarus: true }}
        launchQuoteItemIds={['quote']}
      />
    </MemoryRouter>,
  );
  const deduction = await screen.findByLabelText('Расчёт с вычетом НДС');
  expect(deduction).toBeDisabled();
  expect(deduction).not.toBeChecked();
  expect(
    screen.getByText(/Клиент находится в Республике Беларусь: продажа с НДС 0 %/),
  ).toBeVisible();
});

it('lists supplier prices and exports the base found to Excel', async () => {
  vi.mocked(api).mockResolvedValue({
    items: [
      {
        id: 'q1',
        quoted_at: '2026-10-20',
        quote_number: 'Q-000084',
        request_id: 'r59',
        request_number: '59',
        client_name: 'ООО "ПИК-ФАРМА"',
        supplier_name: 'Beijing Asnail Biotechnology Co, Ltd.',
        nomenclature_name: 'Avilamycin A',
        article: 'TRC-A794655-250MG',
        manufacturer: 'TRC',
        packing: '250 mg',
        unit_price: '4675.00000000',
        currency: 'CNY',
        unit_price_rub: '60775.00',
        delivery_days: 42,
      },
    ],
    total: 1,
    rates_profile: 'Профиль тест',
  });
  render(
    <MemoryRouter>
      <SupplierPricesReport from="2026-01-01" to="2026-10-21" />
    </MemoryRouter>,
  );
  expect(await screen.findByText('Avilamycin A')).toBeVisible();
  expect(screen.getByText('60 775')).toBeVisible();
  expect(screen.getByText(/по курсам профиля расчёта «Профиль тест»/)).toBeVisible();

  fireEvent.change(screen.getByLabelText(/Поиск по товару/), { target: { value: 'Avilamycin' } });
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/analytics/supplier-prices?date_from=2026-01-01&date_to=2026-10-21&q=Avilamycin&page=1&page_size=100',
      expect.anything(),
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: /Выгрузить в Excel/ }));
  expect(download).toHaveBeenCalledWith(
    '/analytics/supplier-prices.xlsx?date_from=2026-01-01&date_to=2026-10-21&q=Avilamycin',
    'База цен поставщиков 2026-01-01 — 2026-10-21.xlsx',
  );
});
