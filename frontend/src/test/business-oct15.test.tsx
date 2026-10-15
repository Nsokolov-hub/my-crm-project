import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { useAuth } from '../app/Auth';
import { api } from '../lib/api';
import { Analytics, AnalyticsReport } from '../pages/Analytics';
import { Tasks } from '../pages/Tasks';

vi.mock('../app/Auth', () => ({ useAuth: vi.fn() }));
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

function signIn(permissions: string[]) {
  vi.mocked(useAuth).mockReturnValue({
    session: {
      user: {
        id: 'me',
        name: 'Тимур',
        email: 'timur@example.com',
        active: true,
        own_requests_only: false,
        version: 1,
        mfa_enabled: false,
        roles: [],
        grants: [],
        permissions: {},
      },
      csrf_token: 'test',
      permissions: {},
    },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    can: (code: string) => permissions.includes(code),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

it('shows a sales manager only the sales by managers report', () => {
  signIn(['analytics.read', 'finance.profit.read']);
  render(
    <MemoryRouter>
      <Analytics />
    </MemoryRouter>,
  );
  expect(screen.getByRole('link', { name: /Продажи по менеджерам/ })).toHaveAttribute(
    'href',
    '/analytics/sales-managers',
  );
  expect(screen.queryByRole('link', { name: /Производители и бренды/ })).not.toBeInTheDocument();
  expect(screen.getAllByRole('link')).toHaveLength(1);
});

it('opens a report on its own page with a period and a chart switch', async () => {
  signIn(['analytics.read', 'analytics.reports', 'finance.profit.read']);
  vi.mocked(api).mockResolvedValue({
    basis: 'Принятые позиции',
    items: [
      { id: 'a', manager: 'Тимур', currency: 'RUB', sales: '105744', sale_net: '86674.99' },
      { id: 'b', manager: 'Александр', currency: 'RUB', sales: '221844', sale_net: '181839.34' },
    ],
  });
  render(
    <MemoryRouter initialEntries={['/analytics/sales-managers']}>
      <Routes>
        <Route path="/analytics/:report" element={<AnalyticsReport />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByRole('heading', { name: 'Продажи по менеджерам', level: 1 })).toBeVisible();
  expect(screen.getByLabelText('Период')).toHaveValue('month');
  expect(screen.getByLabelText('Показывать графики')).toBeChecked();
  const chart = await screen.findByRole('figure', { name: 'Продажи без НДС' });
  expect(within(chart).getByText('Александр')).toBeVisible();
});

it('groups tasks by due date with the count of each group', async () => {
  signIn(['tasks.read', 'tasks.write']);
  vi.mocked(api).mockImplementation(async (path) =>
    String(path).startsWith('/users')
      ? { items: [{ id: 'me', name: 'Тимур' }], total: 1 }
      : {
          items: [
            {
              id: 't1',
              title: 'Обзвонить клиента',
              status: 'assigned',
              priority: 'normal',
              due_at: '2026-09-30T06:14:00Z',
              group: 'overdue',
              due_group: 'overdue',
              client_name: 'ООО ТСМЕД',
            },
            {
              id: 't2',
              title: 'Запрос квот',
              status: 'assigned',
              priority: 'normal',
              due_at: '2026-10-28T12:56:00Z',
              group: 'later',
              due_group: 'later',
            },
          ],
          total: 2,
          groups: [
            { key: 'overdue', label: 'Просрочено', count: 1 },
            { key: 'later', label: 'Позже', count: 1 },
          ],
        },
  );
  render(
    <MemoryRouter>
      <Tasks />
    </MemoryRouter>,
  );
  const heading = await screen.findByRole('columnheader', { name: /Просрочено/ });
  expect(heading).toHaveTextContent('Просрочено1');
  expect(screen.getByRole('columnheader', { name: /Позже/ })).toBeVisible();
  expect(api).toHaveBeenCalledWith(expect.stringContaining('group=due'), expect.anything());
  expect(screen.getByLabelText('Группировка задач')).toHaveValue('due');
});
