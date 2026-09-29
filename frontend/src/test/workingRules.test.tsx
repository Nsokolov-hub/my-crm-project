import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WorkingRulesEditor } from '../components/WorkingRulesEditor';
import { ApiError, api } from '../lib/api';

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: vi.fn(),
}));
vi.mock('../app/Auth', () => ({
  useAuth: () => ({ can: () => true }),
}));

const metadata = {
  call_results: { is_supported: false },
  loss_reasons: { is_supported: true },
  commercial_rules: { is_supported: false },
};
const rows = [
  {
    id: 'call-setting',
    key: 'call_results',
    value: { results: ['callback', 'interested'] },
    version: 4,
  },
  { id: 'loss-setting', key: 'loss_reasons', value: { reasons: ['no_budget'] }, version: 2 },
  { id: 'unused-setting', key: 'commercial_rules', value: {}, version: 1 },
];

beforeEach(() => {
  vi.clearAllMocks();
});

function ruleSection(name: string) {
  const section = screen.getByRole('heading', { name }).closest('section');
  if (!section) throw new Error(`Раздел ${name} не найден`);
  return within(section);
}

describe('Правила работы', () => {
  it('shows fixed call results while keeping editable loss reasons', async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/settings/meta') return metadata;
      if (path === '/settings') return { items: rows, total: rows.length };
      throw new Error(`Unexpected path: ${path}`);
    });

    render(<WorkingRulesEditor />);
    expect(await screen.findByRole('heading', { name: 'Результаты звонка' })).toBeInTheDocument();
    expect(
      ruleSection('Результаты звонка').getByText('Отправлена презентация'),
    ).toBeInTheDocument();
    expect(
      ruleSection('Результаты звонка').queryByRole('button', { name: 'Изменить список' }),
    ).not.toBeInTheDocument();
    expect(ruleSection('Причины отказа').getByText('Нет бюджета')).toBeInTheDocument();
    expect(screen.queryByText('commercial_rules')).not.toBeInTheDocument();
    expect(screen.queryByText('{}')).not.toBeInTheDocument();

    fireEvent.click(ruleSection('Причины отказа').getByRole('button', { name: 'Изменить список' }));
    fireEvent.change(ruleSection('Причины отказа').getByLabelText('Готовый вариант'), {
      target: { value: 'price_too_high' },
    });
    fireEvent.click(
      ruleSection('Причины отказа').getByRole('button', { name: 'Добавить вариант' }),
    );
    fireEvent.click(
      ruleSection('Причины отказа').getByRole('button', { name: 'Сохранить список' }),
    );

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/settings',
        expect.objectContaining({
          method: 'POST',
          body: {
            key: 'loss_reasons',
            value: { reasons: ['no_budget', 'price_too_high'] },
            version: 2,
          },
        }),
      ),
    );
  });

  it('does not overwrite a concurrent change and offers to reload', async () => {
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === '/settings/meta') return metadata;
      if (path === '/settings' && options?.method === 'POST')
        throw new ApiError(409, 'STALE_VERSION', 'Запись уже изменена другим сотрудником.');
      if (path === '/settings') return { items: rows, total: rows.length };
      throw new Error(`Unexpected path: ${path}`);
    });

    render(<WorkingRulesEditor />);
    await screen.findByRole('heading', { name: 'Результаты звонка' });
    fireEvent.click(ruleSection('Причины отказа').getByRole('button', { name: 'Изменить список' }));
    fireEvent.change(ruleSection('Причины отказа').getByLabelText('Или свой вариант'), {
      target: { value: 'Нет согласования' },
    });
    fireEvent.click(
      ruleSection('Причины отказа').getByRole('button', { name: 'Добавить вариант' }),
    );
    fireEvent.click(
      ruleSection('Причины отказа').getByRole('button', { name: 'Сохранить список' }),
    );
    expect(
      await ruleSection('Причины отказа').findByRole('button', {
        name: 'Загрузить актуальный список',
      }),
    ).toBeInTheDocument();
    expect(
      ruleSection('Причины отказа').getByRole('button', { name: 'Сохранить список' }),
    ).toBeDisabled();
  });
});
