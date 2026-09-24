import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RecordForm } from '../components/Form';
import { api } from '../lib/api';
import { requestEditFields } from '../lib/fields';
import { Tasks } from '../pages/Clients';

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

describe('everyday CRM forms', () => {
  it('keeps the stored UTC deadline when editing an existing task', async () => {
    const task = {
      id: 'task-1',
      title: 'Перезвонить клиенту',
      status: 'assigned',
      priority: 'normal',
      due_at: '2026-09-21T12:00:00Z',
      version: 1,
    };
    vi.mocked(api).mockImplementation(async (_path, options) =>
      options?.method === 'PATCH' ? task : { items: [task], total: 1 },
    );
    render(<Tasks />);
    fireEvent.click(await screen.findByRole('button', { name: 'Перезвонить клиенту' }));
    const due = screen.getByLabelText(/Срок/);
    const date = new Date(task.due_at);
    const expectedLocal = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
    expect(due).toHaveValue(expectedLocal);
    fireEvent.change(screen.getByLabelText(/Название/), {
      target: { value: 'Обновить договорённость' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/tasks/task-1',
        expect.objectContaining({
          method: 'PATCH',
          body: expect.objectContaining({ due_at: '2026-09-21T12:00:00.000Z' }),
        }),
      ),
    );
  });

  it('submits the configured loss reason separately from the edit reason', async () => {
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (options?.method === 'PATCH') return { id: 'request-1' };
      if (path.startsWith('/dictionaries/loss_reasons')) {
        return { items: [{ id: 'no_budget', name: 'Нет бюджета' }] };
      }
      return { items: [] };
    });
    render(
      <RecordForm
        title="Изменить заявку"
        fields={requestEditFields}
        endpoint="/requests/request-1"
        method="PATCH"
        initial={{ title: 'Поставка', commercial_stage: 'new' }}
        extra={{ version: 1 }}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );
    await screen.findByRole('option', { name: 'Нет бюджета' });
    fireEvent.change(screen.getByLabelText('Коммерческий этап'), {
      target: { value: 'closed_lost' },
    });
    fireEvent.change(screen.getByLabelText('Причина закрытия без продажи'), {
      target: { value: 'no_budget' },
    });
    fireEvent.change(screen.getByLabelText(/Причина изменения/), {
      target: { value: 'Клиент отменил закупку' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/requests/request-1',
        expect.objectContaining({
          method: 'PATCH',
          body: expect.objectContaining({
            commercial_stage: 'closed_lost',
            loss_reason: 'no_budget',
            reason: 'Клиент отменил закупку',
          }),
        }),
      ),
    );
  });
});
