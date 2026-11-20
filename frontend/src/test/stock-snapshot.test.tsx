import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { StockSnapshot } from '../components/StockSnapshot';

const snapshot = {
  version: 1,
  captured_at: '2026-10-18T17:10:00Z',
  availability: 'in_stock',
  origin: 'supplier',
  packaging_match: 'exact',
  offers: [
    {
      source: 'chimmed',
      source_name: 'Химмед',
      offer_id: 'offer-1',
      catalog_number: 'SKU-100G',
      pack: '100 g',
      match: 'exact_pack',
      seen_at: '2026-10-17T10:00:00Z',
      valid_until: '2026-10-27T10:00:00Z',
      quantity: null,
      url: 'https://supplier.test/offer',
    },
  ],
};

it('shows historical supplier evidence with no editable or purchasing controls', () => {
  render(<StockSnapshot value={snapshot} />);
  expect(screen.getByText('Было у поставщика')).toBeVisible();
  expect(screen.getByText(/На момент заявки:/)).toBeVisible();
  fireEvent.click(screen.getByText('Источники наличия (1)'));
  expect(screen.getByRole('link', { name: 'Химмед' })).toHaveAttribute(
    'href',
    'https://supplier.test/offer',
  );
  expect(screen.getByText('Количество не указано')).toBeVisible();
  expect(screen.getByText('Совпала фасовка')).toBeVisible();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});

it('requires manager confirmation for product-only matches', () => {
  render(
    <StockSnapshot
      value={{ ...snapshot, availability: 'unknown', packaging_match: 'product_only' }}
    />,
  );
  expect(screen.getByText('Нужно уточнить')).toBeVisible();
  expect(screen.getByText(/Фасовка не подтверждена/)).toBeVisible();
});

it('keeps old requests unknown and does not render unsafe source links', () => {
  const view = render(<StockSnapshot value={null} />);
  expect(screen.getByText('Наличие не зафиксировано')).toBeVisible();
  view.rerender(
    <StockSnapshot
      value={{ ...snapshot, offers: [{ ...snapshot.offers[0], url: 'javascript:alert(1)' }] }}
    />,
  );
  fireEvent.click(screen.getByText('Источники наличия (1)'));
  expect(screen.getByText(/^Химмед/)).toBeVisible();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});
