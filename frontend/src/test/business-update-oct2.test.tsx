import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { CounterpartyDocuments } from '../components/CounterpartyDocuments';
import { api, download } from '../lib/api';
import { Clients } from '../pages/Clients';
import { Contacts } from '../pages/Contacts';
import { RequestPayments } from '../pages/RequestPayments';
import { RequestQuotes, RequestRfqs } from '../pages/RequestProcurement';

const permissions = vi.hoisted(() => ({ allowed: new Set<string>(), all: true }));
vi.mock('../app/Auth', () => ({
  useAuth: () => ({
    can: (permission: string) => permissions.all || permissions.allowed.has(permission),
    session: { user: { id: 'manager' } },
  }),
}));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
  download: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(download).mockResolvedValue(undefined);
  permissions.all = true;
  permissions.allowed.clear();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

it('declares a payment using a fixed ISO currency option without free text', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === 'POST') return { id: 'payment' };
    if (path.endsWith('/payment-currencies'))
      return {
        items: [
          { id: 'RUB', name: 'RUB' },
          { id: 'EUR', name: 'EUR' },
        ],
      };
    return { items: [], total: 0 };
  });
  render(<RequestPayments requestId="request" />);
  fireEvent.click(await screen.findByRole('button', { name: /Заявить оплату/ }));
  const currency = screen.getByLabelText(/Валюта/);
  expect(currency.tagName).toBe('SELECT');
  expect(currency).toHaveValue('RUB');
  await screen.findByRole('option', { name: 'EUR' });
  fireEvent.change(currency, { target: { value: 'EUR' } });
  fireEvent.change(screen.getByLabelText(/Сумма поступления/), { target: { value: '100' } });
  fireEvent.change(screen.getByLabelText(/Номер платёжного документа/), {
    target: { value: '135' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/payments',
      expect.objectContaining({ body: expect.objectContaining({ currency: 'EUR' }) }),
    ),
  );
});

it('creates a reusable RFQ without requiring a supplier', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === 'POST') return { id: 'rfq' };
    if (path.startsWith('/requests/request/items'))
      return { items: [{ id: 'item', description: 'Реактив', quantity: '1' }] };
    return { items: [], total: 0 };
  });
  render(<RequestRfqs requestId="request" requestNumber="7" />);
  fireEvent.click(await screen.findByRole('button', { name: /Создать запрос/ }));
  expect(screen.getByLabelText('Поставщик (необязательно)')).not.toBeRequired();
  await screen.findByRole('option', { name: 'Реактив' });
  fireEvent.change(screen.getByLabelText(/Позиции запроса/), { target: { value: 'item' } });
  fireEvent.change(screen.getByLabelText(/Ответ до/), { target: { value: '2026-10-16' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/request/rfqs',
      expect.objectContaining({ body: expect.objectContaining({ item_ids: ['item'] }) }),
    ),
  );
  const body = vi
    .mocked(api)
    .mock.calls.find(([, options]) => options?.method === 'POST')?.[1]?.body;
  expect(body).not.toHaveProperty('supplier_id');
});

it('offers a common RFQ after a supplier is selected for a quote', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.startsWith('/counterparties?kind=supplier'))
      return { items: [{ id: 'supplier', name: 'ООО Поставщик' }] };
    if (path.includes('/rfqs'))
      return {
        items: [
          { id: 'common', supplier_id: null, number: '7' },
          { id: 'other', supplier_id: 'other-supplier', number: '8' },
        ],
      };
    return { items: [], total: 0 };
  });
  render(<RequestQuotes requestId="request" onCalculate={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: /Новая квота/ }));
  fireEvent.focus(screen.getByLabelText('Поставщик'));
  fireEvent.click(await screen.findByRole('option', { name: 'ООО Поставщик' }));
  expect(await screen.findByRole('option', { name: '7 · Общий запрос' })).toBeVisible();
  expect(screen.queryByRole('option', { name: '8' })).not.toBeInTheDocument();
});

it('does not fetch the payment currency write directory for a reader', async () => {
  permissions.all = false;
  permissions.allowed = new Set(['requests.read']);
  vi.mocked(api).mockResolvedValue({ items: [], total: 0 });
  render(<RequestPayments requestId="request" />);
  await screen.findByRole('heading', { name: 'Платежи' });
  expect(screen.queryByRole('button', { name: /Заявить оплату/ })).not.toBeInTheDocument();
  expect(vi.mocked(api).mock.calls.some(([path]) => path.includes('/payment-currencies'))).toBe(
    false,
  );
});

it('creates a supplier contact from the shared counterparty directory', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === 'POST') return { id: 'contact' };
    if (path.startsWith('/counterparties?contact_eligible=true'))
      return {
        items: [{ id: 'supplier', name: 'ООО Поставщик', kind: 'supplier', internal_code: 1622 }],
      };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <Contacts />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: /Добавить контакт/ }));
  fireEvent.focus(screen.getByLabelText(/Контрагент/));
  fireEvent.click(await screen.findByRole('option', { name: /ООО Поставщик/ }));
  fireEvent.change(screen.getByLabelText(/Имя контакта/), { target: { value: 'Иван Закупщик' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/contacts',
      expect.objectContaining({
        body: expect.objectContaining({ client_id: 'supplier', name: 'Иван Закупщик' }),
      }),
    ),
  );
});

