import type { Entity } from '../../lib/types';
import {
  currencyOf,
  nameOf,
  positive,
  validMarkup,
  type Expense,
  type Rate,
  type Selection,
} from './model';

/** What the calculation editor holds when it checks whether a calculation can be requested. */
export type CalculationDraft = {
  waveId: unknown;
  rows: Entity[];
  selections: Record<string, Selection>;
  waveExpenses: { loading: boolean; error: unknown };
  profileId: string;
  profileReady: boolean;
  profile?: Record<string, unknown>;
  canReadReward: boolean;
  currencies: string[];
  rates: Rate[];
  expenses: Expense[];
  prepayment: string;
  deferred: string;
  deferredDays: string;
  deferredStart: string;
  deliveryDays: string;
  serviceFeePercent: string;
};

/** Reasons why the calculation cannot be requested yet, in the order the editor shows them. */
export function calculationErrors(draft: CalculationDraft): string[] {
  const errors: string[] = [];
  if (
    !draft.waveId &&
    draft.rows.some((row) => !row.calculation_type || row.calculation_type === 'IMPORT')
  )
    errors.push('Руководитель должен назначить волну поставки в заявке.');
  if (draft.waveExpenses.loading) errors.push('Загружаются расходы волны.');
  if (draft.waveExpenses.error)
    errors.push(
      'Не удалось загрузить расходы волны. Проверьте тариф поставщика и повторно откройте расчёт.',
    );
  if (!draft.profileId) errors.push('Не выбран профиль расчёта.');
  if (draft.profileId && !draft.profileReady) errors.push('Загружаются расходы профиля.');
  if (!Object.keys(draft.selections).length) errors.push('Выберите хотя бы одну позицию квоты.');
  if (Object.keys(draft.selections).length > draft.rows.length)
    errors.push('Часть выбранных квот не загружена. Откройте их в списке и повторите выбор.');
  draft.rows.forEach((row) => {
    const title = nameOf(row) + ' ' + String(row.packing_name || '');
    if (!currencyOf(row)) errors.push('Для позиции ' + title + ' отсутствует валюта закупки.');
    if (!row.product_group_slug)
      errors.push('Для позиции ' + title + ' не определена товарная группа.');
    if (row.unit_price == null)
      errors.push('Для позиции ' + title + ' отсутствует закупочная цена.');
    if (row.delivery_days == null)
      errors.push('Для позиции ' + title + ' не указан срок поставки.');
    if (
      !Number.isInteger(Number(draft.selections[row.id].quantity)) ||
      Number(draft.selections[row.id].quantity) <= 0
    )
      errors.push('Количество фасовок позиции ' + title + ' должно быть целым числом больше нуля.');
    if (!validMarkup(draft.selections[row.id].markup_coefficient))
      errors.push('Наценка позиции ' + title + ' должна быть не меньше 1.');
    if (
      draft.canReadReward &&
      !validMarkup(
        String(
          draft.selections[row.id].bonus_coefficient ||
            draft.profile?.default_bonus_coefficient ||
            '1',
        ),
      )
    )
      errors.push('Коэффициент бонуса позиции ' + title + ' должен быть не меньше 1.');
    if (row.expired) errors.push('Срок действия цены позиции ' + title + ' истёк.');
  });
  draft.currencies.forEach((currency) => {
    const rate = draft.rates.find((entry) => entry.currency === currency);
    if (!rate) errors.push('Для ' + currency + ' отсутствует курс к RUB.');
    else if (
      !positive(rate.management_per_unit) ||
      !positive(rate.quoted_units) ||
      !rate.date ||
      !rate.source.trim()
    )
      errors.push('Заполните курс ' + currency + ', дату и источник.');
  });
  draft.expenses.forEach((expense, index) => {
    const amountInvalid = !Number.isFinite(Number(expense.amount)) || Number(expense.amount) < 0;
    const minimumInvalid =
      !Number.isFinite(Number(expense.minimum_amount)) || Number(expense.minimum_amount) < 0;
    if (
      !expense.name.trim() ||
      !expense.basis.trim() ||
      (expense.calculation_type !== 'BRACKET' && amountInvalid) ||
      (expense.calculation_type === 'PERCENTAGE' && minimumInvalid) ||
      (['PERCENTAGE', 'BRACKET'].includes(expense.calculation_type) && !expense.percent_base) ||
      (expense.calculation_type === 'BRACKET' && !expense.brackets?.length)
    )
      errors.push('Заполните название, сумму и основание расхода №' + (index + 1) + '.');
  });
  if (
    !Number.isFinite(Number(draft.prepayment)) ||
    !Number.isFinite(Number(draft.deferred)) ||
    Number(draft.prepayment) < 0 ||
    Number(draft.deferred) < 0 ||
    Number(draft.prepayment) + Number(draft.deferred) !== 100
  )
    errors.push('Предоплата и отсрочка должны суммарно составлять 100%.');
  if (!Number.isInteger(Number(draft.deferredDays)) || Number(draft.deferredDays) < 0)
    errors.push('Срок отсрочки укажите целым числом дней.');
  if (
    draft.deliveryDays !== '' &&
    (!Number.isInteger(Number(draft.deliveryDays)) ||
      Number(draft.deliveryDays) < 0 ||
      Number(draft.deliveryDays) > 3650)
  )
    errors.push('Общий срок поставки укажите целым числом от 0 до 3650 дней.');
  if (Number(draft.deferred) > 0 && !draft.deferredStart)
    errors.push('Выберите событие начала отсрочки.');
  if (!Number.isFinite(Number(draft.serviceFeePercent)) || Number(draft.serviceFeePercent) < 0)
    errors.push('Сервисная комиссия должна быть неотрицательным числом.');
  return errors;
}
