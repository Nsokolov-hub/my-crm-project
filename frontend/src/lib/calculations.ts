import { decimal, metric } from './format';
import type { Column, CommerceSummary, Entity } from './types';

/** Present historical snapshots without relabelling their cost-based return as sales profitability. */
export function displayCalculationMetrics(values: Record<string, unknown>) {
  const result = { ...values };
  if (
    values.cost_profitability_percent === undefined &&
    values.profitability_percent !== undefined
  ) {
    result.cost_profitability_percent = values.profitability_percent;
    delete result.profitability_percent;
  }
  if (values.margin_percent !== undefined) result.profitability_percent = values.margin_percent;
  delete result.margin_percent;
  return result;
}

function filled(value: unknown) {
  return value !== null && value !== undefined && value !== '';
}

function commerceAmount(amount: unknown, currency: unknown) {
  if (!filled(amount)) return '—';
  return `${decimal(amount)}${filled(currency) ? ` ${String(currency)}` : ''}`;
}

/** The same read-only commercial columns for calculations, proposals and invoices. */
export function commerceColumns<T extends CommerceSummary>(): Column<T>[] {
  return [
    {
      key: 'supplier_names',
      label: 'Поставщик',
      sortable: false,
      render: (row) => row.supplier_names?.filter(Boolean).join(', ') || '—',
    },
    {
      key: 'delivery_days',
      label: 'Срок',
      sortable: false,
      render: (row) => (filled(row.delivery_days) ? `${decimal(row.delivery_days)} дн.` : '—'),
    },
    {
      key: 'profitability_percent',
      label: 'Рентабельность',
      sortable: false,
      render: (row) =>
        filled(row.profitability_percent) ? `${metric(row.profitability_percent)} %` : '—',
    },
    {
      key: 'purchase_amount',
      label: 'Сумма закупа',
      sortable: false,
      render: (row) => commerceAmount(row.purchase_amount, row.purchase_currency),
    },
    {
      key: 'sale_amount',
      label: 'Сумма продажи',
      sortable: false,
      render: (row) =>
        commerceAmount(row.sale_amount ?? row.total, row.sale_currency ?? row.currency),
    },
  ];
}

export type TransportGroup = { value: string; count: number };
export type TransportSummary = {
  hazards: TransportGroup[];
  temperatures: TransportGroup[];
};

function addGroup(groups: Map<string, number>, value: string) {
  groups.set(value, (groups.get(value) || 0) + 1);
}

export function visibleTransportHazard(value: unknown): string {
  const hazard = String(value ?? '').trim();
  return hazard.toLocaleLowerCase('ru-RU') === 'не опасный' ? '' : hazard;
}

/** Count product positions, not their quantities, for the selected quotes' transport note. */
export function selectedTransportSummary(rows: Entity[]): TransportSummary {
  const hazards = new Map<string, number>();
  const temperatures = new Map<string, number>();
  for (const row of rows) {
    const hazard = visibleTransportHazard(row.transport_hazard);
    if (hazard) addGroup(hazards, hazard);
    addGroup(temperatures, String(row.storage_temperature ?? '').trim() || 'не указана');
  }
  const groups = (values: Map<string, number>) =>
    [...values].map(([value, count]) => ({ value, count }));
  return { hazards: groups(hazards), temperatures: groups(temperatures) };
}

export function positionsLabel(count: number) {
  const lastTwo = count % 100;
  const last = count % 10;
  const word =
    lastTwo >= 11 && lastTwo <= 14
      ? 'позиций'
      : last === 1
        ? 'позиция'
        : last >= 2 && last <= 4
          ? 'позиции'
          : 'позиций';
  return `${count} ${word}`;
}
