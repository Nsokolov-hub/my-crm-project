import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { SearchSelect } from '../components/SearchSelect';
import { TableImportDialog } from '../components/TableImportDialog';
import { api } from '../lib/api';
import { queryClient } from '../lib/queryClient';
import { ApprovalDecision } from '../pages/Fulfillment';
import { WaveHistory } from '../pages/WaveHistory';

// Business remarks of 13 November 2026: suppliers are found by their first letters, the decision window shows each
// position's supplier, quote term and profitability with the expenses of the calculation, a wave shows the history
// of its changes, rows of a checked Excel file are removed before writing.
vi.mock('../app/Auth', () => ({ useAuth: () => ({ can: () => true }) }));
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

const SUPPLIERS = [
  'CHEMICEA PHARMACEUTICALS',
  'ООО "ТД" ХИММЕД',
  'Pharmaffiliates Pvt Ltd',
  'PHARMANANDA',
  'SimSon Pharma Ltd',
];

function Supplier() {
  const [value, setValue] = useState('');
  return (
    <>
      <SearchSelect
        label="Фильтр: поставщик"
        allLabel="Все поставщики"
        value={value}
        options={SUPPLIERS}
        onChange={setValue}
      />
      <output>{value || 'все'}</output>
    </>
  );
}

it('finds a supplier by the first letters of any word and picks it with Enter', () => {
  render(<Supplier />);
  const input = screen.getByRole('combobox', { name: 'Фильтр: поставщик' });
  fireEvent.focus(input);
  expect(screen.getAllByRole('option')).toHaveLength(SUPPLIERS.length + 1);

  fireEvent.change(input, { target: { value: 'хим' } });
  expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
    'ООО "ТД" ХИММЕД',
  ]);
  // Names that begin with the letters come first, then names with a word that does, then the rest.
  fireEvent.change(input, { target: { value: 'pharma' } });
  expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
    'Pharmaffiliates Pvt Ltd',
    'PHARMANANDA',
    'CHEMICEA PHARMACEUTICALS',
    'SimSon Pharma Ltd',
  ]);
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(screen.getByRole('status')).toHaveTextContent('Pharmaffiliates Pvt Ltd');
  expect(input).toHaveValue('Pharmaffiliates Pvt Ltd');

  fireEvent.click(screen.getByRole('button', { name: 'Сбросить: Фильтр: поставщик' }));
  expect(screen.getByRole('status')).toHaveTextContent('все');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'нет такого' } });
  expect(screen.getByText('Совпадений нет')).toBeVisible();
});

it('shows each position with its supplier, quote term and profitability and the expenses', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (String(path) === '/approvals/a1/waves')
      return {
        items: [{ id: 'w1', label: 'Рабочая · ФФ 52', supplier_id: 's1' }],
        wave_id: 'w1',
        required: true,
        lines: [
          { execution_id: 'e1', supplier_id: 's1', label: 'N-Boc-amino — 2 pcs' },
          { execution_id: 'e2', supplier_id: 's1', label: 'Oxcarbazepine — 1 pcs' },
        ],
        positions: [
          {
            execution_id: 'e1',
            description: 'N-Boc-amino',
            quantity: '2',
            unit: 'pcs',
            supplier_name: 'Pharmaffiliates Pvt Ltd',
            delivery_days: 30,
            profitability_percent: '18.5',
          },
          {
            execution_id: 'e2',
            description: 'Oxcarbazepine',
            quantity: '1',
            unit: 'pcs',
            supplier_name: 'Pharmaffiliates Pvt Ltd',
            delivery_days: null,
            profitability_percent: '-2.25',
          },
        ],
        expenses: [
          {
            name: 'Общая логистика',
            method: 'BY_QUANTITY',
            currency: 'RUB',
            amount: '1000',
            share: '200',
          },
          {
            name: 'Таможенный брокер',
            method: 'BY_PURCHASE_VALUE',
            currency: 'RUB',
            amount: '500',
            share: '150.5',
          },
        ],
      };
    return {};
  });
  render(
    <ApprovalDecision row={{ id: 'a1', version: 1 }} onClose={() => {}} onSuccess={() => {}} />,
  );

  const table = (await screen.findByText('Позиции на согласовании')).parentElement as HTMLElement;
  const rows = within(table).getAllByRole('row').slice(1);
  expect(within(rows[0]).getByText('N-Boc-amino — 2 pcs')).toBeVisible();
  expect(within(rows[0]).getByText('Pharmaffiliates Pvt Ltd')).toBeVisible();
  expect(within(rows[0]).getByText('30 дн.')).toBeVisible();
  expect(within(rows[0]).getByText('18,50 %')).toBeVisible();
  expect(
    within(rows[1]).getByText('−2,25 %', { normalizer: (text) => text.replace('-', '−') }),
  ).toBeVisible();

  const expenses = screen.getByText('Расходы, заложенные в расчёт').parentElement as HTMLElement;
  expect(within(expenses).getByText('Общая логистика')).toBeVisible();
  expect(within(expenses).getByText('по закупочной стоимости')).toBeVisible();
  expect(within(expenses).getByText(/350,50 ₽/)).toBeVisible();
  // Each position's own wave choice names its supplier and term.
  expect(screen.getByText('Pharmaffiliates Pvt Ltd · срок по квоте 30 дн.')).toBeVisible();
});

