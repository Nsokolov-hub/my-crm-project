import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { WavePickerDialog } from '../pages/calculations/WavePicker';
import { CalculationBreakdown } from '../pages/calculations/CalculationBreakdown';
import { RequestDocuments } from '../pages/RequestDocuments';
import { RequestPriceReductions } from '../pages/RequestPriceReductions';
import { RequestQuotes } from '../pages/RequestProcurement';

vi.mock('../app/Auth', () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
  download: vi.fn(),
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

it('shows every temperature and only meaningful hazards in a saved calculation', () => {
  render(
    <CalculationBreakdown
      snapshot={{
        id: 'calculation',
        totals: { total: '100' },
        lines: [
          {
            line_id: '1',
            description: 'Товар A',
            quantity: '1',
            product: { storage_temperature: '2-8', transport_hazard: 'UN1234' },
            detail: {},
          },
          {
            line_id: '2',
            description: 'Товар B',
            quantity: '1',
            product: { storage_temperature: '2-8', transport_hazard: 'Не опасный' },
            detail: {},
          },
          {
            line_id: '3',
            description: 'Товар C',
            quantity: '1',
            product: { storage_temperature: null, transport_hazard: null },
            detail: {},
          },
        ],
      }}
    />,
  );
  const summary = screen.getByLabelText('Транспортные условия расчёта');
  expect(within(summary).getByText('Опасность UN1234: 1 позиция')).toBeVisible();
  expect(within(summary).getByText('Температура 2-8: 2 позиции')).toBeVisible();
  expect(within(summary).getByText('Температура не указана: 1 позиция')).toBeVisible();
  expect(screen.queryByText(/Не опасный/)).not.toBeInTheDocument();
});

it('filters waves by supplier and shows forecasts and the supplier card', async () => {
  const waves = [
    {
      id: 'plan-a',
      kind: 'plan',
      number: 'План A',
      supplier_id: 'a',
      supplier_name: 'Поставщик A',
      status: 'planned',
      forecasts: [{ id: 'f', target_quantity: '10', remaining_quantity: '6', active: true }],
    },
    {
      id: 'work-a',
      kind: 'work',
      number: 'Рабочая A',
      supplier_id: 'a',
      supplier_name: 'Поставщик A',
      status: 'assembling',
      plan_number: 'План A',
    },
    {
      id: 'plan-b',
      kind: 'plan',
      number: 'План B',
      supplier_id: 'b',
      supplier_name: 'Поставщик B',
      status: 'planned',
    },
  ];
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/counterparties/a')
      return {
        id: 'a',
        name: 'Поставщик A',
        country: 'Индия',
        details: {
          city: 'Мумбаи',
          'Юридический адрес': 'Мумбаи, склад 4',
          'Логистика по умолчанию': '15000',
          'Валюта логистики': 'RUB',
          'Дополнительные сведения': 'Минимальная партия от двух упаковок',
        },
      };
    return { items: [] };
  });
  const choose = vi.fn().mockResolvedValue(true);
  render(
    <WavePickerDialog
      waves={waves}
      quoteItems={[
        { id: 'q1', supplier_id: 'a', supplier_name: 'Поставщик A' },
        { id: 'q2', supplier_id: 'b', supplier_name: 'Поставщик B' },
      ]}
      counterparties={[]}
      value="plan-a"
      canWrite
      onChoose={choose}
      onClose={vi.fn()}
    />,
  );
  const dialog = screen.getByRole('dialog', { name: 'Выбор волны поставки' });
  expect(await within(dialog).findByText('Мумбаи, склад 4')).toBeVisible();
  expect(within(dialog).getByText('15 000 RUB')).toBeVisible();
  expect(within(dialog).getByText('Минимальная партия от двух упаковок')).toBeVisible();
  expect(within(dialog).getByRole('button', { name: /План: 10 шт/ })).toBeVisible();
  expect(within(dialog).getByRole('button', { name: /Рабочая A/ })).toBeVisible();
  expect(within(dialog).queryByRole('button', { name: /План B/ })).not.toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: /Рабочая A/ }));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Выбрать волну' }));
  await waitFor(() => expect(choose).toHaveBeenCalledWith('work-a'));
});

