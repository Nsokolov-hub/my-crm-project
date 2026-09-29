import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { Collection } from '../components/Collection';
import { api } from '../lib/api';

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

it('applies call-base column filters and search only after Enter', async () => {
  vi.mocked(api).mockImplementation(async () => ({
    items: [{ id: 'client-1', name: 'Альфа' }],
    total: 1,
  }));

  render(
    <Collection
      title="Клиенты для обзвона"
      endpoint="/counterparties"
      columns={[{ key: 'name', label: 'Название' }]}
      filterKeys={['name']}
    />,
  );
  await screen.findByText('Альфа');
  const column = screen.getByRole('textbox', { name: 'Фильтр: Название' });
  const search = screen.getByRole('textbox', { name: 'Поиск: Клиенты для обзвона' });
  const initialCalls = vi.mocked(api).mock.calls.length;

  fireEvent.change(column, { target: { value: 'Клиент' } });
  fireEvent.change(search, { target: { value: 'Москва' } });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 350));
  });
  expect(api).toHaveBeenCalledTimes(initialCalls);

  fireEvent.keyDown(column, { key: 'Enter' });
  await waitFor(() => expect(vi.mocked(api).mock.calls.length).toBe(initialCalls + 1));
  const submitted = new URLSearchParams(
    String(vi.mocked(api).mock.calls.at(-1)?.[0]).split('?')[1],
  );
  expect(submitted.get('filter_name')).toBe('Клиент');
  expect(submitted.get('q')).toBe('Москва');

  await screen.findByText('Альфа');
  fireEvent.change(screen.getByRole('textbox', { name: 'Фильтр: Название' }), {
    target: { value: '' },
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 350));
  });
  expect(api).toHaveBeenCalledTimes(initialCalls + 1);
  fireEvent.keyDown(search, { key: 'Enter' });
  await waitFor(() => expect(vi.mocked(api).mock.calls.length).toBe(initialCalls + 2));
  const cleared = new URLSearchParams(String(vi.mocked(api).mock.calls.at(-1)?.[0]).split('?')[1]);
  expect(cleared.get('filter_name')).toBe('');
});
