import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { TableImportDialog } from '../components/TableImportDialog';
import { api } from '../lib/api';
import { Calls } from '../pages/Clients';
import { RequestItemEditor } from '../pages/RequestItemEditor';
import { RequestPayments } from '../pages/RequestPayments';

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: vi.fn(),
  download: vi.fn(),
}));
vi.mock('../app/Auth', () => ({
  useAuth: () => ({ can: () => true, session: { user: { id: 'user' } } }),
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

it('filters the cold base and promotes the same company without losing its code', async () => {
  const company = {
    id: 'cold',
    name: 'Холодная компания',
    client_base: 'cold',
    internal_code: 513,
    version: 1,
  };
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/counterparties/cold') return company;
    if (path === '/counterparties/cold/promote') return { ...company, client_base: 'working' };
    return { items: path.startsWith('/counterparties?') ? [company] : [], total: 1 };
  });
  render(
    <MemoryRouter>
      <Calls />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Холодная компания'));
  fireEvent.click(screen.getByRole('button', { name: 'Перенести в рабочую базу' }));
  await screen.findByText(/Клиент перенесён в рабочую базу/);
  expect(api).toHaveBeenCalledWith(
    '/counterparties/cold/promote',
    expect.objectContaining({ method: 'POST', body: { version: 1 } }),
  );
  expect(vi.mocked(api).mock.calls.some(([path]) => path.includes('client_base=cold'))).toBe(true);
});

it('sets the currency from the selected invoice and sends its ISO code', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === 'POST') return { id: 'payment' };
    if (path.includes('/payment-currencies'))
      return {
        items: [
          { id: 'RUB', name: 'RUB' },
          { id: 'EUR', name: 'EUR' },
        ],
      };
    if (path.includes('/invoices'))
      return { items: [{ id: 'invoice', number: 'INV-513', currency: 'EUR', status: 'issued' }] };
    return { items: [], total: 0 };
  });
  render(<RequestPayments requestId="request" />);
  fireEvent.click(await screen.findByRole('button', { name: /Заявить оплату/ }));
  await screen.findByRole('option', { name: 'INV-513 · EUR' });
  fireEvent.change(screen.getByLabelText('Счёт'), { target: { value: 'invoice' } });
  await waitFor(() => expect(screen.getByLabelText(/Валюта/)).toHaveValue('EUR'));
  fireEvent.change(screen.getByLabelText(/Сумма поступления/), { target: { value: '75038' } });
  fireEvent.change(screen.getByLabelText(/Номер платёжного документа/), {
    target: { value: '135' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/payments',
      expect.objectContaining({
        body: expect.objectContaining({ currency: 'EUR', invoice_id: 'invoice', amount: '75038' }),
      }),
    ),
  );
});

it('shows all import counters and prevents writing a quote file with errors', async () => {
  vi.mocked(api).mockResolvedValue({
    id: 'batch',
    columns: ['Артикул'],
    mapping: { article: 'Артикул' },
    status: 'preview',
    rows: [
      {
        row_number: 2,
        action: 'error',
        data: { article: '0341' },
        errors: ['Поставщик не найден'],
      },
    ],
    summary: { total: 3, checked: 1, create_nomenclature: 1, errors: 1 },
    result: {},
  });
  render(
    <TableImportDialog requestId="request" kind="quotes" onClose={vi.fn()} onSuccess={vi.fn()} />,
  );
  fireEvent.change(screen.getByLabelText('Файл таблицы'), {
    target: { files: [new File(['test'], 'quote.xlsx')] },
  });
  fireEvent.submit(screen.getByLabelText('Файл таблицы').closest('form')!);
  expect(await screen.findByText('Поставщик не найден')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Записать позиции: 3' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Скачать все ошибки' })).toBeEnabled();
  expect(screen.getByRole('status')).toHaveTextContent('Создать номенклатуру: 1');
});