it('shows the client owner to readers and permits supplier contacts without reassign permission', async () => {
  permissions.all = false;
  permissions.allowed = new Set(['clients.read', 'clients.write']);
  const supplier = {
    id: 'supplier',
    name: 'ООО Поставщик',
    kind: 'supplier',
    owner_id: 'owner',
    owner_name: 'Александр Олейников',
    version: 2,
  };
  vi.mocked(api).mockImplementation(async (path) =>
    path.startsWith('/counterparties?') ? { items: [supplier], total: 1 } : { items: [], total: 0 },
  );
  render(
    <MemoryRouter>
      <Clients />
    </MemoryRouter>,
  );
  expect(await screen.findByText('Александр Олейников')).toBeVisible();
  fireEvent.click(screen.getByText('ООО Поставщик'));
  expect(screen.getByRole('button', { name: /Добавить контакт/ })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Редактировать' }));
  expect(screen.queryByLabelText('Ответственный')).not.toBeInTheDocument();
});

it('sends the reassignment reason when editing a counterparty owner', async () => {
  const company = {
    id: 'company',
    name: 'ООО Клиент',
    kind: 'client',
    owner_id: 'old-owner',
    owner_name: 'Первый менеджер',
    version: 4,
  };
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/counterparties/company' && options?.method === 'PATCH')
      return { ...company, owner_id: 'new-owner', owner_name: 'Новый менеджер', version: 5 };
    if (path.startsWith('/counterparties?')) return { items: [company], total: 1 };
    if (path.startsWith('/counterparty-owners'))
      return {
        items: [
          { id: 'old-owner', name: 'Первый менеджер' },
          { id: 'new-owner', name: 'Новый менеджер' },
        ],
      };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <Clients />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('ООО Клиент'));
  fireEvent.click(screen.getByRole('button', { name: 'Редактировать' }));
  fireEvent.focus(screen.getByLabelText('Ответственный'));
  fireEvent.click(await screen.findByRole('option', { name: 'Новый менеджер' }));
  fireEvent.change(screen.getByLabelText('Причина изменения'), {
    target: { value: 'Передача клиента в другой отдел' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/counterparties/company',
      expect.objectContaining({
        method: 'PATCH',
        body: expect.objectContaining({
          version: 4,
          owner_id: 'new-owner',
          reason: 'Передача клиента в другой отдел',
        }),
      }),
    ),
  );
  expect(await screen.findByText('Новый менеджер')).toBeVisible();
});

it('uploads categorized counterparty documents and archives their metadata by version', async () => {
  const document = {
    id: 'doc',
    name: 'Устав.pdf',
    category: 'founding',
    status: 'clean',
    version: 3,
  };
  vi.mocked(api).mockImplementation(async (_path, options) =>
    options?.method ? { id: 'uploaded' } : { items: [document], total: 1 },
  );
  render(<CounterpartyDocuments counterpartyId="company" />);
  await screen.findByText('Устав.pdf');
  fireEvent.click(screen.getByRole('button', { name: 'Скачать' }));
  expect(download).toHaveBeenCalledWith('/counterparty-documents/doc/download', 'Устав.pdf');
  fireEvent.change(screen.getByLabelText('Категория документа'), { target: { value: 'contract' } });
  const file = new File(['%PDF-1.4\n%%EOF'], 'Договор.pdf', { type: 'application/pdf' });
  fireEvent.change(screen.getByLabelText('Файл документа'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('button', { name: /Загрузить документ/ }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/counterparties/company/documents',
      expect.objectContaining({
        method: 'POST',
        body: expect.any(FormData),
        key: expect.any(String),
      }),
    ),
  );
  const form = vi.mocked(api).mock.calls.find(([, options]) => options?.method === 'POST')?.[1]
    ?.body as FormData;
  expect(form.get('category')).toBe('contract');
  expect(form.get('file')).toBe(file);
  fireEvent.click(await screen.findByRole('button', { name: 'В архив' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/counterparty-documents/doc',
      expect.objectContaining({ method: 'PATCH', body: { version: 3, archived: true } }),
    ),
  );
});

it('blocks downloads of unverified documents and hides writes without client permission', async () => {
  permissions.all = false;
  permissions.allowed = new Set(['clients.read', 'exports.download']);
  vi.mocked(api).mockResolvedValue({
    items: [
      { id: 'doc', name: 'Договор.pdf', category: 'contract', status: 'quarantined', version: 1 },
    ],
  });
  render(<CounterpartyDocuments counterpartyId="company" />);
  expect(await screen.findByRole('button', { name: 'Скачать' })).toBeDisabled();
  expect(screen.getByText('Проверяется…')).toBeVisible();
  expect(screen.queryByLabelText('Файл документа')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'В архив' })).not.toBeInTheDocument();
});

it('keeps documents beyond the first page accessible in the counterparty container', async () => {
  vi.mocked(api).mockImplementation(async (path) => ({
    items: [
      {
        id: 'doc',
        name: path.includes('page=2') ? 'Документ 26.pdf' : 'Документ 1.pdf',
        category: 'other',
        status: 'clean',
        version: 1,
      },
    ],
    total: 26,
  }));
  render(<CounterpartyDocuments counterpartyId="company" />);
  await screen.findByText('Документ 1.pdf');
  fireEvent.click(screen.getByRole('button', { name: 'Следующая страница' }));
  expect(await screen.findByText('Документ 26.pdf')).toBeVisible();
  expect(api).toHaveBeenCalledWith(
    '/counterparties/company/documents?page=2&page_size=25',
    expect.anything(),
  );
});