it('creates a reduction and asks the server for the required purchase hint', async () => {
  const option = {
    quote_item_id: 'quote',
    nomenclature_name: 'Реактив A',
    supplier_name: 'Поставщик A',
    article: 'A-1',
    unit_price: '1000',
    currency_code: 'RUB',
    current_sale_unit_price: '2000',
  };
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.includes('/price-reduction-options'))
      return {
        items: [option],
        leaders: [{ id: 'leader', name: 'Руководитель' }],
        managers: [{ id: 'manager', name: 'Менеджер квоты' }],
        default_manager_id: 'manager',
      };
    if (path.endsWith('/price-reduction-hints'))
      return { items: [{ ...option, required_purchase_rub: '800' }] };
    if (path.endsWith('/price-reductions') && options?.method === 'POST')
      return { id: 'reduction', status: 'pending_leader', items: [option], reviews: [] };
    if (path.endsWith('/price-reductions')) return { items: [] };
    return { items: [] };
  });
  const consumed = vi.fn();
  render(
    <RequestPriceReductions
      request={{ id: 'request', version: 7 }}
      launchQuoteItemIds={['quote']}
      onLaunchConsumed={consumed}
      onChanged={vi.fn()}
    />,
  );
  fireEvent.change(await screen.findByLabelText('Желаемая цена Реактив A'), {
    target: { value: '1800' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Подсказать закуп по последнему расчёту' }));
  expect(await screen.findByText('800')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Комментарий и условия клиента'), {
    target: { value: 'Готовы на аналог, нужна скидка 10 %' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Отправить на согласование' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/price-reductions',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          request_version: 7,
          leader_id: 'leader',
          manager_id: 'manager',
          quote_item_ids: ['quote'],
          targets: [{ quote_item_id: 'quote', target_sale_price: '1800' }],
        }),
      }),
    ),
  );
  expect(consumed).toHaveBeenCalled();
});

it('marks discounted quote prices and launches a reduction from selected positions', async () => {
  vi.mocked(api).mockResolvedValue({
    items: [
      {
        id: 'new-quote',
        quote_number: 'Q-2',
        nomenclature_name: 'Реактив A',
        unit_price: '900',
        previous_unit_price: '1000',
        discount_percent: '10.00',
        price_reduction_id: 'reduction',
        currency_code: 'RUB',
      },
    ],
    total: 1,
  });
  const reduce = vi.fn();
  render(<RequestQuotes requestId="request" onCalculate={vi.fn()} onPriceReduction={reduce} />);
  expect(await screen.findByText('Снижение −10%')).toBeVisible();
  expect(screen.getByText(/была 1 000/)).toBeVisible();
  fireEvent.click(screen.getByLabelText('Выбрать позицию Реактив A'));
  fireEvent.click(screen.getByRole('button', { name: 'Создать запрос на снижение' }));
  expect(reduce).toHaveBeenCalledWith(['new-quote']);
});

it('uses the same commercial columns in proposal and invoice lists', async () => {
  const row = {
    id: 'document',
    number: 'KP-1',
    status: 'issued',
    total: '1200',
    currency: 'RUB',
    files: {},
    supplier_names: ['Поставщик A'],
    delivery_days: 14,
    profitability_percent: '25.00',
    purchase_amount: '800.00',
    purchase_currency: 'RUB',
    sale_amount: '1200.00',
    sale_currency: 'RUB',
  };
  vi.mocked(api).mockImplementation(async () => ({ items: [row], total: 1 }));
  render(<RequestDocuments requestId="request" onChanged={vi.fn()} />);
  expect(await screen.findAllByText('Поставщик A')).toHaveLength(2);
  for (const heading of ['Срок', 'Рентабельность', 'Сумма закупа', 'Сумма продажи']) {
    expect(screen.getAllByRole('columnheader', { name: heading })).toHaveLength(2);
  }
  expect(screen.getAllByText('800 RUB')).toHaveLength(2);
  expect(screen.getAllByText('1 200 RUB')).toHaveLength(2);
});
