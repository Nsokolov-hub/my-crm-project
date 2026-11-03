import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { UserAccessEditor } from '../components/RolePermissionsEditor';
import { Settings } from '../pages/Settings';
import { api } from '../lib/api';

// 3 November 2026: an employee forgot the password, and the roles column showed raw JSON.
vi.mock('../app/Auth', () => ({
  useAuth: () => ({ can: () => true, session: { user: { name: 'Администратор' } } }),
}));
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

const employee = {
  id: 'u-oan',
  version: 3,
  name: 'Александр Олейников',
  email: 'oan@ogk-chem.ru',
  active: true,
  roles: [
    { id: 'role-admin', name: 'Администратор' },
    { id: 'role-sales', name: 'Менеджер продаж' },
  ],
  grants: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === 'POST') return { ok: true };
    if (path.startsWith('/admin/users')) return { items: [employee], total: 1 };
    if (path.startsWith('/admin/roles')) return { items: [], total: 0 };
    if (path === '/admin/permissions') return { items: [] };
    return { items: [], total: 0 };
  });
});

it('lists the names of the assigned roles', async () => {
  render(<Settings />);
  fireEvent.click(screen.getByRole('button', { name: 'Сотрудники' }));
  expect(await screen.findByText('Администратор, Менеджер продаж')).toBeInTheDocument();
  expect(screen.queryByText(/"id"/)).not.toBeInTheDocument();
});

it('sets a generated password for an employee and returns to the access form', async () => {
  render(<UserAccessEditor user={employee} onClose={vi.fn()} onSuccess={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Задать новый пароль' }));
  const dialog = screen.getByRole('dialog', { name: 'Новый пароль: Александр Олейников' });
  expect(dialog).toHaveTextContent('Старый пароль посмотреть нельзя');

  fireEvent.click(within(dialog).getByRole('button', { name: 'Сгенерировать' }));
  const field = within(dialog).getByLabelText('Новый пароль');
  const password = (field as HTMLInputElement).value;
  expect(password).toMatch(/^[A-HJ-NP-Za-km-z2-9]{16}$/);

  fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить пароль' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/admin/users/u-oan/password',
      expect.objectContaining({ method: 'POST', body: { new_password: password } }),
    ),
  );
  expect(await within(dialog).findByRole('status')).toHaveTextContent('Пароль сохранён');
  expect(field).toHaveAttribute('readonly');

  fireEvent.click(within(dialog).getByRole('button', { name: 'Готово' }));
  expect(
    screen.getByRole('dialog', { name: 'Доступ сотрудника: Александр Олейников' }),
  ).toBeVisible();
});
