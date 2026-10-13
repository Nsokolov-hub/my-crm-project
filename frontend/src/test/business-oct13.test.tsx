import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { Clients } from '../pages/Clients';

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

const duplicate = {
  id: 'duplicate',
  internal_code: 1659,
  name: 'ВЕКТОР ООО',
  kind: 'client',
  owner_id: 'leader',
  version: 3,
  archived: false,
};
const main = { ...duplicate, id: 'main', internal_code: 1001, name: 'ООО Вектор', version: 7 };

function renderClients() {
  render(
    <MemoryRouter>
      <Clients />
    </MemoryRouter>,
  );
}

it('archives a counterparty with a reason and offers it back from the archive tab', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/counterparties/duplicate/archive' && options?.method === 'POST')
      return { ...duplicate, archived: true, version: 4 };
    if (path.startsWith('/counterparties?') && path.includes('archived=true'))
      return { items: [{ ...duplicate, archived: true, version: 4 }], total: 1 };
    if (path.startsWith('/counterparties?')) return { items: [duplicate], total: 1 };
    return { items: [], total: 0 };
  });
  renderClients();
  fireEvent.click(await screen.findByText('ВЕКТОР ООО'));
  fireEvent.click(screen.getByRole('button', { name: 'В архив' }));
  fireEvent.change(screen.getByLabelText(/^Причина/), { target: { value: 'Компания закрылась' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/counterparties/duplicate/archive',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          version: 3,
          archived: true,
          reason: 'Компания закрылась',
          idempotency_key: expect.any(String),
        }),
      }),
    ),
  );
  expect(await screen.findByText(/Контрагент в архиве/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Вернуть из архива' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Редактировать' })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
  fireEvent.click(screen.getByRole('button', { name: 'Архив' }));
  await waitFor(() =>
    expect(
      vi
        .mocked(api)
        .mock.calls.some(
          ([path]) => path.includes('/counterparties?') && path.includes('archived=true'),
        ),
    ).toBe(true),
  );
});

it('merges a duplicate into the main card and reports what moved', async () => {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/counterparties/duplicate/merge' && options?.method === 'POST')
      return {
        ...main,
        version: 8,
        moved: { requests: 2, contacts: 1, calls: 3, documents: 0, tasks: 1 },
      };
    if (path.startsWith('/counterparties?')) return { items: [duplicate, main], total: 2 };
    return { items: [], total: 0 };
  });
  renderClients();
  fireEvent.click(await screen.findByText('ВЕКТОР ООО'));
  fireEvent.click(screen.getByRole('button', { name: 'Объединить с основной' }));
  expect(screen.getByText(/«ВЕКТОР ООО» уйдёт в архив/)).toBeVisible();
  fireEvent.focus(screen.getByLabelText(/^Основная карточка/));
  fireEvent.click(await screen.findByRole('option', { name: '1001 · ООО Вектор' }));
  fireEvent.change(screen.getByLabelText(/^Причина/), { target: { value: 'Дубль из импорта' } });
  fireEvent.click(screen.getByRole('button', { name: 'Объединить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/counterparties/duplicate/merge',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          version: 3,
          target_id: 'main',
          reason: 'Дубль из импорта',
        }),
      }),
    ),
  );
  expect(
    await screen.findByText(
      'Дубль объединён. Перенесено: заявок — 2, контактов — 1, звонков — 3, документов — 0, задач — 1.',
    ),
  ).toBeVisible();
  expect(screen.getByRole('heading', { name: 'ООО Вектор' })).toBeVisible();
});

it('shows a merged duplicate with a link to the main card and no way back', async () => {
  const merged = {
    ...duplicate,
    archived: true,
    merged_into_id: 'main',
    merged_into_name: 'ООО Вектор',
    version: 4,
  };
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/counterparties/main') return main;
    if (path.startsWith('/counterparties?')) return { items: [merged], total: 1 };
    return { items: [], total: 0 };
  });
  renderClients();
  fireEvent.click(screen.getByRole('button', { name: 'Архив' }));
  expect(await screen.findByText('ООО Вектор')).toBeVisible();
  fireEvent.click(screen.getByText('ВЕКТОР ООО'));
  expect(screen.getByText(/Дубль объединён с «ООО Вектор»/)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Вернуть из архива' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Объединить с основной' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Открыть основную карточку' }));
  expect(await screen.findByRole('heading', { name: 'ООО Вектор' })).toBeVisible();
});
