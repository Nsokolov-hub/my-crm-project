import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { useAuth } from '../app/Auth';
import { api, download } from '../lib/api';
import { FilesPanel } from '../pages/Communication';
import { Waves } from '../pages/Fulfillment';
import { QuickQuoteDialog } from '../pages/QuickQuote';
import { RequestCalculations } from '../pages/RequestCalculations';

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

it('takes a zip archive and downloads chosen files as one archive', async () => {
  vi.mocked(api).mockResolvedValue({
    items: [
      { id: 'zip', name: 'Сертификаты.zip', size: 2071606, status: 'clean' },
      { id: 'coa', name: 'CoA.pdf', size: 1024, status: 'clean' },
      { id: 'new', name: 'Ещё.pdf', size: 1024, status: 'quarantined' },
    ],
    total: 3,
  });
  render(<FilesPanel entityType="request" entityId="r59" archiveName="Вложения заявки №59" />);
  expect(screen.getByLabelText('Файлы для загрузки')).toHaveAttribute(
    'accept',
    expect.stringContaining('.zip'),
  );
  fireEvent.click(await screen.findByLabelText('Добавить в архив Сертификаты.zip'));
  fireEvent.click(screen.getByLabelText('Добавить в архив CoA.pdf'));
  expect(screen.getByLabelText('Добавить в архив Ещё.pdf')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /Скачать архивом \(2\)/ }));
  expect(download).toHaveBeenCalledWith(
    `/files/archive?ids=zip,coa&name=${encodeURIComponent('Вложения заявки №59')}`,
    'Вложения заявки №59.zip',
  );
});

it('quotes a product found by name with the data of its last quote', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.startsWith('/requests/r73/quote-candidates'))
      return {
        prices: true,
        items: [
          {
            nomenclature_id: 'n1',
            name: 'Tween-20',
            article: 'sc-29113',
            manufacturer: 'Santa Cruz Biotechnology',
            cas: '9005-64-5',
            product_group_name: 'Реактивы',
            packings: [
              {
                id: 'p100',
                name: '100 ml',
                last: {
                  quote_item_id: 'q1',
                  packing_id: 'p100',
                  supplier_id: 'asnail',
                  supplier_name: 'Beijing Asnail',
                  unit_price: '580.00000000',
                  currency_id: 'cny',
                  currency_code: 'CNY',
                  delivery_days: 21,
                  quoted_at: '2026-10-21T08:00:00Z',
                  quote_number: 'Q-000091',
                  request_number: '72',
                },
              },
            ],
            last: null,
            request_item_id: null,
            request_quantity: null,
          },
        ],
      };
    if (path.startsWith('/currencies')) return { items: [{ id: 'cny', code: 'CNY' }] };
    if (path.startsWith('/counterparties/asnail')) return { id: 'asnail', name: 'Beijing Asnail' };
    if (path === '/requests/r73/quote-sheets' && options?.method === 'POST')
      return { id: 'sheet', number: 'Q-000095' };
    return { items: [], total: 0 };
  });
  const saved = vi.fn();
  render(<QuickQuoteDialog requestId="r73" onClose={vi.fn()} onSaved={saved} />);
  fireEvent.change(screen.getByPlaceholderText('Например, Avilamycin'), {
    target: { value: 'Tween' },
  });
  fireEvent.click(await screen.findByRole('button', { name: /Tween-20/ }));
  expect(screen.getByLabelText('Цена за фасовку')).toHaveValue('580');
  expect(screen.getByLabelText('Срок поставки, дней')).toHaveValue(21);
  expect(screen.getByText(/Подставлено из квоты Q-000091 по заявке №72/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('Количество, фасовок'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить квоту' }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(api).toHaveBeenCalledWith(
    '/requests/r73/quote-sheets',
    expect.objectContaining({
      method: 'POST',
      body: {
        supplier_id: 'asnail',
        items: [
          {
            source_request_item_id: null,
            nomenclature_id: 'n1',
            packing_id: 'p100',
            quantity: '2',
            unit_price: '580',
            currency_id: 'cny',
            delivery_days: 21,
          },
        ],
      },
    }),
  );
});

it('copies any saved calculation into a new one', async () => {
  const saved = {
    id: 'calc-1',
    selection_label: '№1 · 20.10.2026 10:00',
    calculation_number: '73-V1',
    created_at: '2026-10-20T07:00:00Z',
    snapshot: {
      input: { profile_id: 'profile', selections: [{ quote_item_id: 'quote', quantity: '2' }] },
      lines: [{ quote_item_id: 'quote' }],
    },
  };
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.startsWith('/requests/r73/calculations')) return { items: [saved], total: 1 };
    if (path.includes('/quote-items'))
      return { items: [{ id: 'quote', nomenclature_name: 'Tween-20', currency_code: 'CNY' }] };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <RequestCalculations request={{ id: 'r73', version: 4 }} />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: /Копировать/ }));
  expect(await screen.findByRole('heading', { name: 'Копия расчёта 73-V1' })).toBeVisible();
  await waitFor(() => expect(screen.getByLabelText(/Количество Tween-20/)).toHaveValue('2'));
});

it('gathers the requests of the plan into its working wave', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  const work = {
    id: 'work',
    version: 3,
    kind: 'work',
    plan_id: 'plan',
    plan_number: 'Аозеал 4 / Плановая',
    number: 'Аозеал 4 / Плановая — рабочая 1',
    status: 'assembling',
    route: 'Китай-РФ',
    archived: false,
    forecasts: [],
    allocations: [],
    working_waves: [],
    supplier_name: 'Shenzhen Sungening Bio-Medical Co., Ltd',
    requests: 1,
    hidden_requests: 0,
    requests_without_proposal: 0,
    proposals: 0,
    quantity: '3',
    planned_quantity: null,
    summary: null,
    close_date: '2026-10-26',
    departure_date: '2026-10-28',
    arrival_date: '2026-11-09',
    close_week: 44,
    departure_week: 44,
    arrival_week: 46,
  };
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.startsWith('/waves/board'))
      return { items: [work], basis: '', amounts: false, profitability: false };
    if (path === '/waves/work') return work;
    if (path === '/waves/work/plan-requests')
      return {
        plan_id: 'plan',
        items: [
          {
            id: 'r70',
            number: '70',
            client_name: 'ООО ХимВет',
            commercial_stage: 'proposal_sent',
            calculation_number: '70-V2',
            proposals: 1,
            orders: 0,
          },
        ],
      };
    if (path === '/waves/work/take-plan-requests' && options?.method === 'POST')
      return { moved: ['70'], wave: { ...work, version: 4 } };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <Waves />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Аозеал 4 / Плановая — рабочая 1'));
  expect(await screen.findByRole('heading', { name: 'Заявки плановой волны' })).toBeVisible();
  const row = (await screen.findByText('№70')).closest('tr')!;
  expect(within(row).getByText('70-V2')).toBeVisible();
  fireEvent.click(screen.getByLabelText('Перенести заявку №70'));
  fireEvent.click(screen.getByRole('button', { name: /Перенести в эту рабочую волну \(1\)/ }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/waves/work/take-plan-requests',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({ version: 3, request_ids: ['r70'] }),
      }),
    ),
  );
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('№70'));
  confirm.mockRestore();
});
