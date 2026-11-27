import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CalculationBreakdown } from '../pages/calculations/CalculationBreakdown';

vi.mock('../app/Auth', () => ({ useAuth: () => ({ can: () => true }) }));

it('shows monetary costs per unit while retaining exchange rates, percentages and unit prices', () => {
  render(
    <CalculationBreakdown
      snapshot={{
        id: 'calculation',
        lines: [
          {
            line_id: 'line',
            description: 'Реактив',
            quantity: '10',
            total: '1220',
            purchase_currency: 'USD',
            quote_item: { unit_price: '25' },
            detail: {
              purchase_foreign: '250',
              purchase_rub: '500',
              exchange_rate: '2',
              international_logistics: '100',
              cost: '700',
              duty_per_unit: '7',
              sale_unit_gross: '122',
              markup_coefficient: '1.5',
              profitability_percent: '42',
              cost_profitability_percent: '70',
              fixed_group_quantity: '20',
            },
            expense_details: { Доставка: '100' },
          },
        ],
        totals: { total: '1220' },
      }}
    />,
  );
  const summary = screen.getByText(/Реактив · 10 шт. · 122,00 ₽\/шт./);
  fireEvent.click(summary);
  const details = summary.closest('details')!;
  const value = (label: string) =>
    [...details.querySelectorAll('dt')].find((dt) => dt.textContent?.replace(' ⓘ', '') === label)
      ?.nextElementSibling;
  expect(value('Закупка, ₽')).toHaveTextContent('50,00');
  expect(value('Закупка в валюте')).toHaveTextContent('25,00');
  expect(value('Международная логистика, ₽')).toHaveTextContent('10,00');
  expect(value('Курс к ₽')).toHaveTextContent('2,00');
  expect(value('Пошлина за единицу, ₽')).toHaveTextContent('7,00');
  expect(value('Цена за штуку с НДС, ₽')).toHaveTextContent('122,00');
  expect(value('Рентабельность, %')).toHaveTextContent('42,00');
  expect(value('Количество в группе пошлины, шт.')).toHaveTextContent('20,00');
  expect(value('Доставка, ₽/шт.')).toHaveTextContent('10,00');
  expect(within(details).getByText(/25 USD × 1 шт. × курс 2 = 50,00 ₽ за 1 шт./)).toBeVisible();
});
