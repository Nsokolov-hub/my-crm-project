import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Layout } from '../app/Layout';
import { useAuth } from '../app/Auth';

vi.mock('../app/Auth', () => ({ useAuth: vi.fn() }));
vi.mock('../lib/hooks', () => ({
  useApi: () => ({ data: { unread: 0 }, loading: false, error: undefined }),
}));

function renderLayout(permissions: string[]) {
  vi.mocked(useAuth).mockReturnValue({
    session: {
      user: { id: 'user-1', name: 'Тестовый сотрудник', email: 'test@example.com' },
      csrf_token: 'test',
    },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    can: (code: string) => permissions.includes(code),
  });
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<div>Главная страница</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('navigation access', () => {
  it('shows an administrator only modules that their read rights open', () => {
    renderLayout([
      'admin.users',
      'admin.settings',
      'clients.read',
      'catalog.read',
      'chats.use',
      'tasks.read',
    ]);
    expect(screen.getByRole('link', { name: 'Рабочий стол' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Клиенты' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'База обзвона' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Номенклатура' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Настройки' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Заявки' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Документы' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Аналитика' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Поиск заявок')).not.toBeInTheDocument();
  });

  it('keeps request registries and search available to a sales manager', () => {
    renderLayout([
      'requests.read',
      'clients.read',
      'tasks.read',
      'catalog.read',
      'analytics.read',
      'chats.use',
    ]);
    for (const label of [
      'Заявки',
      'Документы',
      'Оплаты',
      'Согласования',
      'Волны поставок',
      'Аналитика',
    ]) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByLabelText('Поиск заявок')).toBeInTheDocument();
  });
});
