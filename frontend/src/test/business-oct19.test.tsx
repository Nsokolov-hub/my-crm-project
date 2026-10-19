import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { useAuth } from '../app/Auth';
import { api } from '../lib/api';
import { SupplierOrders } from '../pages/Business';
import { Notifications } from '../pages/Communication';
import { PaymentConfirmations } from '../pages/Registry';

vi.mock('../app/Auth', () => ({ useAuth: vi.fn() }));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(useAuth).mockReturnValue({
    can: () => true,
  } as unknown as ReturnType<typeof useAuth>);
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

const calls = (prefix: string) =>
  vi.mocked(api).mock.calls.filter(([path]) => String(path).startsWith(prefix));

const position = (id: string, name: string) => ({
  id,
  name,
  article: `A-${id}`,
  packaging: '25 mg',
  quantity: '1.000000',
  unit_price: '2000.00000000',
  currency: 'CNY',
  supplier_id: 'shenzhen',
  supplier_name: 'Shenzhen Sungening',
  contract: 'OGK-1607',
  payment_terms: 'Before shipment',
});

it('shows ordered positions of several statuses at once, everything but the cancelled', async () => {
  vi.mocked(api).mockResolvedValue({ items: [], total: 0 });
  render(
    <MemoryRouter>
      <SupplierOrders />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Заказанные позиции' }));
  fireEvent.click(screen.getByLabelText('Статусы'));
  fireEvent.click(screen.getByRole('button', { name: 'Все, кроме отменённых' }));
  await waitFor(() =>
    expect(
      calls('/supplier-orders/positions?').some(([path]) =>
        String(path).includes(
          `status=${encodeURIComponent('waiting,delivery,arrived_ru,delivered')}`,
        ),
      ),
    ).toBe(true),
  );
  expect(screen.getByLabelText('Статусы')).toHaveTextContent('Все, кроме отменённых');
  expect(JSON.parse(localStorage.getItem('supplier-orders:statuses') || '[]')).not.toContain(
    'cancelled',
  );
});

it('lists prices in a table and issues an order without freight for a consolidation', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.startsWith('/supplier-orders/positions'))
      return { items: [position('e1', 'Demeclocycline'), position('e2', 'Tetracycline')] };
    if (path.startsWith('/supplier-orders/logistics'))
      return { amount: '4200.00000000', currency: 'CNY', basis: 'бюджет волны', notes: [] };
    if (path.startsWith('/sellers')) return { items: [{ id: 'ogk', name: 'ООО "ОГК-ХИМ"' }] };
    if (path === '/supplier-orders' && options?.method === 'POST')
      return { id: 'order', number: 'SHENZ1651-000002', total: '4000', currency: 'CNY' };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <SupplierOrders />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByLabelText('Выбрать Demeclocycline'));
  fireEvent.click(screen.getByLabelText('Выбрать Tetracycline'));
  fireEvent.click(screen.getByRole('button', { name: 'Создать заказ (2)' }));

  const table = (await screen.findByText('Цена за ед., CNY')).closest('table')!;
  expect(within(table).getByLabelText('Цена Demeclocycline')).toHaveValue('2000');
  expect(within(table).getByText('4 000')).toBeVisible();
  const logistics = screen.getByLabelText(/Международная логистика, CNY/);
  expect(logistics).toHaveValue('4200');
  fireEvent.click(screen.getByLabelText(/Без стоимости доставки/));
  expect(logistics).toHaveValue('0');

  fireEvent.focus(screen.getByLabelText(/Наша организация/));
  fireEvent.click(await screen.findByRole('option', { name: 'ООО "ОГК-ХИМ"' }));
  fireEvent.change(screen.getByLabelText(/Ожидаемая дата поставки/), {
    target: { value: '2026-11-06' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/supplier-orders',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          execution_ids: ['e1', 'e2'],
          logistics_separately: true,
          logistics_amount: '0',
        }),
      }),
    ),
  );
});

it('splits notices into work and system ones and reads all of a kind', async () => {
  vi.mocked(api).mockImplementation(async (path) =>
    String(path).startsWith('/notifications?')
      ? {
          items: [
            {
              id: 'n1',
              title: 'Запрос поставщику отправлен',
              entity_type: 'request',
              entity_id: 'r1',
              category: 'system',
              read: false,
              created_at: '2026-10-19T10:00:00Z',
            },
          ],
          total: 1,
          unread_work: 2,
          unread_system: 40,
        }
      : { count: 40 },
  );
  render(
    <MemoryRouter>
      <Notifications />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith('/notifications?category=work&read=false', expect.anything()),
  );
  fireEvent.click(screen.getByRole('button', { name: /Системные/ }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/notifications?category=system&read=false',
      expect.anything(),
    ),
  );
  fireEvent.click(await screen.findByRole('button', { name: /Прочитать все/ }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/notifications/read-all',
      expect.objectContaining({ method: 'POST', body: { category: 'system' } }),
    ),
  );
});

it('confirms a declared payment from the approvals registry', async () => {
  vi.mocked(api).mockImplementation(async (_path, options) =>
    options?.method === 'POST'
      ? { id: 'pay', status: 'confirmed' }
      : {
          items: [
            {
              id: 'pay',
              version: 1,
              number: '115',
              amount: '150000.00000000',
              currency: 'RUB',
              payment_date: '2026-10-19',
              request_id: 'r66',
              request_number: '66',
              client_name: 'ООО ХимВет',
              declared_by_name: 'Тимур',
              status: 'declared',
            },
          ],
        },
  );
  render(
    <MemoryRouter>
      <PaymentConfirmations />
    </MemoryRouter>,
  );
  expect(api).toHaveBeenCalledWith('/payments?status=declared&page_size=100', expect.anything());
  fireEvent.click(await screen.findByText('ООО ХимВет'));
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить поступление' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/payments/pay/confirm',
      expect.objectContaining({ method: 'POST', body: expect.objectContaining({ version: 1 }) }),
    ),
  );
});