it('lists the changes of a wave with their comments', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (String(path) === '/waves/w1/history')
      return {
        items: [
          {
            id: 'h2',
            created_at: '2026-11-13T09:30:00Z',
            actor_name: 'Тимур',
            title: 'Волна изменена',
            changes: [{ label: 'Прибытие', before: 'неделя 52, 2026', after: 'неделя 53, 2026' }],
            details: null,
            comment: 'Поставщик сдвинул отгрузку',
          },
          {
            id: 'h1',
            created_at: '2026-11-10T09:30:00Z',
            actor_name: 'Олейников',
            title: 'Заявка №124 перешла в волну',
            changes: [],
            details: 'из волны «ФФ 51»',
            comment: null,
          },
        ],
      };
    return {};
  });
  render(<WaveHistory waveId="w1" number="ФФ 52" onClose={() => {}} />);
  expect(await screen.findByText('Волна изменена')).toBeVisible();
  expect(screen.getByText('неделя 53, 2026')).toBeVisible();
  expect(screen.getByText('Поставщик сдвинул отгрузку')).toBeVisible();
  expect(screen.getByText('Заявка №124 перешла в волну')).toBeVisible();
  expect(screen.getByText(/Тимур/)).toBeVisible();
});

it('removes a row of the checked file before it is written', async () => {
  const row = (number: number, name: string) => ({
    row_number: number,
    action: 'checked',
    errors: [],
    data: { description: name },
  });
  const batch = (rows: ReturnType<typeof row>[]) => ({
    id: 'b1',
    status: 'preview',
    columns: ['Наименование'],
    mapping: { description: 'Наименование' },
    rows,
    summary: { total: rows.length, checked: rows.length, create_nomenclature: 0, errors: 0 },
    result: {},
  });
  vi.mocked(api).mockImplementation(async (path) => {
    if (String(path).endsWith('/table-imports/preview'))
      return batch([row(2, 'Ацетонитрил'), row(3, 'Лишняя'), row(4, 'Метанол')]);
    if (String(path).endsWith('/remove-rows'))
      return batch([row(2, 'Ацетонитрил'), row(4, 'Метанол')]);
    return {};
  });
  render(<TableImportDialog requestId="r1" kind="items" onClose={() => {}} onSuccess={() => {}} />);
  const input = screen.getByLabelText('Файл таблицы');
  fireEvent.change(input, { target: { files: [new File(['x'], 'table.xlsx')] } });
  fireEvent.submit(input.closest('form')!);
  fireEvent.click(await screen.findByRole('button', { name: 'Убрать строку 3' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/requests/r1/table-imports/b1/remove-rows',
      expect.objectContaining({ body: { row_numbers: [3] } }),
    ),
  );
  await waitFor(() => expect(screen.queryByText('Лишняя')).toBeNull());
  expect(screen.getByRole('button', { name: 'Записать позиции: 2' })).toBeVisible();
});
