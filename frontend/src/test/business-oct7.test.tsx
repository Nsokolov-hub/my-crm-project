import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { SupplierMailEditor } from '../components/SupplierMailEditor';
import { api } from '../lib/api';
import { RequestCalculations } from '../pages/RequestCalculations';
import { RequestDocuments } from '../pages/RequestDocuments';
import { RequestQuotes } from '../pages/RequestProcurement';

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

it.each([
  ['Удалить позицию', '/quote-items/quote/delete', 2],
  ['Удалить квоту целиком', '/quote-sheets/sheet/delete', 3],
])(
  'deletes through %s and refreshes the list and calculation selection',
  async (button, endpoint, version) => {
    let removed = false;
    vi.mocked(api).mockImplementation(async (_path, options) => {
      if (options?.method === 'POST') {
        removed = true;
        return { deleted_ids: ['quote'] };
      }
      return {
        items: removed
          ? []
          : [
              {
                id: 'quote',
                version: 2,
                quote_sheet_id: 'sheet',
                quote_sheet_version: 3,
                quote_number: 'Q-000001',
                supplier_name: 'Aozeal',
                nomenclature_name: 'Sample',
                packing_name: '100 mg',
              },
            ],
        total: removed ? 0 : 1,
      };
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<RequestQuotes requestId="request" onCalculate={vi.fn()} />);
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Выбрать позицию Sample' }));
    expect(screen.getByRole('button', { name: 'Сформировать расчёт' })).toBeEnabled();
    fireEvent.click(screen.getByText('Sample'));
    fireEvent.click(screen.getByRole('button', { name: String(button) }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        endpoint,
        expect.objectContaining({
          method: 'POST',
          body: expect.objectContaining({ version, idempotency_key: expect.any(String) }),
        }),
      ),
    );
    await waitFor(() => expect(screen.queryByText('Sample')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Сформировать расчёт' })).toBeDisabled();
  },
);

it('requires a fresh preview after changing CC and sends all chosen copy recipients', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/counterparties/supplier')
      return { id: 'supplier', name: 'Aozeal', email: 'sales@example.com' };
    if (path.endsWith('/preview'))
      return { items: [{ recipient: 'sales@example.com', body: 'Name\tArt / CAS\nSample\t0042' }] };
    return { items: [{ id: 'supplier', name: 'Aozeal' }], total: 1 };
  });
  render(
    <SupplierMailEditor
      requestId="request"
      itemIds={['item']}
      onClose={vi.fn()}
      onSuccess={vi.fn()}
    />,
  );
  expect(screen.getByLabelText(/Копия/)).toHaveValue('info@ogk-chem.ru');
  fireEvent.focus(screen.getByRole('combobox'));
  fireEvent.click(await screen.findByRole('option', { name: 'Aozeal' }));
  fireEvent.click(screen.getByRole('button', { name: 'Добавить получателя' }));
  await screen.findByText('sales@example.com');
  fireEvent.click(screen.getByRole('button', { name: 'Предпросмотр' }));
  await screen.findByRole('button', { name: /Отправить отдельные письма/ });
  fireEvent.change(screen.getByLabelText(/Копия/), {
    target: { value: 'info@ogk-chem.ru; manager@example.com' },
  });
  expect(
    screen.queryByRole('button', { name: /Отправить отдельные письма/ }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Предпросмотр' }));
  fireEvent.click(await screen.findByRole('button', { name: /Отправить отдельные письма/ }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/supplier-mail/send',
      expect.objectContaining({
        body: expect.objectContaining({ cc: ['info@ogk-chem.ru', 'manager@example.com'] }),
      }),
    ),
  );
});

it('selects a calculation by its number and defaults proposal validity to five calendar days', async () => {
  vi.mocked(api).mockImplementation(async (path) =>
    path.includes('/calculations')
      ? {
          items: [{ id: 'calc', version_number: 14, reason: 'Длинное описание источника расчёта' }],
        }
      : { items: [] },
  );
  render(
    <MemoryRouter>
      <RequestDocuments requestId="request" />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Выпустить КП' }));
  const expiry = screen.getByLabelText(/^Действует до/);
  const today = new Date();
  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  expect(expiry).toHaveAttribute('min', iso(today));
  today.setDate(today.getDate() + 5);
  expect(expiry).toHaveValue(iso(today));
  expect(expiry).toHaveAttribute('max', iso(today));
  fireEvent.focus(screen.getByLabelText(/^Расчёт/));
  expect(await screen.findByRole('option', { name: '14' })).toBeVisible();
  expect(screen.queryByText('Длинное описание источника расчёта')).not.toBeInTheDocument();
});

it('shows supplier and delivery days next to saved calculation positions', async () => {
  vi.mocked(api).mockImplementation(async (path) =>
    path.includes('/calculations')
      ? {
          items: [
            {
              id: 'calc',
              version_number: 14,
              snapshot: {
                lines: [
                  {
                    line_id: 'line',
                    description: 'Sample 100 mg',
                    supplier_name: 'Aozeal',
                    delivery_days: 35,
                    quantity: '1',
                    total: '100',
                  },
                ],
              },
            },
          ],
        }
      : { items: [] },
  );
  render(
    <MemoryRouter>
      <RequestCalculations request={{ id: 'request', number: '22' }} />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('№14'));
  expect(screen.getByText('Aozeal')).toBeVisible();
  expect(screen.getByText('Срок поставки, дней')).toBeVisible();
  expect(screen.getByText('35')).toBeVisible();
});
