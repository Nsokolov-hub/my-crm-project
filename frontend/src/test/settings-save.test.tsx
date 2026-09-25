import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Settings } from '../pages/Settings';
import { api } from '../lib/api';

vi.mock('../app/Auth', () => ({
  useAuth: () => ({ can: () => true, session: { user: { name: 'Администратор' } } }),
}));
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  const browserCrypto = globalThis.crypto;
  vi.stubGlobal('crypto', { getRandomValues: browserCrypto.getRandomValues.bind(browserCrypto) });
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (options?.method === 'POST') return { id: 'created' };
    if (path.startsWith('/admin/roles'))
      return { items: [{ id: 'role-sales', name: 'Менеджер продаж' }], total: 1 };
    return { items: [], total: 0 };
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('settings save on HTTP origins', () => {
  it('creates an organization with requisites', async () => {
    render(<Settings />);
    fireEvent.click(screen.getByRole('button', { name: 'Добавить организацию' }));
    fireEvent.change(screen.getByLabelText(/Название организации/), {
      target: { value: 'ООО Тест' },
    });
    fireEvent.change(screen.getByLabelText('ИНН'), { target: { value: '1234567890' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/sellers',
        expect.objectContaining({
          method: 'POST',
          body: {
            name: 'ООО Тест',
            currency: 'RUB',
            details: { ИНН: '1234567890' },
          },
        }),
      ),
    );
  });

  it('creates a new employee with a selected role', async () => {
    render(<Settings />);
    fireEvent.click(screen.getByRole('button', { name: 'Сотрудники' }));
    fireEvent.click(screen.getByRole('button', { name: 'Создать доступ' }));
    fireEvent.change(screen.getByLabelText(/Имя сотрудника/), {
      target: { value: 'Ирина Иванова' },
    });
    fireEvent.change(screen.getByLabelText(/Рабочая почта/), {
      target: { value: 'irina@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/Начальный пароль/), {
      target: { value: 'long-test-password' },
    });
    await screen.findByRole('option', { name: 'Менеджер продаж' });
    await userEvent.selectOptions(screen.getByLabelText('Роли'), 'role-sales');
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/admin/users',
        expect.objectContaining({
          method: 'POST',
          body: {
            name: 'Ирина Иванова',
            email: 'irina@example.com',
            password: 'long-test-password',
            role_ids: ['role-sales'],
          },
        }),
      ),
    );
  });
});
