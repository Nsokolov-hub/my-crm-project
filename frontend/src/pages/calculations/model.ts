import { normalizeLogisticsExpense } from '../../lib/logistics';
import type { Entity } from '../../lib/types';

export type Selection = {
  quantity?: string;
  quote_item_id: string;
  markup_coefficient: string;
  bonus_coefficient?: string;
};
export type Rate = {
  currency: string;
  management_per_unit: string;
  quoted_units: string;
  date: string;
  source: string;
  reason: string;
};
export type Expense = {
  name: string;
  amount: string;
  currency: string;
  method: string;
  basis: string;
  stage: string;
  scope: string;
  calculation_type: string;
  minimum_amount: string;
  minimum_currency: string;
  percent_base?: string;
  brackets?: Record<string, unknown>[];
  include_in_cost: boolean;
  include_in_cash: boolean;
};
export const nameOf = (row: Entity) => String(row.nomenclature_name || row.description || 'Товар');
export const currencyOf = (row: Entity) => String(row.currency_code || row.currency || '');
export const positive = (value: string) => Number.isFinite(Number(value)) && Number(value) > 0;
// Quantities arrive as stored decimals ("4.000000"); the editor shows them as people type them.
export const plainQuantity = (value: unknown) => {
  const raw = String(value ?? '');
  return raw.includes('.') ? raw.replace(/\.?0+$/, '') : raw;
};
export const validMarkup = (value: string) => Number.isFinite(Number(value)) && Number(value) >= 1;
export const detailLabels: Record<string, string> = {
  purchase_foreign: 'Закупка в валюте',
  exchange_rate: 'Курс к ₽',
  purchase_rub: 'Закупка, ₽',
  international_logistics: 'Международная логистика, ₽',
  domestic_logistics: 'Логистика РФ — вывоз из аэропорта, ₽',
  client_delivery: 'Доставка клиенту — СДЭК и Москва, ₽',
  domestic_logistics_total: 'Логистика внутри РФ всего, ₽',
  customs_base: 'Таможенная стоимость, ₽',
  duty: 'Пошлина, ₽',
  duty_per_unit: 'Пошлина за единицу, ₽',
  customs_fee: 'Таможенный сбор, ₽',
  customs_fee_1: 'Сбор 1 — по шкале, ₽',
  customs_fee_2: 'Сбор 2 — колонки, ₽',
  customs_fee_per_unit: 'Сбор за единицу, ₽',
  general_expenses: 'Прочие расходы, ₽',
  expenses_total: 'Все распределённые расходы, ₽',
  cash_expenses: 'Денежные расходы, ₽',
  import_vat: 'Ввозной НДС, ₽',
  import_vat_base: 'База ввозного НДС (ННБ), ₽',
  import_cost_with_vat: 'Таможенная стоимость с пошлиной и ввозным НДС, ₽',
  clean_cost: 'Стоимость с пошлиной и невозмещаемым НДС, ₽',
  pre_bonus_sale_net: 'Цена до бонуса без НДС, ₽',
  cost_before_financing: 'Себестоимость до финансирования, ₽',
  financed_amount: 'Финансируемая сумма, ₽',
  financing_cost: 'Стоимость финансирования, ₽',
  cost_before_adjustment: 'Затраты до бонуса, ₽',
  internal_bonus: 'Бонус, ₽',
  service_fee: 'Сервисная комиссия, ₽',
  cost: 'Себестоимость, ₽',
  bonus_withdrawal_percent: 'Комиссия за вывод бонуса, %',
  sale_unit_gross: 'Цена за штуку с НДС, ₽',
  bonus_withdrawal_fee: 'Комиссия за вывод бонуса, ₽',
  additional_service_fee: 'Дополнительная комиссия, ₽',
  fixed_group_quantity: 'Количество в группе пошлины, шт.',
  markup_coefficient: 'Коэффициент наценки',
  markup_base: 'База наценки, ₽',
  cost_after_markup: 'Цена с наценкой, ₽',
  markup_amount: 'Прибавка от наценки, ₽',
  bonus_coefficient: 'Коэффициент бонуса',
  sale_net: 'Продажа без НДС, ₽',
  sale_tax: 'НДС продажи, ₽',
  sale_total: 'Продажа с НДС, ₽',
  profit: 'Валовая прибыль, ₽',
  vat_payable: 'НДС к уплате, ₽',
  cash_need: 'Денежные затраты на заказ, ₽',
  profitability_percent: 'Рентабельность, %',
  cost_profitability_percent: 'Доходность затрат, %',
  expense_share_percent: 'Доля расходов, %',
  investment_efficiency_percent: 'Эффективность средств, %',
};

export function expenseFromReference(row: Record<string, unknown>): Expense {
  return normalizeLogisticsExpense({
    name: String(row.name || ''),
    amount: String(row.amount ?? row.default_value ?? '0'),
    currency: String(row.currency || row.currency_code || 'RUB'),
    method: String(row.method || row.distribution_method || 'BY_QUANTITY'),
    basis: String(row.basis || row.name || 'Расход сделки'),
    stage: String(row.stage || 'GENERAL'),
    scope: String(row.scope || 'WAVE'),
    calculation_type: String(row.calculation_type || 'FIXED'),
    minimum_amount: String(row.minimum_amount ?? '0'),
    minimum_currency: String(row.minimum_currency || 'RUB'),
    percent_base: row.percent_base ? String(row.percent_base) : undefined,
    brackets: Array.isArray(row.brackets) ? (row.brackets as Record<string, unknown>[]) : [],
    include_in_cost: row.include_in_cost !== false,
    include_in_cash: row.include_in_cash !== false,
  });
}

export const standardExpenses: Expense[] = [
  expenseFromReference({ name: 'Декларант', amount: '25000', currency: 'RUB' }),
  expenseFromReference({ name: 'Терминальная обработка', amount: '10000', currency: 'RUB' }),
  expenseFromReference({
    name: 'Валютный контроль',
    amount: '0.18',
    currency: 'RUB',
    calculation_type: 'PERCENTAGE',
    percent_base: 'CUSTOMS_BASE',
    minimum_amount: '30',
    minimum_currency: 'USD',
  }),
  expenseFromReference({
    name: 'Комиссия за платёж',
    amount: '0.29',
    currency: 'RUB',
    calculation_type: 'PERCENTAGE',
    percent_base: 'CUSTOMS_BASE',
  }),
];
export const moscowDelivery = expenseFromReference({
  name: 'Доставка клиенту в Москве',
  amount: '5000',
  currency: 'RUB',
  scope: 'REQUEST',
  stage: 'CLIENT_DELIVERY',
});
export const airportPickup = expenseFromReference({
  name: 'Логистика РФ (вывоз из аэропорта)',
  amount: '0',
  currency: 'RUB',
  stage: 'DOMESTIC_LOGISTICS',
  scope: 'WAVE',
});
