import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { SiteRequests } from '../pages/SiteRequests';

vi.mock('../app/Auth', () => ({
  useAuth: () => ({ can: () => true, session: { user: { id: 'owner' } } }),
}));
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

const row = {
  id: 'sr-1',
  version: 1,
  number: 'W-1',
  site_number: 'S-2026-000001',
  kind: 'product_rfq',
  kind_label: 'Корзина запроса',
  lang: 'ru',
  status: 'new',
  status_label: 'Новая',
  received_at: '2026-10-24T08:18:04Z',
  company: 'ООО «Пример-Фарма»',
  inn: '7707083893',
  customer_name: 'Иван Петров',
  phone: '+79991234567',
  email: 'zakupki@example.ru',
  items_count: 1,
  channel: 'cpc',
  assignee_id: null,
  assignee_name: null,
  request_id: null,
  request_number: null,
};
const detail = {
  ...row,
  marketing_consent: false,
  items: [
    {
      id: 'i-1',
      position: 1,
      name: 'Oxcarbazepine CRS',
      brand: 'EDQM',
      catalog_number: 'Y0001897',
      cas: '28721-07-5',
      packaging: '100 mg',
      quantity: '2.000000',
      analogue: 'requested',
      custom_packaging: false,
    },
  ],
  attribution: { channel: 'cpc', last_utm_source: 'yandex', last_utm_medium: 'cpc', yclid: '42' },
  files: [],
  history: [{ action: 'received', at: '2026-10-24T08:18:04Z', actor_name: 'Сайт (интеграция)' }],
  can_assign: true,
  can_process: true,
};

it('lists site requests, shows one as sent and rejects it with a reason', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/site-requests/sr-1/reject' && options?.method === 'POST')
      return { ...detail, status: 'rejected', status_label: 'Отклонена', reject_reason: 'Дубль' };
    if (path === '/site-requests/sr-1') return detail;
    if (path.startsWith('/site-requests'))
      return {
        items: [row],
        total: 1,
        counts: { new: 1, in_work: 0, converted: 0, rejected: 0 },
        statuses: {},
      };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <SiteRequests />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('W-1'));
  const card = await screen.findByRole('dialog', { name: 'Заявка с сайта W-1' });
  expect(await within(card).findByText('Oxcarbazepine CRS')).toBeVisible();
  expect(within(card).getByText('Подберите аналог')).toBeVisible();
  expect(within(card).getByText('yandex / cpc')).toBeVisible();

  fireEvent.click(within(card).getByRole('button', { name: /Отклонить/ }));
  const form = await screen.findByRole('dialog', { name: 'Отклонить заявку с сайта' });
  fireEvent.change(within(form).getByLabelText(/Причина/), { target: { value: 'Дубль' } });
  fireEvent.click(within(form).getByRole('button', { name: 'Отклонить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/site-requests/sr-1/reject',
      expect.objectContaining({ method: 'POST', body: { reason: 'Дубль', version: 1 } }),
    ),
  );
});

it('creates a CRM request for the client found by INN in one command', async () => {
  const conversion = {
    matches: [
      {
        id: 'client-1',
        name: 'Пример-Фарма',
        internal_code: 17,
        tax_id: '7707083893',
        client_base: 'working',
        owner_name: 'Менеджер',
        reasons: ['ИНН', 'Название'],
        contacts: [{ id: 'contact-1', name: 'Иван Петров', phone: '+7 999 123-45-67' }],
        matched_contact_id: 'contact-1',
      },
    ],
    hidden: [],
    owner: { id: 'owner', name: 'Владелец' },
    proposal: {
      client: {
        name: 'ООО «Пример-Фарма»',
        tax_id: '7707083893',
        country: null,
        city: null,
        phone: null,
        email: null,
      },
      contact: {
        name: 'Иван Петров',
        position: null,
        phone: '+79991234567',
        email: 'zakupki@example.ru',
      },
      title: 'Oxcarbazepine CRS — ООО «Пример-Фарма»',
      items: [
        {
          site_item_id: 'i-1',
          description: 'Oxcarbazepine CRS — EDQM',
          article: 'Y0001897',
          cas: '28721-07-5',
          packaging: '100 mg',
          quantity: '2',
          unit: 'pcs',
          allow_analogue: true,
          comment: 'Клиент просит подобрать аналог',
        },
      ],
    },
  };
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/site-requests/sr-1/convert' && options?.method === 'POST')
      return {
        ...detail,
        status: 'converted',
        status_label: 'Преобразована',
        request_number: '17',
      };
    if (path === '/site-requests/sr-1/conversion') return conversion;
    if (path === '/site-requests/sr-1') return detail;
    if (path.startsWith('/site-requests'))
      return {
        items: [row],
        total: 1,
        counts: { new: 1, in_work: 0, converted: 0, rejected: 0 },
        statuses: {},
      };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter initialEntries={['/?id=sr-1']}>
      <SiteRequests />
    </MemoryRouter>,
  );
  const card = await screen.findByRole('dialog', { name: 'Заявка с сайта W-1' });
  fireEvent.click(await within(card).findByRole('button', { name: /Создать заявку/ }));
  const window = await screen.findByRole('dialog', { name: 'Создать заявку' });
  expect(await within(window).findByRole('radio', { name: /Пример-Фарма/ })).toBeChecked();
  expect(within(window).getByRole('radio', { name: /Иван Петров/ })).toBeChecked();
  fireEvent.change(within(window).getByLabelText('Количество позиции 1'), {
    target: { value: '3' },
  });
  fireEvent.click(within(window).getByRole('button', { name: 'Создать заявку' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/site-requests/sr-1/convert',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          version: 1,
          client_id: 'client-1',
          contact_id: 'contact-1',
          items: [
            expect.objectContaining({ site_item_id: 'i-1', quantity: '3', allow_analogue: true }),
          ],
        }),
      }),
    ),
  );
});
