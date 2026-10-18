import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { useAuth } from '../app/Auth';
import { api, ApiError } from '../lib/api';
import { SupplierOrders } from '../pages/Business';
import { RequestCalculations } from '../pages/RequestCalculations';

vi.mock('../app/Auth', () => ({ useAuth: vi.fn() }));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

function allow(check: (code: string) => boolean) {
  vi.mocked(useAuth).mockReturnValue({
    session: undefined,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    can: check,
  } as unknown as ReturnType<typeof useAuth>);
}

beforeEach(() => {
  vi.clearAllMocks();
  allow(() => true);
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

const wave = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  version: 4,
  kind: 'work',
  number: `Волна ${id}`,
  supplier_name: 'Shenzhen Sungening',
  status: 'planned',
  archived: false,
  ...extra,
});

const calls = (path: string) => vi.mocked(api).mock.calls.filter(([called]) => called === path);

it('repeats the wave assignment on the current request version when only the version moved on', async () => {
  let attempts = 0;
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/waves?open=true') return { items: [wave('new')], total: 1 };
    if (path === '/waves/old') return wave('old', { archived: true });
    if (path === '/requests/request' && !options?.method)
      return { id: 'request', version: 7, wave_id: 'old' };
    if (path === '/requests/request/wave') {
      attempts += 1;
      if (attempts === 1)
        throw new ApiError(409, 'VERSION_CONFLICT', 'Запись изменена другим сотрудником.');
      return { wave_id: 'new', version: 8 };
    }
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <RequestCalculations request={{ id: 'request', version: 3, wave_id: 'old' }} />
    </MemoryRouter>,
  );
  const picker = screen.getByLabelText('Волна поставки');
  await waitFor(() =>
    expect(within(picker).getByRole('option', { name: /Волна new/ })).toBeVisible(),
  );
  fireEvent.change(picker, { target: { value: 'new' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить волну' }));

  await waitFor(() => expect(calls('/requests/request/wave')).toHaveLength(2));
  const bodies = calls('/requests/request/wave').map(
    ([, options]) => options?.body as Record<string, unknown>,
  );
  expect(bodies.map((body) => body.request_version)).toEqual([3, 7]);
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Сохранить волну' })).toBeDisabled(),
  );
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('saves the shared expenses to the wave with a repeatable operation key', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/waves?open=true') return { items: [wave('plan')], total: 1 };
    if (path.includes('/quote-items'))
      return {
        items: [
          {
            id: 'quote',
            nomenclature_name: 'Ticagrelor impurity E',
            currency_code: 'RUB',
            unit_price: '100',
            calculation_type: 'IMPORT',
          },
        ],
      };
    if (path.startsWith('/profiles'))
      return {
        items: [
          {
            id: 'profile',
            name: 'Профиль 2026',
            status: 'published',
            definition: { methodology: 'itemized_v2', default_expenses: [], exchange_rates: [] },
          },
        ],
      };
    if (path.endsWith('/wave-expenses'))
      return { expenses: [], wave_version: 4, complete_budget: true };
    if (path === '/waves/plan/budget') return wave('plan', { version: 5 });
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <RequestCalculations
        request={{ id: 'request', version: 1, wave_id: 'plan' }}
        launchQuoteItemIds={['quote']}
      />
    </MemoryRouter>,
  );
  fireEvent.change(await screen.findByLabelText('Профиль расчёта'), {
    target: { value: 'profile' },
  });
  const save = await screen.findByRole('button', { name: 'Сохранить общие расходы в волну' });
  await waitFor(() => expect(save).toBeEnabled());
  fireEvent.click(save);

  await waitFor(() => expect(calls('/waves/plan/budget')).toHaveLength(1));
  const [, options] = calls('/waves/plan/budget')[0];
  expect(options?.method).toBe('PUT');
  expect(options?.body).toMatchObject({
    version: 4,
    profile_id: 'profile',
    idempotency_key: expect.any(String),
  });
});

const ordered = {
  id: 'line-1',
  version: 2,
  name: 'Tryptophan',
  supplier_id: 'supplier',
  supplier_name: 'Pharmaffiliates Pvt Ltd',
  manufacturer: 'Pharmaffiliates',
  article: 'PA 20 62000',
  packaging: '50 mg',
  quantity: '1',
  currency: 'INR',
  status: 'waiting',
  order_number: 'PHARM1622-000002',
  request_id: 'request',
  request_number: '41',
  client_name: 'ООО Клиент',
};

function mockOrders() {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.startsWith('/supplier-orders/positions?') && path.includes('ordered=true'))
      return {
        items: [ordered, { ...ordered, id: 'line-2', name: 'Bacitracin', status: 'cancelled' }],
      };
    if (path.startsWith('/supplier-order-lines/')) return { count: 1, items: [] };
    return { items: [], total: 0 };
  });
}

it('lets a logistician mark selected ordered positions as arrived in Russia', async () => {
  allow((code) => ['requests.read', 'supplier_orders.delivery'].includes(code));
  mockOrders();
  render(
    <MemoryRouter>
      <SupplierOrders />
    </MemoryRouter>,
  );
  expect(screen.queryAllByRole('button', { name: /Позиции к заказу|Статус заказов/ })).toEqual([]);
  fireEvent.click(await screen.findByLabelText('Выбрать Tryptophan'));
  // A cancelled position cannot be moved on and has no checkbox.
  expect(screen.queryByLabelText('Выбрать Bacitracin')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Отменить…' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Прибыло в РФ' }));

  await waitFor(() => expect(calls('/supplier-order-lines/status')).toHaveLength(1));
  expect(calls('/supplier-order-lines/status')[0][1]?.body).toMatchObject({
    lines: [{ id: 'line-1', version: 2 }],
    status: 'arrived_ru',
    idempotency_key: expect.any(String),
  });
  expect(await screen.findByRole('status')).toHaveTextContent('Прибыло в РФ: позиций 1.');
});

it('cancels selected ordered positions only with a reason', async () => {
  mockOrders();
  render(
    <MemoryRouter>
      <SupplierOrders />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Заказанные позиции' }));
  fireEvent.click(await screen.findByLabelText('Выбрать Tryptophan'));
  fireEvent.click(screen.getByRole('button', { name: 'Отменить…' }));
  const submit = screen.getByRole('button', { name: 'Отменить позиции' });
  expect(submit).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Причина отмены'), {
    target: { value: 'Поставщик снял позицию' },
  });
  fireEvent.click(submit);

  await waitFor(() => expect(calls('/supplier-order-lines/cancel')).toHaveLength(1));
  expect(calls('/supplier-order-lines/cancel')[0][1]?.body).toMatchObject({
    lines: [{ id: 'line-1', version: 2 }],
    reason: 'Поставщик снял позицию',
  });
  expect(await screen.findByRole('status')).toHaveTextContent('Позиции к заказу');
});
