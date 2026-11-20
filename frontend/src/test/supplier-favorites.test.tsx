import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { Clients } from '../pages/Clients';
import { SupplierMailEditor } from '../components/SupplierMailEditor';
import { api } from '../lib/api';

vi.mock('../app/Auth', () => ({
  useAuth: () => ({ can: (permission: string) => permission === 'clients.read' }),
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

const supplier = {
  id: 'supplier',
  name: 'Sabiha',
  kind: 'supplier',
  email: 'rfq@example.com',
  owner_name: 'Менеджер',
  is_favorite: false,
};

it('lets an employee mark a supplier in its card without editing the shared counterparty', async () => {
  let favorite = false;
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.endsWith('/favorite') && options?.method === 'PUT') {
      favorite = Boolean((options.body as { favorite: boolean }).favorite);
      return { id: supplier.id, is_favorite: favorite };
    }
    if (path.startsWith('/counterparties?'))
      return { items: [{ ...supplier, is_favorite: favorite }], total: 1 };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <Clients />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Sabiha'));
  const card = screen.getByRole('dialog');
  expect(within(card).queryByRole('button', { name: 'Редактировать' })).not.toBeInTheDocument();
  fireEvent.click(within(card).getByRole('button', { name: 'Добавить Sabiha в избранное' }));
  await waitFor(() =>
    expect(
      within(card).getByRole('button', { name: 'Убрать Sabiha из избранного' }),
    ).toHaveAttribute('aria-pressed', 'true'),
  );
  expect(api).toHaveBeenCalledWith(
    '/counterparties/supplier/favorite',
    expect.objectContaining({ method: 'PUT', body: { favorite: true } }),
  );
  expect(vi.mocked(api).mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(false);
});

it('keeps a delayed preference response on its original card after another supplier is opened', async () => {
  const second = { ...supplier, id: 'second', name: 'Merck' };
  let resolveFavorite: ((value: { id: string; is_favorite: boolean }) => void) | undefined;
  const pending = new Promise<{ id: string; is_favorite: boolean }>((resolve) => {
    resolveFavorite = resolve;
  });
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.endsWith('/favorite') && options?.method === 'PUT') return pending;
    if (path.startsWith('/counterparties?')) return { items: [supplier, second], total: 2 };
    return { items: [], total: 0 };
  });
  render(
    <MemoryRouter>
      <Clients />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Sabiha'));
  fireEvent.click(screen.getByRole('button', { name: 'Добавить Sabiha в избранное' }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Закрыть' }));
  fireEvent.click(screen.getByText('Merck'));
  await act(async () => {
    resolveFavorite?.({ id: supplier.id, is_favorite: true });
    await pending;
  });
  expect(
    within(screen.getByRole('dialog')).getByRole('button', { name: 'Добавить Merck в избранное' }),
  ).toHaveAttribute('aria-pressed', 'false');
  expect(
    screen.queryByRole('button', { name: 'Убрать Merck из избранного' }),
  ).not.toBeInTheDocument();
});

it('adds a favorite through the regular recipient path, deduplicates it and never sends mail', async () => {
  let favorite = true;
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.endsWith('/favorite') && options?.method === 'PUT') {
      favorite = Boolean((options.body as { favorite: boolean }).favorite);
      return { id: supplier.id, is_favorite: favorite };
    }
    if (path === '/counterparties/supplier') return { ...supplier, is_favorite: favorite };
    if (path.endsWith('/preview'))
      return { items: [{ recipient: supplier.email, body: 'Позиция из заявки' }] };
    if (path.includes('favorite=true'))
      return {
        items: favorite ? [{ ...supplier, is_favorite: true }] : [],
        total: favorite ? 1 : 0,
      };
    return { items: [{ ...supplier, is_favorite: favorite }], total: 1 };
  });
  render(
    <SupplierMailEditor
      requestId="request"
      itemIds={['item']}
      onClose={vi.fn()}
      onSuccess={vi.fn()}
    />,
  );
  const shortcuts = screen.getByRole('region', { name: 'Избранные поставщики' });
  fireEvent.click(await within(shortcuts).findByRole('button', { name: 'Sabiha' }));
  await screen.findByText(supplier.email);
  expect(within(shortcuts).getByRole('button', { name: 'Sabiha · добавлен' })).toBeDisabled();
  fireEvent.focus(screen.getByRole('combobox'));
  fireEvent.click(await screen.findByRole('option', { name: 'Sabiha' }));
  fireEvent.click(screen.getByRole('button', { name: 'Добавить получателя' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Добавить получателя' })).toBeDisabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Предпросмотр' }));
  await screen.findByText('Позиция из заявки');
  expect(api).toHaveBeenCalledWith(
    '/requests/request/supplier-mail/preview',
    expect.objectContaining({ body: expect.objectContaining({ supplier_ids: ['supplier'] }) }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Убрать Sabiha из избранного' }));
  await within(shortcuts).findByText(/Отметьте поставщика звёздочкой/);
  expect(screen.getByText(supplier.email)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Отправить отдельные письма/ })).toBeInTheDocument();
  expect(vi.mocked(api).mock.calls.some(([path]) => path.endsWith('/send'))).toBe(false);
});

it('provides all favorite suppliers through pagination instead of silently truncating the list', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.includes('favorite=true')) {
      const second = new URL(path, 'https://test.invalid').searchParams.get('page') === '2';
      return {
        items: second
          ? [{ ...supplier, id: 's13', name: 'Поставщик 13' }]
          : Array.from({ length: 12 }, (_, index) => ({
              ...supplier,
              id: `s${index + 1}`,
              name: `Поставщик ${index + 1}`,
            })),
        total: 13,
      };
    }
    return { items: [], total: 0 };
  });
  render(
    <SupplierMailEditor
      requestId="request"
      itemIds={['item']}
      onClose={vi.fn()}
      onSuccess={vi.fn()}
    />,
  );
  const shortcuts = screen.getByRole('region', { name: 'Избранные поставщики' });
  await within(shortcuts).findByRole('button', { name: 'Поставщик 12' });
  fireEvent.click(within(shortcuts).getByRole('button', { name: 'Следующая страница' }));
  await within(shortcuts).findByRole('button', { name: 'Поставщик 13' });
  expect(api).toHaveBeenCalledWith(
    '/counterparties?kind=supplier&favorite=true&page=2&page_size=12',
    expect.anything(),
  );
});
