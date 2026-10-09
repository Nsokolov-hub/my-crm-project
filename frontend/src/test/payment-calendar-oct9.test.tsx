import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { PaymentCalendar, WorkflowApprovals } from '../pages/Business';
import { api, download } from '../lib/api';

const permissions = vi.hoisted(() => ({ manager: true }));
vi.mock('../app/Auth', () => ({
  useAuth: () => ({ can: (code: string) => code !== 'approvals.decide' || permissions.manager }),
}));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
  download: vi.fn(),
}));

const pending = {
  id: 'payment',
  version: 2,
  status: 'pending',
  direction: 'expense',
  planned_date: '2026-10-06',
  actual_date: null,
  amount: '100',
  currency: 'RUB',
  purpose: 'Счёт за доставку',
  counterparty_name: 'Поставщик',
  payment_kind: 'other',
  recurrence: 'none',
  outside_payment_days: false,
  review_id: 'review',
  review_version: 4,
};
const anchor = {
  id: 'anchor',
  version: 3,
  balance_date: '2026-10-01',
  currency: 'RUB',
  amount: '1000',
  reason: 'Банковская выписка',
};
let calendar: Record<string, unknown>;

beforeEach(() => {
  vi.clearAllMocks();
  permissions.manager = true;
  calendar = {
    items: [pending],
    total: 1,
    global_balance: true,
    payment_days: { version: 2, weekdays: [1, 3] },
    parties: [],
    summary: [
      {
        currency: 'RUB',
        income: '200',
        expense: '100',
        planned_income: '0',
        planned_expense: '100',
        opening_balance: '1000',
        closing_balance: '1100',
        current_balance: '1100',
      },
    ],
    daily: [
      { date: '2026-10-06', currency: 'RUB', income: '200', expense: '0', balance: '1200' },
      { date: '2026-10-08', currency: 'RUB', income: '0', expense: '100', balance: '1100' },
    ],
  };
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method) return { ...pending, status: 'confirmed' };
    if (path === '/payment-calendar/balances') return { items: [anchor], total: 1 };
    if (path.startsWith('/payment-calendar?')) return calendar;
    return { items: [], total: 0 };
  });
  vi.mocked(download).mockResolvedValue(undefined);
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

function renderCalendar() {
  return render(
    <MemoryRouter>
      <PaymentCalendar />
    </MemoryRouter>,
  );
}

it('shows invoice attachment and confirms payment with the actual date, exception and review version', async () => {
  renderCalendar();
  fireEvent.click(await screen.findByText(pending.purpose));
  await screen.findByText('Счёт и документы платежа');
  expect(api).toHaveBeenCalledWith(
    '/files?entity_type=calendar_entry&entity_id=payment',
    expect.anything(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Согласовать или отклонить' }));
  fireEvent.change(screen.getByLabelText('Фактическая дата оплаты'), {
    target: { value: '2026-10-08' },
  });
  fireEvent.click(screen.getByLabelText('Платёж вне платёжных дней'));
  fireEvent.change(screen.getByLabelText(/Комментарий/), {
    target: { value: 'Банк провёл оплату' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/workflow-approvals/review/decision',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          version: 4,
          decision: 'approved',
          actual_date: '2026-10-08',
          outside_payment_days: true,
          idempotency_key: expect.any(String),
        }),
      }),
    ),
  );
});

it('allows rejection without supplying an actual cash date', async () => {
  renderCalendar();
  fireEvent.click(await screen.findByText(pending.purpose));
  fireEvent.click(screen.getByRole('button', { name: 'Согласовать или отклонить' }));
  fireEvent.change(screen.getByLabelText(/Решение/, { selector: 'select' }), {
    target: { value: 'rejected' },
  });
  fireEvent.change(screen.getByLabelText('Фактическая дата оплаты'), { target: { value: '' } });
  fireEvent.change(screen.getByLabelText(/Комментарий/), { target: { value: 'Ошибочный счёт' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/workflow-approvals/review/decision',
      expect.objectContaining({
        body: expect.objectContaining({ decision: 'rejected', reason: 'Ошибочный счёт' }),
      }),
    ),
  );
  const call = vi.mocked(api).mock.calls.find(([path]) => path.endsWith('/decision'));
  expect(call?.[1]?.body).not.toHaveProperty('actual_date');
});

it('edits a dated balance with its version and accepts negative saldo', async () => {
  renderCalendar();
  fireEvent.click(await screen.findByRole('button', { name: 'Зафиксировать сальдо' }));
  fireEvent.click(await screen.findByText('Банковская выписка'));
  fireEvent.change(screen.getByLabelText(/Сальдо/, { selector: 'input' }), {
    target: { value: '-100' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/payment-calendar/balances',
      expect.objectContaining({
        method: 'PUT',
        body: expect.objectContaining({
          version: 3,
          balance_date: '2026-10-01',
          currency: 'RUB',
          amount: '-100',
          idempotency_key: expect.any(String),
        }),
      }),
    ),
  );
});

it('saves configurable payment weekdays as numbers with optimistic concurrency', async () => {
  renderCalendar();
  await screen.findByText(pending.purpose);
  fireEvent.click(screen.getByRole('button', { name: 'Платёжные дни' }));
  const days = screen.getByLabelText(/Дни недели/) as HTMLSelectElement;
  for (const option of days.options) option.selected = option.value === '4';
  fireEvent.change(days);
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/payment-calendar/rules',
      expect.objectContaining({
        method: 'PUT',
        body: expect.objectContaining({ version: 2, weekdays: [4] }),
      }),
    ),
  );
});

