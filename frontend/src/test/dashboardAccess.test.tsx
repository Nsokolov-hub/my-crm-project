import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { Dashboard } from '../pages/Dashboard';
import { api } from '../lib/api';

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: vi.fn(),
}));
vi.mock('../app/Auth', () => ({
  useAuth: () => ({
    session: { user: { name: 'Тестовый администратор' } },
    can: (code: string) =>
      ['clients.read', 'catalog.read', 'tasks.read', 'admin.settings'].includes(code),
  }),
}));

describe('рабочий стол без права на аналитику', () => {
  it('показывает доступные разделы без запрещённого запроса к статистике', () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: 'Доступные разделы' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Клиенты/ })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Создать заявку/ })).not.toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });
});
