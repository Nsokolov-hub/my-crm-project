import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { PaymentCalendar } from '../pages/Business';
import { SupplierMailEditor } from '../components/SupplierMailEditor';
import { api } from '../lib/api';

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

it('submits a calendar row with version and body idempotency key, then allows draft editing', async () => {
  const row = {
    id: 'payment',
    version: 3,
    status: 'draft',
    direction: 'expense',
    planned_date: '2026-10-06',
    amount: '100',
    currency: 'RUB',
    purpose: 'Интернет',
    payment_kind: 'other',
    recurrence: 'none',
  };
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method) return row;
    if (path.startsWith('/payment-calendar'))
      return { items: [row], total: 1, summary: [], daily: [], parties: [] };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <PaymentCalendar />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Интернет'));
  fireEvent.click(screen.getByRole('button', { name: 'Направить руководителю' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/payment-calendar/payment/submit',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({ version: 3, idempotency_key: expect.any(String) }),
      }),
    ),
  );
  fireEvent.click(await screen.findByText('Интернет'));
  fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
  fireEvent.change(screen.getByLabelText(/Назначение платежа/), {
    target: { value: 'Аренда офиса' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/payment-calendar/payment',
      expect.objectContaining({
        method: 'PATCH',
        body: expect.objectContaining({
          version: 3,
          purpose: 'Аренда офиса',
          idempotency_key: expect.any(String),
        }),
      }),
    ),
  );
});

it('previews supplier mail and sends only after the explicit send button', async () => {
  const onSuccess = vi.fn();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/counterparties/supplier')
      return { id: 'supplier', name: 'Aozeal', email: 'sales@aozeal.example' };
    if (path.endsWith('/preview'))
      return {
        items: [
          { recipient: 'sales@aozeal.example', body: 'Dear Colleagues,\nSample\tA123\t1 pc' },
        ],
      };
    if (path.endsWith('/send')) return { items: [{ id: 'mail', status: 'queued' }] };
    return { items: [{ id: 'supplier', name: 'Aozeal' }], total: 1 };
  });
  render(
    <MemoryRouter>
      <SupplierMailEditor
        requestId="request"
        requestNumber="14"
        itemIds={['item']}
        onClose={() => {}}
        onSuccess={onSuccess}
      />
    </MemoryRouter>,
  );
  fireEvent.focus(await screen.findByRole('combobox'));
  fireEvent.click(await screen.findByRole('option', { name: 'Aozeal' }));
  fireEvent.click(screen.getByRole('button', { name: 'Добавить получателя' }));
  await screen.findByText('sales@aozeal.example');
  fireEvent.click(screen.getByRole('button', { name: 'Предпросмотр' }));
  await screen.findByText(/Sample/);
  expect(vi.mocked(api).mock.calls.some(([path]) => path.endsWith('/send'))).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: /Отправить отдельные письма/ }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/supplier-mail/send',
      expect.objectContaining({
        body: expect.objectContaining({
          supplier_ids: ['supplier'],
          cc: ['info@ogk-chem.ru'],
          item_ids: ['item'],
          subject: 'Request 14',
          idempotency_key: expect.any(String),
        }),
      }),
    ),
  );
  expect(onSuccess).toHaveBeenCalledOnce();
});
