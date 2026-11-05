import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { queryClient } from '../lib/queryClient';
import { ApprovalDecision } from '../pages/Fulfillment';
import { Requests } from '../pages/Requests';
import { SiteRequestConvert } from '../pages/SiteRequestConvert';

// Business remarks of 5 November 2026: free lines of a site request, a wave for each approved position and
// requests of several clients.
vi.mock('../app/Auth', () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

const siteLine = {
  site_item_id: 'site-1',
  description: 'ENOXAPARIN SODIUM FOR BIOASSAYS — Sigma-Aldrich',
  article: '1235831-1AMP',
  cas: null,
  packaging: '1 шт.',
  quantity: '9',
  unit: 'pcs',
  allow_analogue: false,
  comment: null,
};

it('removes site lines, loads new ones from Excel and creates the request with them', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/site-requests/s5/conversion')
      return {
        matches: [],
        hidden: [],
        owner: { id: 'u', name: 'Анна' },
        proposal: {
          client: {
            name: 'ООО Промомед',
            tax_id: null,
            country: null,
            city: null,
            phone: null,
            email: null,
          },
          contact: { name: 'Анна', position: null, phone: null, email: null },
          title: 'ENOXAPARIN — ООО Промомед',
          items: [siteLine],
        },
      };
    if (path === '/site-requests/s5/conversion/table' && options?.method === 'POST')
      return {
        items: [
          { ...siteLine, site_item_id: null, description: 'Enoxaparin USP', article: '1235831' },
        ],
        errors: [{ row_number: 3, errors: ['Пустая строка'] }],
      };
    if (path === '/site-requests/s5/convert') return { status: 'converted' };
    return { items: [] };
  });
  const done = vi.fn();
  render(<SiteRequestConvert siteRequestId="s5" version={1} onClose={vi.fn()} onDone={done} />);
  const article = await screen.findByLabelText('Артикул позиции 1');
  // The whole catalogue number is in the field and in its hint.
  expect(article).toHaveValue('1235831-1AMP');
  expect(article).toHaveAttribute('title', '1235831-1AMP');

  fireEvent.click(screen.getByRole('button', { name: 'Удалить позицию 1' }));
  expect(screen.queryByLabelText('Артикул позиции 1')).not.toBeInTheDocument();

  const file = new File(['xlsx'], 'lines.xlsx');
  fireEvent.change(screen.getByLabelText('Таблица позиций'), { target: { files: [file] } });
  expect(await screen.findByLabelText('Артикул позиции 1')).toHaveValue('1235831');
  expect(screen.getByText(/добавлено позиций: 1\. Не прочитаны строки: 3/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Добавить позицию' }));
  fireEvent.change(screen.getByLabelText('Описание позиции 2'), {
    target: { value: 'Heparin USP' },
  });

  fireEvent.click(screen.getByRole('button', { name: 'Создать клиента, контакт и заявку' }));
  await waitFor(() => expect(done).toHaveBeenCalled());
  const convert = vi.mocked(api).mock.calls.find(([path]) => path === '/site-requests/s5/convert');
  const items = (convert?.[1]?.body as { items: { description: string; article: string | null }[] })
    .items;
  expect(items.map((item) => [item.description, item.article])).toEqual([
    ['Enoxaparin USP', '1235831'],
    ['Heparin USP', null],
  ]);
});

it('sends each approved position to its own wave', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/approvals/approval/waves')
      return {
        required: true,
        wave_id: null,
        items: [
          { id: 'near', label: 'Рабочая · Aozeal — рабочая 1', new: false, supplier_id: 'aozeal' },
          {
            id: 'plan',
            label: 'Новая рабочая волна из плановой «Aozeal»',
            new: true,
            supplier_id: 'aozeal',
          },
        ],
        lines: [
          { execution_id: 'e1', supplier_id: 'aozeal', label: 'Колонка C18 — 1 pcs' },
          { execution_id: 'e2', supplier_id: 'aozeal', label: 'Стандарт USP — 2 pcs' },
        ],
      };
    if (path === '/approvals/approval/decision') return { id: 'approval', status: 'approved' };
    return { items: [] };
  });
  const done = vi.fn();
  render(
    <ApprovalDecision row={{ id: 'approval', version: 1 }} onClose={vi.fn()} onSuccess={done} />,
  );
  await screen.findByLabelText(/Волна поставки для всех позиций/);
  fireEvent.change(screen.getByLabelText(/^Решение/, { selector: 'select' }), {
    target: { value: 'approved' },
  });
  fireEvent.change(screen.getByLabelText(/Колонка C18/), { target: { value: 'near' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  // One position without a wave and no common wave: the leader is asked to choose.
  expect(await screen.findByText(/для каждой позиции ниже/)).toBeInTheDocument();
  expect(done).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText(/Стандарт USP/), { target: { value: 'plan' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() => expect(done).toHaveBeenCalled());
  expect(api).toHaveBeenCalledWith(
    '/approvals/approval/decision',
    expect.objectContaining({
      body: expect.objectContaining({
        decision: 'approved',
        wave_id: null,
        line_waves: { e1: 'near', e2: 'plan' },
      }),
    }),
  );
  const body = vi
    .mocked(api)
    .mock.calls.find(([path]) => path === '/approvals/approval/decision')![1]!.body as Record<
    string,
    unknown
  >;
  expect(Object.keys(body).some((key) => key.startsWith('line_wave:'))).toBe(false);
});

it('filters the requests by several clients', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.startsWith('/counterparties?'))
      return {
        items: [
          { id: 'c1', name: 'АО «Верофарм»' },
          { id: 'c2', name: 'ООО «Промомед»' },
        ],
        total: 2,
      };
    if (path.startsWith('/requests?')) return { items: [], total: 0 };
    if (path.startsWith('/me/preferences')) return { value: null };
    return { items: [] };
  });
  render(
    <MemoryRouter>
      <Requests />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Клиенты: все' }));
  const menu = screen.getByRole('group', { name: 'Фильтр по клиентам' });
  fireEvent.click(await within(menu).findByLabelText('АО «Верофарм»'));
  fireEvent.click(within(menu).getByLabelText('ООО «Промомед»'));
  await waitFor(() =>
    expect(
      vi.mocked(api).mock.calls.some(([path]) => String(path).includes('client_ids=c1%2Cc2')),
    ).toBe(true),
  );
  expect(
    screen.getByRole('button', { name: /Клиенты: АО «Верофарм», ООО «Промомед»/ }),
  ).toHaveTextContent('Клиенты: 2');
});
