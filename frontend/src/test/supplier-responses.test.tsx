import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { duration, RequestSupplierResponses, SupplierReport } from '../pages/SupplierResponses';

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

const matrix = {
  suppliers: [
    {
      supplier_id: 'alpha',
      name: 'Alpha Chem',
      asked_at: '2026-10-10T09:00:00Z',
      answered_at: '2026-10-11T15:00:00Z',
      hours: 30,
      mail_id: 'mail-a',
      mail_version: 2,
      replied_at: null,
      reply_note: '',
    },
    {
      supplier_id: 'beta',
      name: 'Beta Labs',
      asked_at: '2026-10-10T09:00:00Z',
      answered_at: null,
      hours: null,
      mail_id: 'mail-b',
      mail_version: 3,
      replied_at: null,
      reply_note: '',
    },
  ],
  items: [
    {
      id: 'i1',
      number: 1,
      description: 'Atropine',
      manufacturer_name: 'Merck',
      cells: {
        alpha: { status: 'priced', requested_at: '', answered_at: '', hours: 30 },
        beta: { status: 'waiting', requested_at: '', answered_at: null, hours: null },
      },
    },
    {
      id: 'i2',
      number: 2,
      description: 'Caffeine',
      manufacturer_name: null,
      cells: { alpha: { status: 'declined', requested_at: '', answered_at: null, hours: null } },
    },
  ],
};

it('shows per position who priced it, who left it without a price and who has not answered', async () => {
  vi.mocked(api).mockImplementation(async (_path, options) =>
    options?.method === 'POST' ? { id: 'mail-b' } : matrix,
  );
  render(<RequestSupplierResponses requestId="request" />);
  const table = (await screen.findByText('1. Atropine')).closest('table')!;
  const rows = within(table).getAllByRole('row');
  expect(within(rows[1]).getByText('Цена')).toBeVisible();
  expect(within(rows[1]).getByText('через 1,3 дн.')).toBeVisible();
  expect(within(rows[1]).getByText('Ждём ответа')).toBeVisible();
  expect(within(rows[2]).getByText('Без цены')).toBeVisible();
  expect(within(rows[2]).getByText('не запрашивали')).toBeVisible();
  expect(within(rows[0]).getByText('Ответ через 1,3 дн.')).toBeVisible();

  fireEvent.click(within(rows[0]).getByRole('button', { name: 'Ответил без цен' }));
  fireEvent.change(screen.getByLabelText(/^Что ответил/), {
    target: { value: 'Не работаем с Merck' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/supplier-mails/mail-b/reply',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          version: 3,
          note: 'Не работаем с Merck',
          replied_at: expect.any(String),
          idempotency_key: expect.any(String),
        }),
      }),
    ),
  );
});

it('says when no request was mailed yet', async () => {
  vi.mocked(api).mockResolvedValue({ suppliers: [], items: [] });
  render(<RequestSupplierResponses requestId="request" />);
  expect(await screen.findByText(/Запросы по почте ещё не отправлялись/)).toBeVisible();
});

it('ranks suppliers by answers and opens the brands one leaves without prices', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.startsWith('/analytics/suppliers/beta'))
      return {
        brands: [
          {
            id: 'merck',
            manufacturer_id: 'merck',
            name: 'Merck',
            positions: 4,
            priced: 0,
            declined: 3,
            waiting: 1,
          },
        ],
        positions: [
          {
            id: 'p1',
            request_number: '45',
            description: 'Atropine',
            manufacturer_name: 'Merck',
            requested_at: '2026-10-10T09:00:00Z',
            status: 'declined',
            hours: null,
          },
        ],
      };
    return {
      items: [
        {
          id: 'beta',
          supplier_id: 'beta',
          name: 'Beta Labs',
          requests: 5,
          answered_requests: 3,
          answer_rate: 60,
          average_hours: 50,
          median_hours: 20,
          positions: 12,
          priced: 6,
          declined: 4,
          waiting: 2,
          declined_brands: [{ id: 'merck', name: 'Merck', declined: 3, positions: 4 }],
        },
      ],
      basis: 'Письма с запросом цен, отправленные за период.',
    };
  });
  render(<SupplierReport from="2026-10-01" to="2026-10-13" />);
  expect(await screen.findByText('3 · 60 %')).toBeVisible();
  expect(screen.getByText('в среднем 2,1 дн., медиана 20 ч')).toBeVisible();
  expect(screen.getByText('Merck (3 из 4)')).toBeVisible();
  fireEvent.click(screen.getByText('Beta Labs'));
  const dialog = await screen.findByRole('dialog');
  expect(await within(dialog).findByRole('heading', { name: 'По брендам' })).toBeVisible();
  expect(within(dialog).getByText('Без цены', { selector: '.badge' })).toBeVisible();
});

it('says durations the way people do', () => {
  expect(duration(0.4)).toBe('меньше часа');
  expect(duration(5.4)).toBe('5 ч');
  expect(duration(36)).toBe('1,5 дн.');
  expect(duration(48)).toBe('2 дн.');
  expect(duration(null)).toBe('—');
});
