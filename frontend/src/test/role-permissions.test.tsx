import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RolePermissionsEditor, UserAccessEditor } from '../components/RolePermissionsEditor';
import { api } from '../lib/api';

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
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

describe('role and employee permissions', () => {
  it('shows readable access and saves allowed actions with their scope', async () => {
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (options?.method === 'PUT') return { id: 'role-sales' };
      if (path.startsWith('/admin/roles')) {
        return {
          items: [
            {
              id: 'role-sales',
              name: 'Менеджер продаж',
              version: 2,
              grants: [
                { code: 'requests.read', scope: 'own', allow: true },
                { code: 'finance.purchase.read', scope: 'own', allow: true },
              ],
            },
          ],
          total: 1,
        };
      }
      if (path === '/admin/permissions') {
        return {
          items: [
            { code: 'requests.read', name: 'Просмотр заявок' },
            { code: 'finance.purchase.read', name: 'Просмотр закупочных цен' },
            { code: 'admin.users', name: 'Управление пользователями и ролями' },
          ],
        };
      }
      throw new Error(`Unexpected API request: ${path}`);
    });
    render(<RolePermissionsEditor />);
    fireEvent.click(await screen.findByRole('button', { name: 'Менеджер продаж' }));
    expect(screen.getByLabelText('Просматривать заявки: доступ')).toHaveValue('allow');
    expect(screen.getByLabelText('Просматривать заявки: область доступа')).toHaveValue('own');
    expect(screen.getByLabelText('Смотреть закупочные цены: область доступа')).toHaveValue('own');
    expect(screen.queryByText('requests.read')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Просматривать заявки: область доступа'), {
      target: { value: 'all' },
    });
    fireEvent.change(screen.getByLabelText('Управлять сотрудниками и ролями: доступ'), {
      target: { value: 'allow' },
    });
    fireEvent.change(screen.getByLabelText('Смотреть закупочные цены: область доступа'), {
      target: { value: 'all' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить роль' }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/admin/roles/role-sales',
        expect.objectContaining({
          method: 'PUT',
          body: expect.objectContaining({
            name: 'Менеджер продаж',
            version: 2,
            grants: expect.arrayContaining([
              { code: 'requests.read', scope: 'all', allow: true },
              { code: 'finance.purchase.read', scope: 'all', allow: true },
              { code: 'admin.users', scope: 'all', allow: true },
            ]),
          }),
        }),
      ),
    );
  });

  it('preserves personal denies and sends new exceptions with employee roles', async () => {
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (options?.method === 'PATCH') return { id: 'employee-1' };
      if (path === '/admin/roles') {
        return { items: [{ id: 'role-sales', name: 'Менеджер продаж', grants: [] }] };
      }
      if (path === '/admin/permissions') {
        return {
          items: [
            { code: 'finance.profit.read', name: 'Плановая доходность' },
            { code: 'exports.download', name: 'Экспорт и скачивание' },
          ],
        };
      }
      if (path === '/users') return { items: [] };
      throw new Error(`Unexpected API request: ${path}`);
    });
    render(
      <UserAccessEditor
        user={{
          id: 'employee-1',
          version: 4,
          name: 'Ирина',
          email: 'irina@example.com',
          active: true,
          roles: [{ id: 'role-sales', name: 'Менеджер продаж' }],
          grants: [{ code: 'finance.profit.read', scope: 'all', allow: false }],
        }}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );
    expect(await screen.findByLabelText('Смотреть плановую доходность: доступ')).toHaveValue(
      'deny',
    );
    fireEvent.change(screen.getByLabelText('Скачивать документы и данные: доступ'), {
      target: { value: 'deny' },
    });
    fireEvent.change(screen.getByLabelText('Причина изменения *'), {
      target: { value: 'Ограничить доступ к выгрузке' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить доступ' }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/admin/users/employee-1',
        expect.objectContaining({
          method: 'PATCH',
          body: expect.objectContaining({
            version: 4,
            role_ids: ['role-sales'],
            reason: 'Ограничить доступ к выгрузке',
            grants: expect.arrayContaining([
              { code: 'finance.profit.read', scope: 'all', allow: false },
              { code: 'exports.download', scope: 'all', allow: false },
            ]),
          }),
        }),
      ),
    );
  });
});
