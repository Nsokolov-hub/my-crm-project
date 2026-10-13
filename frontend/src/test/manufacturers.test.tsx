import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { Catalog } from '../pages/Catalog';
import { ManufacturerReport, type Manufacturer } from '../pages/Manufacturers';

vi.mock('../app/Auth', () => ({
  useAuth: () => ({ can: () => true, session: { user: { id: 'leader' } } }),
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

function maker(id: string, code: number, name: string, extra: Partial<Manufacturer> = {}) {
  return {
    id,
    version: 1,
    internal_code: code,
    name,
    label: `${code} · ${name}`,
    aliases: [],
    umbrella_id: null,
    umbrella_name: null,
    nomenclatures: 0,
    brand_members: 0,
    merged_into_id: null,
    merged_into_name: null,
    ...extra,
  };
}
const sigma = maker('sigma', 7, 'Sigma-Aldrich', { nomenclatures: 8, umbrella_name: 'Merck' });
const spaced = maker('spaced', 9, 'Sigma Aldrich', { nomenclatures: 2, version: 3 });
const short = maker('short', 12, 'Sigma', { nomenclatures: 2, version: 2 });

function renderCatalog() {
  render(
    <MemoryRouter>
      <Catalog />
    </MemoryRouter>,
  );
}

it('lists the registry and merges a group of look-alike spellings into the chosen main entry', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.startsWith('/manufacturers/duplicates'))
      return { items: [{ id: 'sigma', manufacturers: [sigma, spaced, short] }], total: 1 };
    if (path.endsWith('/merge') && options?.method === 'POST') return { ...sigma, moved: {} };
    if (path.startsWith('/manufacturers')) return { items: [sigma, spaced, short], total: 3 };
    return { items: [], total: 0 };
  });
  renderCatalog();
  fireEvent.click(await screen.findByRole('button', { name: 'Производители и бренды' }));
  expect(await screen.findByText('Похоже на одного производителя')).toBeVisible();
  const table = (await screen.findByRole('columnheader', { name: /^Номер/ })).closest('table')!;
  expect(within(table).getByText('Merck')).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: 'Объединить' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByLabelText(/7 · Sigma-Aldrich/)).toBeChecked();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Объединить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/manufacturers/short/merge',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({ version: 2, target_id: 'sigma' }),
      }),
    ),
  );
  expect(api).toHaveBeenCalledWith(
    '/manufacturers/spaced/merge',
    expect.objectContaining({
      body: expect.objectContaining({
        version: 3,
        target_id: 'sigma',
        reason: 'Одно и то же написание производителя',
        idempotency_key: expect.any(String),
      }),
    }),
  );
});

it('edits spellings one per line and chooses the umbrella brand', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/manufacturers/spaced' && options?.method === 'PATCH') return spaced;
    if (path.startsWith('/manufacturers/duplicates')) return { items: [], total: 0 };
    if (path.startsWith('/manufacturers')) return { items: [spaced], total: 1 };
    return { items: [], total: 0 };
  });
  renderCatalog();
  fireEvent.click(await screen.findByRole('button', { name: 'Производители и бренды' }));
  fireEvent.click(await screen.findByText('Sigma Aldrich'));
  fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
  fireEvent.change(screen.getByLabelText(/^Другие написания/), {
    target: { value: 'SIGMA ALDRICH\n\n  Sigma Aldrich Co ' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/manufacturers/spaced',
      expect.objectContaining({
        method: 'PATCH',
        body: expect.objectContaining({
          version: 3,
          aliases: ['SIGMA ALDRICH', 'Sigma Aldrich Co'],
          umbrella_id: null,
        }),
      }),
    ),
  );
});

it('shows requests and orders by manufacturer or by umbrella brand', async () => {
  vi.mocked(api).mockImplementation(async (path) => ({
    items: [
      {
        id: 'sigma',
        manufacturer_id: 'sigma',
        internal_code: 7,
        name: path.includes('by=umbrella') ? 'Merck' : 'Sigma-Aldrich',
        umbrella_name: path.includes('by=umbrella') ? null : 'Merck',
        requests: 3,
        positions: 5,
        quoted_positions: 4,
        ordered_positions: 2,
        ordered_quantity: '6',
        ordered_sales: '125000.00',
      },
    ],
    basis: 'Позиции заявок, созданных за период.',
  }));
  render(<ManufacturerReport from="2026-01-01" to="2026-10-13" />);
  expect(await screen.findByText('7 · Sigma-Aldrich')).toBeVisible();
  expect(screen.getByText('125 000')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'По зонтичным брендам' }));
  expect(await screen.findByText('7 · Merck')).toBeVisible();
  expect(api).toHaveBeenLastCalledWith(
    '/analytics/manufacturers?date_from=2026-01-01&date_to=2026-10-13&by=umbrella',
    expect.anything(),
  );
});