it('shows the actual imported count after confirmation', async () => {
  const batch = {
    id: 'batch',
    columns: ['Артикул'],
    mapping: { article: 'Артикул' },
    rows: [],
    status: 'preview',
    summary: { total: 150, checked: 100, create_nomenclature: 50, errors: 0 },
    result: {},
  };
  vi.mocked(api)
    .mockResolvedValueOnce(batch)
    .mockResolvedValueOnce({
      ...batch,
      status: 'completed',
      result: { imported: 150, nomenclatures_created: 45 },
    });
  const saved = vi.fn();
  render(
    <TableImportDialog requestId="request" kind="quotes" onClose={vi.fn()} onSuccess={saved} />,
  );
  fireEvent.change(screen.getByLabelText('Файл таблицы'), {
    target: { files: [new File(['test'], 'quote.xlsx')] },
  });
  fireEvent.submit(screen.getByLabelText('Файл таблицы').closest('form')!);
  fireEvent.click(await screen.findByRole('button', { name: 'Записать позиции: 150' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Записано: 150'));
  expect(screen.getByRole('status')).toHaveTextContent('Номенклатур создано: 45');
  expect(saved).toHaveBeenCalledOnce();
});

it('maps a product group column and previews each item’s actual group before confirming', async () => {
  const batch = {
    id: 'batch',
    columns: ['Артикул', 'Категория'],
    mapping: { article: 'Артикул' },
    status: 'preview',
    rows: [
      {
        row_number: 2,
        action: 'create_nomenclature',
        data: { name: 'Новый реактив', product_group_name: 'Прочее' },
        errors: [],
      },
      {
        row_number: 3,
        action: 'checked',
        data: { name: 'Известный товар', product_group_name: 'Колонки' },
        errors: [],
      },
    ],
    summary: { total: 2, checked: 1, create_nomenclature: 1, errors: 0 },
    result: {},
  };
  vi.mocked(api)
    .mockResolvedValueOnce(batch)
    .mockResolvedValueOnce({
      ...batch,
      mapping: { ...batch.mapping, product_group: 'Категория' },
      rows: [
        { ...batch.rows[0], data: { name: 'Новый реактив', product_group_name: 'Реактивы' } },
        batch.rows[1],
      ],
    });
  render(
    <TableImportDialog requestId="request" kind="quotes" onClose={vi.fn()} onSuccess={vi.fn()} />,
  );
  const input = screen.getByLabelText('Файл таблицы');
  fireEvent.change(input, { target: { files: [new File(['test'], 'quote.xlsx')] } });
  fireEvent.submit(input.closest('form')!);
  expect(await screen.findByRole('columnheader', { name: 'Товарная группа' })).toBeVisible();
  expect(screen.getByRole('cell', { name: 'Прочее' })).toBeVisible();
  expect(screen.getByRole('cell', { name: 'Колонки' })).toBeVisible();
  fireEvent.click(screen.getByText('Сопоставление столбцов'));
  fireEvent.change(screen.getByLabelText('Товарная группа'), { target: { value: 'Категория' } });
  expect(screen.getByRole('button', { name: 'Записать позиции: 2' })).toBeDisabled();
  fireEvent.submit(input.closest('form')!);
  expect(await screen.findByRole('cell', { name: 'Реактивы' })).toBeVisible();
  expect(screen.getByRole('cell', { name: 'Колонки' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Записать позиции: 2' })).toBeEnabled();
  const previewBody = vi.mocked(api).mock.calls[1][1]?.body as FormData;
  expect(JSON.parse(String(previewBody.get('mapping')))).toEqual({
    article: 'Артикул',
    product_group: 'Категория',
  });
});

it('saves a client demand row before exact nomenclature is known', async () => {
  vi.mocked(api).mockImplementation(async (_path, options) =>
    options?.method === 'POST' ? { id: 'item' } : { items: [] },
  );
  render(<RequestItemEditor requestId="request" onClose={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText(/Исходное наименование/), {
    target: { value: 'Сыворотка по описанию клиента' },
  });
  fireEvent.click(screen.getByRole('button', { name: /Сохранить позицию/ }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/items',
      expect.objectContaining({
        body: expect.objectContaining({
          nomenclature_id: null,
          packing_id: null,
          description: 'Сыворотка по описанию клиента',
        }),
      }),
    ),
  );
});
