import { describe, expect, it } from 'vitest';
import { expenseFromReference } from '../pages/calculations/model';
import { calculationErrors, type CalculationDraft } from '../pages/calculations/validation';

function draft(changes: Partial<CalculationDraft> = {}): CalculationDraft {
  return {
    waveId: 'wave',
    rows: [
      {
        id: 'q1',
        nomenclature_name: 'Реактив',
        packing_name: '1 кг',
        currency_code: 'USD',
        product_group_slug: 'reagents',
        unit_price: '10',
        delivery_days: 30,
        calculation_type: 'IMPORT',
      },
    ],
    selections: { q1: { quote_item_id: 'q1', quantity: '2', markup_coefficient: '1.5' } },
    waveExpenses: { loading: false, error: undefined },
    profileId: 'profile',
    profileReady: true,
    profile: { default_bonus_coefficient: '1' },
    canReadReward: true,
    currencies: ['USD'],
    rates: [
      {
        currency: 'USD',
        management_per_unit: '90',
        quoted_units: '1',
        date: '2026-10-01',
        source: 'ЦБ РФ',
        reason: '',
      },
    ],
    expenses: [],
    prepayment: '100',
    deferred: '0',
    deferredDays: '0',
    deferredStart: '',
    deliveryDays: '',
    serviceFeePercent: '0',
    ...changes,
  };
}

describe('calculation checks before a request', () => {
  it('accepts a complete calculation', () => {
    expect(calculationErrors(draft())).toEqual([]);
  });

  it('needs a supply wave for imported positions', () => {
    expect(calculationErrors(draft({ waveId: null }))).toEqual([
      'Руководитель должен назначить волну поставки в заявке. Поставщику по DAP или внутри РФ волна не нужна: укажите это в его карточке («Тип расчёта»).',
    ]);
    const rows = [{ ...draft().rows[0], supplier_name: 'Pharmaffiliates Pvt Ltd' }];
    expect(calculationErrors(draft({ waveId: null, rows }))[0]).toContain(
      'Импорт: Pharmaffiliates Pvt Ltd.',
    );
  });

  it('calculates DAP and resale in Russia without a wave or its expenses', () => {
    for (const type of ['DAP', 'RUSSIA']) {
      const rows = [{ ...draft().rows[0], calculation_type: type }];
      expect(
        calculationErrors(
          draft({ waveId: null, rows, waveExpenses: { loading: false, error: new Error('Нет') } }),
        ),
      ).toEqual([]);
    }
  });

  it('keeps imports apart from DAP and resale in Russia', () => {
    const rows = [draft().rows[0], { ...draft().rows[0], id: 'q2', calculation_type: 'RUSSIA' }];
    const selections = {
      ...draft().selections,
      q2: { quote_item_id: 'q2', quantity: '1', markup_coefficient: '1.5' },
    };
    expect(calculationErrors(draft({ rows, selections }))).toEqual([
      'Импорт и DAP или перепродажу внутри РФ посчитайте отдельными расчётами: волна и её расходы относятся только к импорту.',
    ]);
  });

  it('checks quantities, markups and the bonus only for those who see rewards', () => {
    const selections = {
      q1: {
        quote_item_id: 'q1',
        quantity: '1.5',
        markup_coefficient: '0.9',
        bonus_coefficient: '0.5',
      },
    };
    expect(calculationErrors(draft({ selections }))).toEqual([
      'Количество фасовок позиции Реактив 1 кг должно быть целым числом больше нуля.',
      'Наценка позиции Реактив 1 кг должна быть не меньше 1.',
      'Коэффициент бонуса позиции Реактив 1 кг должен быть не меньше 1.',
    ]);
    expect(calculationErrors(draft({ selections, canReadReward: false }))).toHaveLength(2);
  });

  it('requires a complete rate for every foreign currency', () => {
    expect(calculationErrors(draft({ rates: [] }))).toEqual(['Для USD отсутствует курс к RUB.']);
    const rate = { ...draft().rates[0], source: ' ' };
    expect(calculationErrors(draft({ rates: [rate] }))).toEqual([
      'Заполните курс USD, дату и источник.',
    ]);
  });

  it('requires payment shares of 100% and a start event for a deferral', () => {
    expect(
      calculationErrors(draft({ prepayment: '50', deferred: '40', deferredStart: 'delivery' })),
    ).toEqual(['Предоплата и отсрочка должны суммарно составлять 100%.']);
    expect(calculationErrors(draft({ prepayment: '50', deferred: '50' }))).toEqual([
      'Выберите событие начала отсрочки.',
    ]);
  });

  it('names the incomplete expense by its number', () => {
    const expenses = [
      expenseFromReference({ name: 'Декларант', amount: '25000' }),
      expenseFromReference({ name: 'Сбор', amount: '-1' }),
    ];
    expect(calculationErrors(draft({ expenses }))).toEqual([
      'Заполните название, сумму и основание расхода №2.',
    ]);
  });
});
