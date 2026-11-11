import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { LineCommand } from '../components/LineCommand';
import { api } from '../lib/api';
import { queryClient } from '../lib/queryClient';
import { Registry } from '../pages/Registry';
import { RequestCalculations } from '../pages/RequestCalculations';
import { WavePositions } from '../pages/WavePositions';
import { WavesTable } from '../pages/WavesTable';

// Business remarks of 11 November 2026: the waves table sorts and filters, the wave positions filter by supplier
// and manufacturer with the term of the quote, all positions are chosen at once, approvals have names and the
// calculation shows its buyer.
vi.mock('../app/Auth', () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

const wave = (id: string, number: string, supplier: string, status: string, arrival: string) => ({
  id,
  kind: 'work',
  number,
  supplier_name: supplier,
  route: 'Китай-РФ',
  status,
  close_date: '2026-12-14',
  departure_date: '2026-12-21',
  arrival_date: arrival,
});

function names() {
  return screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[0].textContent);
}

it('sorts the waves table by any column and filters it by supplier, state and dates', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (String(path).startsWith('/waves?'))
      return {
        items: [
          wave('a', 'Аснаил 6', 'Beijing Asnail', 'assembling', '2026-12-28'),
          wave('b', 'Аозеал 5', 'Shenzhen Aozeal', 'planned', '2026-11-23'),
          wave('c', 'ФФ 1', 'Pharmaffiliates', 'shipped', '2027-01-11'),
        ],
      };
    if (path === '/me/preferences/waves.filters') return { value: null };
    return {};
  });
  render(<WavesTable onOpen={() => undefined} />);
  await screen.findByText('Аснаил 6');
  // Earliest arrival first by default.
  expect(names()).toEqual(['Аозеал 5', 'Аснаил 6', 'ФФ 1']);
  fireEvent.click(screen.getByRole('button', { name: /Поставщик/ }));
  expect(names()).toEqual(['Аснаил 6', 'ФФ 1', 'Аозеал 5']);
  fireEvent.click(screen.getByRole('button', { name: /Поставщик/ }));
  expect(names()).toEqual(['Аозеал 5', 'ФФ 1', 'Аснаил 6']);
  expect(screen.getByRole('columnheader', { name: /Поставщик/ })).toHaveAttribute(
    'aria-sort',
    'descending',
  );

  fireEvent.change(screen.getByLabelText('Фильтр: состояние'), { target: { value: 'planned' } });
  expect(names()).toEqual(['Аозеал 5']);
  fireEvent.change(screen.getByLabelText('Фильтр: состояние'), { target: { value: '' } });
  fireEvent.change(screen.getByLabelText('Фильтр: поставщик'), {
    target: { value: 'Pharmaffiliates' },
  });
  expect(names()).toEqual(['ФФ 1']);
  fireEvent.change(screen.getByLabelText('Фильтр: поставщик'), { target: { value: '' } });
  // Arrival in December: the week of 28 December counts, 11 January does not.
  fireEvent.change(screen.getByLabelText('Срок с'), { target: { value: '2026-12-01' } });
  fireEvent.change(screen.getByLabelText('Срок по'), { target: { value: '2026-12-31' } });
  expect(names()).toEqual(['Аснаил 6']);

  fireEvent.click(screen.getByRole('button', { name: 'Сохранить фильтры' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/me/preferences/waves.filters',
      expect.objectContaining({
        method: 'PUT',
        body: { value: expect.objectContaining({ from: '2026-12-01', to: '2026-12-31' }) },
      }),
    ),
  );
});

it('filters wave positions by supplier and manufacturer, shows the quote term and opens the wave', async () => {
  const openWave = vi.fn();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/me/preferences/wave-positions.filters')
      return { value: { supplier: 'Aozeal', sort: 'delivery_days', direction: 'desc' } };
    if (String(path).startsWith('/waves/positions'))
      return {
        items: [
          {
            id: 'p1',
            name: 'Oxaliplatin Impurity B',
            manufacturer: 'Aozeal',
            supplier_name: 'Aozeal',
            delivery_days: 45,
            request_id: 'r22',
            request_number: '22',
            wave_id: 'w1',
            wave_number: 'Рабочая Аозеал 5',
            status: 'order',
            status_label: 'Заказ подтверждён',
          },
          {
            id: 'p2',
            name: '7,8-Dihydrofolic acid',
            manufacturer: 'Aozeal',
            supplier_name: 'Aozeal',
            delivery_days: 60,
            request_id: 'r22',
            request_number: '22',
            wave_id: 'w1',
            wave_number: 'Рабочая Аозеал 5',
            status: 'order',
            status_label: 'Заказ подтверждён',
          },
        ],
        total: 2,
        statuses: { order: 'Заказ подтверждён' },
        waves: [{ id: 'w1', number: 'Рабочая Аозеал 5' }],
        suppliers: ['Aozeal', 'Pharmaffiliates'],
        manufacturers: ['Aozeal', 'Pharmaffiliates'],
      };
    return {};
  });
  render(
    <MemoryRouter>
      <WavePositions onOpenWave={openWave} />
    </MemoryRouter>,
  );
  // The saved filters apply when the list opens.
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith('/waves/positions?supplier=Aozeal', expect.anything()),
  );
  expect(await screen.findByText('Срок синтеза по квоте, дн.')).toBeVisible();
  const rows = screen.getAllByRole('row').slice(1);
  expect(within(rows[0]).getByText('60')).toBeVisible();
  expect(screen.getByText('Позиции открыты с сохранёнными фильтрами.')).toBeVisible();

  fireEvent.change(screen.getByLabelText('Фильтр: производитель'), {
    target: { value: 'Pharmaffiliates' },
  });
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/waves/positions?supplier=Aozeal&manufacturer=Pharmaffiliates',
      expect.anything(),
    ),
  );
  fireEvent.click(screen.getAllByRole('button', { name: 'Рабочая Аозеал 5' })[0]);
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/waves/positions?wave_id=w1&supplier=Aozeal&manufacturer=Pharmaffiliates',
      expect.anything(),
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Открыть волну «Рабочая Аозеал 5»' }));
  expect(openWave).toHaveBeenCalledWith('w1');
});

