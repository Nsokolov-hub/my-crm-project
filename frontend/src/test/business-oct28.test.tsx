import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { ApprovalDecision } from '../pages/Fulfillment';
import { RequestCalculations } from '../pages/RequestCalculations';

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

const quote = {
  id: 'quote',
  nomenclature_name: 'Буфер',
  supplier_name: 'Aozeal',
  quantity: '2.000000',
  request_quantity: '2.000000',
  currency_code: 'RUB',
  product_group_slug: 'standards',
  unit_price: '100',
  delivery_days: 14,
  calculation_type: 'IMPORT',
};

type Body = { planned_volume?: unknown; planned_volume_comment?: string };

function mockCalculator() {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.endsWith('/preview'))
      return {
        snapshot: {
          lines: [{ quote_item_id: 'quote', quantity: '2', detail: {} }],
          totals: { total: '1000' },
          wave_distribution: { total_quantity: '20', digest: 'a'.repeat(64) },
        },
      };
    if (path.includes('/quote-items')) return { items: [quote] };
    if (path.startsWith('/profiles'))
      return {
        items: [
          {
            id: 'profile',
            name: 'Профиль',
            status: 'published',
            definition: { methodology: 'itemized_v2', default_expenses: [], exchange_rates: [] },
          },
        ],
      };
    if (path.endsWith('/wave-expenses'))
      return {
        expenses: [],
        complete_budget: true,
        wave_version: 2,
        planned_volume: [
          {
            product_group_id: 'standards',
            product_group_name: 'Стандартные образцы',
            wave_quantity: '10',
            plan_number: 'Aozeal / Плановая',
          },
        ],
      };
    return { items: [] };
  });
}

const previews = () =>
  vi
    .mocked(api)
    .mock.calls.filter(([path]) => String(path).endsWith('/preview'))
    .map(([, options]) => options?.body as Body);

it('changes the planned volume of the wave inside a calculation only with a comment', async () => {
  mockCalculator();
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
  const volume = await screen.findByLabelText('Плановый объём в расчёте: Стандартные образцы');
  expect(volume).toHaveValue('10');
  await waitFor(() => expect(previews()).toHaveLength(1));
  expect(previews()[0].planned_volume).toBeUndefined();

  fireEvent.change(volume, { target: { value: '20' } });
  const comment = screen.getByLabelText(/Комментарий к плановому объёму/);
  // Without a comment the changed volume is neither calculated nor saved.
  await new Promise((resolve) => setTimeout(resolve, 600));
  expect(previews()).toHaveLength(1);
  fireEvent.change(comment, { target: { value: 'Клиент берёт ещё 10 шт. в ноябре' } });
  await waitFor(() => expect(previews()).toHaveLength(2));
  expect(previews()[1]).toMatchObject({
    planned_volume: [{ product_group_id: 'standards', quantity: '20' }],
    planned_volume_comment: 'Клиент берёт ещё 10 шт. в ноябре',
  });

  fireEvent.click(screen.getByRole('button', { name: 'Вернуть объём волны' }));
  expect(volume).toHaveValue('10');
  expect(screen.queryByLabelText(/Комментарий к плановому объёму/)).not.toBeInTheDocument();
});

it('asks the leader for the wave of the approved orders', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/approvals/approval/waves')
      return {
        required: true,
        wave_id: 'work',
        items: [
          { id: 'work', label: 'Рабочая · Aozeal — рабочая 1', new: false },
          { id: 'plan', label: 'Новая рабочая волна из плановой «Aozeal»', new: true },
        ],
      };
    if (path === '/approvals/approval/decision') return { id: 'approval', status: 'approved' };
    return { items: [] };
  });
  const done = vi.fn();
  render(
    <ApprovalDecision row={{ id: 'approval', version: 1 }} onClose={vi.fn()} onSuccess={done} />,
  );
  const wave = await screen.findByLabelText(/Волна поставки/);
  expect(wave).toHaveValue('work');
  fireEvent.change(screen.getByLabelText(/^Решение/, { selector: 'select' }), {
    target: { value: 'approved' },
  });
  fireEvent.change(wave, { target: { value: 'plan' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() => expect(done).toHaveBeenCalled());
  expect(api).toHaveBeenCalledWith(
    '/approvals/approval/decision',
    expect.objectContaining({
      method: 'POST',
      body: expect.objectContaining({ version: 1, decision: 'approved', wave_id: 'plan' }),
    }),
  );
});

it('sends no wave when the leader returns the composition', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/approvals/approval/waves')
      return {
        required: true,
        wave_id: 'work',
        items: [{ id: 'work', label: 'Рабочая · Aozeal — рабочая 1', new: false }],
      };
    return { id: 'approval', status: 'returned' };
  });
  const done = vi.fn();
  render(
    <ApprovalDecision row={{ id: 'approval', version: 1 }} onClose={vi.fn()} onSuccess={done} />,
  );
  await screen.findByLabelText(/Волна поставки/);
  fireEvent.change(screen.getByLabelText(/^Решение/, { selector: 'select' }), {
    target: { value: 'returned' },
  });
  fireEvent.change(screen.getByLabelText(/Комментарий к решению/), {
    target: { value: 'Уточнить количество' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() => expect(done).toHaveBeenCalled());
  expect(api).toHaveBeenCalledWith(
    '/approvals/approval/decision',
    expect.objectContaining({ body: expect.objectContaining({ wave_id: null }) }),
  );
});
