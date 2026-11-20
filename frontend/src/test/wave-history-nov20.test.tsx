import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { Modal } from '../components/ui';
import { api } from '../lib/api';
import { queryClient } from '../lib/queryClient';
import { WaveHistory, WaveHistoryTab } from '../pages/WaveHistory';

vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

const event = (id: string, comment: string) => ({
  id,
  created_at: '2026-11-20T08:00:00Z',
  actor_name: 'Тимур',
  title: 'Волна изменена',
  changes: [{ label: 'Маршрут', before: 'Старый', after: 'Новый' }],
  details: null,
  comment,
});

function WaveCard() {
  const [open, setOpen] = useState(false);
  return (
    <Modal title="Волна Sabiha 48" className="wave-modal" onClose={() => {}}>
      <WaveHistoryTab expanded={open} onOpen={() => setOpen(true)} />
      {open && <WaveHistory waveId="w1" number="Sabiha 48" onClose={() => setOpen(false)} />}
    </Modal>
  );
}

it('opens the side history with its own accessible title and loads the rest without replacing previous changes', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (String(path) === '/waves/w1/history')
      return {
        items: [event('h2', 'Комментарий первой страницы')],
        has_more: true,
        next_cursor: 'oldest/with+space',
      };
    if (String(path) === '/waves/w1/history?cursor=oldest%2Fwith%2Bspace')
      return {
        items: [event('h1', 'Комментарий второй страницы')],
        has_more: false,
        next_cursor: null,
      };
    throw new Error(`Unexpected request ${path}`);
  });
  render(<WaveCard />);
  const tab = screen.getByRole('button', { name: 'История изменений волны' });
  expect(tab).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(tab);
  const history = await screen.findByRole('dialog', { name: 'История изменений · Sabiha 48' });
  expect(screen.getByRole('dialog', { name: 'Волна Sabiha 48' })).toBeVisible();
  expect(tab).toHaveAttribute('aria-expanded', 'true');
  expect(await within(history).findByText('Комментарий первой страницы')).toBeVisible();
  fireEvent.click(within(history).getByRole('button', { name: 'Показать ещё изменения' }));
  expect(await within(history).findByText('Комментарий второй страницы')).toBeVisible();
  expect(within(history).getByText('Комментарий первой страницы')).toBeVisible();
  expect(within(history).getByText('Показано изменений: 2')).toBeVisible();
  expect(within(history).queryByRole('button', { name: 'Показать ещё изменения' })).toBeNull();
  fireEvent.click(within(history).getByRole('button', { name: 'Закрыть' }));
  expect(screen.queryByRole('dialog', { name: 'История изменений · Sabiha 48' })).toBeNull();
  expect(screen.getByRole('dialog', { name: 'Волна Sabiha 48' })).toBeVisible();
  fireEvent.click(tab);
  fireEvent(
    await screen.findByRole('dialog', { name: 'История изменений · Sabiha 48' }),
    new Event('cancel', { bubbles: true, cancelable: true }),
  );
  expect(screen.queryByRole('dialog', { name: 'История изменений · Sabiha 48' })).toBeNull();
  expect(screen.getByRole('dialog', { name: 'Волна Sabiha 48' })).toBeVisible();
});