it('shows actual charts, red rejected rows, period controls and a daily Excel registry', async () => {
  calendar.items = [{ ...pending, status: 'rejected', review_id: null }];
  const { container } = renderCalendar();
  await screen.findByText(pending.purpose);
  expect(
    screen.getByRole('img', { name: 'График поступлений и расходов, RUB' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('img', { name: 'График остатка денежных средств, RUB' }),
  ).toBeInTheDocument();
  expect(container.querySelector('.calendar-row-rejected .badge.red')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('С даты'), { target: { value: '2026-10-05' } });
  fireEvent.change(screen.getByLabelText('По дату'), { target: { value: '2026-10-09' } });
  fireEvent.change(screen.getByLabelText('График по'), { target: { value: 'week' } });
  await waitFor(() =>
    expect(
      vi
        .mocked(api)
        .mock.calls.some(([path]) => path.includes('from_date=2026-10-05&to_date=2026-10-09')),
    ).toBe(true),
  );
  fireEvent.change(screen.getByLabelText('Дата реестра'), { target: { value: '2026-10-08' } });
  fireEvent.click(screen.getByRole('button', { name: 'Выгрузить Excel' }));
  expect(download).toHaveBeenCalledWith(
    '/payment-calendar/export?on_date=2026-10-08&date_basis=actual',
    'payments-2026-10-08.xlsx',
  );
});

it('keeps company balance and manager decision actions out of employee view', async () => {
  permissions.manager = false;
  calendar.global_balance = false;
  renderCalendar();
  await screen.findByText(pending.purpose);
  expect(screen.queryByRole('button', { name: 'Зафиксировать сальдо' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Платёжные дни' })).not.toBeInTheDocument();
  expect(
    screen.queryByRole('img', { name: 'График остатка денежных средств, RUB' }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText('Фактический остаток на сегодня')).not.toBeInTheDocument();
  fireEvent.click(screen.getByText(pending.purpose));
  expect(
    screen.queryByRole('button', { name: 'Согласовать или отклонить' }),
  ).not.toBeInTheDocument();
  expect(api).not.toHaveBeenCalledWith('/payment-calendar/balances', expect.anything());
});

it('records the actual payment date through the common approvals page too', async () => {
  const review = {
    id: 'review',
    version: 4,
    kind: 'calendar',
    entity_id: 'payment',
    status: 'pending',
    title: 'Согласование счёта №17',
    snapshot: pending,
  };
  vi.mocked(api).mockImplementation(async (path) =>
    path.startsWith('/workflow-approvals')
      ? { items: [review], total: 1 }
      : { items: [], total: 0 },
  );
  render(
    <MemoryRouter>
      <WorkflowApprovals />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText(review.title));
  await screen.findByText('Счёт и документы платежа');
  fireEvent.click(screen.getByRole('button', { name: 'Принять решение' }));
  fireEvent.change(screen.getByLabelText(/Решение/, { selector: 'select' }), {
    target: { value: 'approved' },
  });
  fireEvent.change(screen.getByLabelText('Фактическая дата оплаты'), {
    target: { value: '2026-10-08' },
  });
  fireEvent.change(screen.getByLabelText(/Комментарий/), {
    target: { value: 'Оплачено по выписке' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/workflow-approvals/review/decision',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          version: 4,
          decision: 'approved',
          actual_date: '2026-10-08',
          idempotency_key: expect.any(String),
        }),
      }),
    ),
  );
});