it('chooses every position the client accepted at once', async () => {
  vi.mocked(api).mockResolvedValue({});
  const lines = [
    { id: 'l1', name: 'Barbaloin (Aloin) 100 mg', quantity: '1', unit: 'pcs' },
    { id: 'l2', name: 'Zinc bisglycinate 100 mg', quantity: '2', unit: 'pcs' },
    { id: 'l3', name: 'Lecithin 100 mg', quantity: '1', unit: 'pcs' },
  ];
  render(
    <LineCommand
      title="Зафиксировать принятие состава клиентом"
      endpoint="/proposals/p/accept"
      rows={lines}
      rowKey="line_id"
      reasonRequired
      includeAnalogue
      onClose={() => undefined}
      onSuccess={() => undefined}
    />,
  );
  expect(screen.getByText('Выбрано: 0 из 3')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать все (3)' }));
  expect(screen.getByText('Выбрано: 3 из 3')).toBeVisible();
  expect(screen.getAllByRole('checkbox').every((box) => (box as HTMLInputElement).checked)).toBe(
    true,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Снять все' }));
  expect(screen.getByText('Выбрано: 0 из 3')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать все (3)' }));
  fireEvent.change(screen.getByLabelText('Основание решения'), {
    target: { value: 'Клиент принял всё' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/proposals/p/accept',
      expect.objectContaining({
        body: expect.objectContaining({
          lines: [
            { line_id: 'l1', quantity: '1', analogue_reason: '' },
            { line_id: 'l2', quantity: '2', analogue_reason: '' },
            { line_id: 'l3', quantity: '1', analogue_reason: '' },
          ],
        }),
      }),
    ),
  );
});

it('names an approval by its request, client, suppliers and wave', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (String(path).startsWith('/approvals?'))
      return {
        items: [
          {
            id: 'f92c6ca5-0000',
            status: 'pending',
            created_at: '2026-11-10T12:39:00Z',
            request_id: 'r144',
            title: 'Заявка №144 · АЛИКВОТА ООО',
            details: '23 позиции · Beijing Asnail · волна Рабочая Аснаил 6',
            reviewer_name: 'Александр Олейников',
          },
        ],
      };
    return { items: [] };
  });
  render(
    <MemoryRouter>
      <Registry kind="approvals" />
    </MemoryRouter>,
  );
  expect(await screen.findByText('Заявка №144 · АЛИКВОТА ООО')).toBeVisible();
  expect(screen.getByText('23 позиции · Beijing Asnail · волна Рабочая Аснаил 6')).toBeVisible();
  expect(screen.queryByText(/f92c6ca5/)).not.toBeInTheDocument();
});

it('shows the buyer and the wave on top of a new calculation', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/waves?open=true')
      return {
        items: [
          {
            id: 'aozeal',
            kind: 'plan',
            number: 'Плановая Аозеал 52 неделя',
            status: 'planned',
            archived: false,
          },
        ],
      };
    return { items: [] };
  });
  render(
    <MemoryRouter>
      <RequestCalculations
        request={{
          id: 'request',
          version: 4,
          number: '22',
          title: 'Импурити',
          client_name: 'ООО "ВЕРОФАРМ"',
          wave_id: 'aozeal',
        }}
      />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: /Новый расчёт/ }));
  expect(
    await screen.findByRole('heading', { name: 'Новый расчёт · ООО "ВЕРОФАРМ"' }),
  ).toBeVisible();
  const head = document.querySelector('.calculation-head') as HTMLElement;
  expect(within(head).getByText('ООО "ВЕРОФАРМ"')).toBeVisible();
  expect(within(head).getByText('№22 · Импурити')).toBeVisible();
  await waitFor(() => expect(within(head).getByLabelText('Волна расчёта')).toHaveValue('aozeal'));
});
