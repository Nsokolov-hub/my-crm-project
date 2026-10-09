import { Plus, Save, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import { Badge, Button, DataTable, DetailPairs, ErrorBox, Modal } from '../components/ui';
import { ApiError } from '../lib/api';
import { displayCalculationMetrics } from '../lib/calculations';
import { normalizeLogisticsExpense } from '../lib/logistics';
import { date, decimal, label, metric } from '../lib/format';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
import { waveWeekLabel } from '../lib/waves';

type Selection = {
  quantity?: string;
  quote_item_id: string;
  markup_coefficient: string;
  bonus_coefficient?: string;
};
type Rate = {
  currency: string;
  management_per_unit: string;
  quoted_units: string;
  date: string;
  source: string;
  reason: string;
};
type Expense = {
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
const nameOf = (row: Entity) => String(row.nomenclature_name || row.description || 'Товар');
const currencyOf = (row: Entity) => String(row.currency_code || row.currency || '');
const positive = (value: string) => Number.isFinite(Number(value)) && Number(value) > 0;
const validMarkup = (value: string) => Number.isFinite(Number(value)) && Number(value) >= 1;
const detailLabels: Record<string, string> = {
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

function CalculationBreakdown({ snapshot }: { snapshot: Entity }) {
  const auth = useAuth();
  const totals = (snapshot.totals || {}) as Entity;
  const lines = (snapshot.lines || []) as Entity[];
  const wave = (snapshot.wave || {}) as Entity;
  const distribution = (snapshot.wave_distribution || {}) as Entity;
  const allocations = (snapshot.expense_allocations || []) as Entity[];
  const rates = (snapshot.rates || []) as Entity[];
  const rows = [
    ...lines.map((line) => ({ ...line, id: String(line.line_id) })),
    ...(Number(distribution.existing_quantity || 0) > 0
      ? [
          {
            id: 'wave-existing',
            description: 'Другие позиции волны',
            quantity: distribution.existing_quantity,
            detail: {},
          },
        ]
      : []),
  ];
  return (
    <div className="calculation-breakdown">
      <h4>Общий расчёт по позициям</h4>
      <div className="calculation-summary">
        <span>
          Итого с НДС: <strong>{metric(totals.total)} ₽</strong>
        </span>
        {auth.can('finance.profit.read') && (
          <span>
            Рентабельность расчёта:{' '}
            <strong>{metric(displayCalculationMetrics(totals).profitability_percent)} %</strong>
          </span>
        )}
      </div>
      <p>
        Волна: {String(wave.number || '—')}. База распределения общих расходов волны:{' '}
        {decimal(distribution.total_quantity || 0)} шт. (
        {decimal(distribution.selected_quantity || 0)} шт. в этом расчёте +{' '}
        {decimal(distribution.existing_quantity || 0)} шт. в закупках +{' '}
        {decimal(distribution.forecast_quantity || 0)} шт. прогноза). Доставка клиенту делится на{' '}
        {decimal(distribution.selected_quantity || 0)} шт. этого расчёта.
      </p>
      <DataTable<Entity>
        className="calculation-overview"
        rows={rows}
        columns={[
          {
            key: 'description',
            label: 'Товар',
            render: (row) => (
              <span className="calculation-product">
                <strong>{String(row.description || '')}</strong>
                <span className="calculation-manufacturer">
                  {String((row.product as Entity | undefined)?.manufacturer || '—')}
                </span>
                <small>Арт. {String((row.product as Entity | undefined)?.article || '—')}</small>
              </span>
            ),
          },
          {
            key: 'supplier_name',
            label: 'Поставщик',
            render: (row) => String(row.supplier_name || '—'),
          },
          {
            key: 'delivery_days',
            label: 'Срок поставки, дней',
            render: (row) => String(row.delivery_days ?? '—'),
          },
          { key: 'quantity', label: 'Кол-во', render: (row) => decimal(row.quantity) },
          ...(['cost', 'sale_total', 'profit'] as const).map((key) => ({
            key,
            label: detailLabels[key],
            render: (row: Entity) =>
              (row.detail as Entity | undefined)?.[key] == null
                ? '—'
                : metric((row.detail as Entity)[key]),
          })),
          {
            key: 'profitability_percent',
            label: 'Рентабельность, %',
            render: (row) =>
              metric(displayCalculationMetrics((row.detail || {}) as Entity).profitability_percent),
          },
        ]}
      />
      <h4>Расшифровка каждой позиции</h4>
      {lines.map((line) => {
        const detail = (line.detail || {}) as Entity;
        const rule = (line.customs_rule || {}) as Entity;
        const expenses = (line.expense_details || {}) as Entity;
        const quote = (line.quote_item || {}) as Entity;
        return (
          <details key={String(line.line_id)}>
            <summary>
              {String(line.description)} · {decimal(line.quantity)} шт. · {decimal(line.total)} ₽
            </summary>
            <p>
              Закупка: {decimal(quote.unit_price)} {String(line.purchase_currency || '')} ×{' '}
              {decimal(line.quantity)} шт. × курс {decimal(detail.exchange_rate)} ={' '}
              {decimal(detail.purchase_rub)} ₽.
            </p>
            <p>
              Пошлина:{' '}
              {rule.type === 'FIXED_GROUP'
                ? `${decimal(rule.value)} ₽ × ${decimal(line.quantity)} / ${decimal(detail.fixed_group_quantity)} шт. группы`
                : rule.type === 'PERCENTAGE'
                  ? `${decimal(detail.customs_base)} ₽ × ${decimal(rule.value)}%`
                  : 'без пошлины'}{' '}
              = {decimal(detail.duty)} ₽.
            </p>
            <DetailPairs
              formulas={{
                'Таможенная стоимость, ₽': 'Закупка в рублях + международная логистика',
                'База ввозного НДС (ННБ), ₽': 'Таможенная стоимость + таможенная пошлина',
                'Ввозной НДС, ₽': 'ННБ × ставка НДС / 100. При вычете в цену до наценки не входит.',
                'НДС продажи, ₽':
                  'Из цены с НДС: сумма × ставка / (100 + ставка). Округление цены вверх входит в налоговую базу.',
                'Таможенная стоимость с пошлиной и ввозным НДС, ₽': 'ННБ + ввозной НДС',
                'Стоимость с пошлиной и невозмещаемым НДС, ₽':
                  'Импорт с вычетом: ННБ. Без вычета: ННБ + ввозной НДС.',
                'Цена до бонуса без НДС, ₽':
                  'Импорт: (таможенная стоимость + пошлина) × коэффициент + расходы + сбор + финансирование; НДС без вычета добавляется отдельно.',
                'База наценки, ₽': 'Импорт: таможенная стоимость + таможенная пошлина',
                'Цена с наценкой, ₽':
                  'База наценки × коэффициент, до добавления перевыставляемых расходов',
                'Логистика РФ — вывоз из аэропорта, ₽':
                  'Общая логистика РФ × количество строки / количество всей волны',
                'Доставка клиенту — СДЭК и Москва, ₽':
                  '(СДЭК + доставка в Москве) × количество строки / количество этого расчёта',
                'Логистика внутри РФ всего, ₽': 'Доля вывоза из аэропорта + доля доставки клиенту',
                'Прибавка от наценки, ₽': 'База наценки × (коэффициент − 1)',
                'Валовая прибыль, ₽': 'Продажа без НДС − себестоимость',
                'Рентабельность, %': 'Валовая прибыль / продажа без НДС × 100%',
                'Доходность затрат, %': 'Валовая прибыль / себестоимость × 100%',
                'Денежные затраты на заказ, ₽':
                  'Полная сумма денежных расходов на выполнение заказа. Предоплата клиента из неё не вычитается.',
              }}
              values={Object.fromEntries(
                Object.entries(displayCalculationMetrics(detail)).map(([key, value]) => [
                  detailLabels[key] || key,
                  metric(value),
                ]),
              )}
            />
            {Object.keys(expenses).length > 0 && (
              <DetailPairs
                values={Object.fromEntries(
                  Object.entries(expenses).map(([key, value]) => [key + ', ₽', metric(value)]),
                )}
              />
            )}
          </details>
        );
      })}
      {allocations.length > 0 && (
        <>
          <h4>Распределение общих расходов</h4>
          <DataTable<Entity>
            rows={allocations.map((row, index) => ({ ...row, id: String(index) }))}
            columns={[
              { key: 'name', label: 'Расход' },
              { key: 'amount', label: 'Всего, ₽', render: (row) => decimal(row.amount) },
              { key: 'method', label: 'Способ' },
              {
                key: 'distribution_quantity',
                label: 'На сколько единиц делится',
                render: (row) => decimal(row.distribution_quantity ?? row.wave_total_quantity),
              },
              {
                key: 'existing_wave_share',
                label: 'На прежние заказы, ₽',
                render: (row) => decimal(row.existing_wave_share),
              },
              {
                key: 'parts',
                label: 'На позиции расчёта, ₽',
                render: (row) =>
                  decimal(
                    Object.values((row.parts || {}) as Entity).reduce<number>(
                      (sum, value) => sum + Number(value),
                      0,
                    ),
                  ),
              },
            ]}
          />
        </>
      )}
      {rates.length > 0 && (
        <p>
          Зафиксированные курсы:{' '}
          {rates
            .map(
              (rate) =>
                `${rate.currency}: ${decimal(rate.management_per_unit)} ₽ за ${decimal(rate.quoted_units)} (${date(rate.date)}, ${rate.source})`,
            )
            .join('; ')}
          .
        </p>
      )}
    </div>
  );
}
function expenseFromReference(row: Record<string, unknown>): Expense {
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

const standardExpenses: Expense[] = [
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
const moscowDelivery = expenseFromReference({
  name: 'Доставка клиенту в Москве',
  amount: '5000',
  currency: 'RUB',
  scope: 'REQUEST',
  stage: 'CLIENT_DELIVERY',
});
const airportPickup = expenseFromReference({
  name: 'Логистика РФ (вывоз из аэропорта)',
  amount: '0',
  currency: 'RUB',
  stage: 'DOMESTIC_LOGISTICS',
  scope: 'WAVE',
});

function CalculationEditor({
  request,
  previous,
  initialIds,
  onClose,
  onSuccess,
}: {
  request: Entity;
  previous?: Entity;
  initialIds: string[];
  onClose: () => void;
  onSuccess: () => void;
}) {
  const previousSnapshot = (previous?.snapshot || {}) as Entity;
  const previousInput = (previousSnapshot.input || {}) as Entity;
  const previousSelections = Array.isArray(previousInput.selections)
    ? (previousInput.selections as Entity[])
    : [];
  const previousTerms = (previousInput.payment_terms ||
    previousSnapshot.payment_terms ||
    {}) as Entity;
  const previousAdjustment = (previousInput.internal_adjustment ||
    previousSnapshot.internal_adjustment ||
    {}) as Entity;
  const auth = useAuth();
  const quotes = useApi<Page>('/requests/' + request.id + '/quote-items?all=true');
  const initialQuotes = useApi<Page>(null);
  const profiles = useApi<Page>('/profiles?page_size=100');
  const expenseTypes = useApi<Page>('/expense-types?active=true');
  const waveExpenses = useApi<{
    expenses: Entity[] | null;
    source_calculation_id: string | null;
    complete_budget?: boolean;
    wave_version?: number;
  }>(request.wave_id ? `/requests/${request.id}/wave-expenses` : null);
  const command = useCommand();
  const budgetCommand = useCommand();
  const [profileId, setProfileId] = useState(
    String(previousInput.profile_id || previousSnapshot.profile_id || previous?.profile_id || ''),
  );
  const [selections, setSelections] = useState<Record<string, Selection>>(() =>
    Object.fromEntries(
      initialIds.map((id) => {
        const old = previousSelections.find((item) => item.quote_item_id === id);
        const line = ((previousSnapshot.lines || []) as Entity[]).find(
          (item) => item.quote_item_id === id,
        );
        const detail = (line?.detail || {}) as Entity;
        let bonus = String(old?.bonus_coefficient || detail.bonus_coefficient || '1');
        if (
          Number(bonus) === 1 &&
          previousAdjustment.enabled &&
          Number(detail.pre_bonus_sale_net) > 0
        ) {
          bonus = (
            1 +
            Number(detail.internal_bonus || 0) / Number(detail.pre_bonus_sale_net)
          ).toFixed(6);
        }
        return [
          id,
          {
            quote_item_id: id,
            ...(old?.quantity ? { quantity: String(old.quantity) } : {}),
            markup_coefficient: String(old?.markup_coefficient || '1.5'),
            ...(old || line ? { bonus_coefficient: bonus } : {}),
          },
        ];
      }),
    ),
  );
  const [bulkMarkup, setBulkMarkup] = useState('1.5');
  const [expenses, setExpenses] = useState<Expense[]>(() =>
    Array.isArray(previousInput.expenses)
      ? (previousInput.expenses as Entity[]).map(expenseFromReference)
      : [],
  );
  const [hydratedProfileId, setHydratedProfileId] = useState('');
  const [expenseTypeId, setExpenseTypeId] = useState('');
  const [prepayment, setPrepayment] = useState(String(previousTerms.prepayment_percent || '100'));
  const [deferred, setDeferred] = useState(String(previousTerms.deferred_percent || '0'));
  const [deferredDays, setDeferredDays] = useState(String(previousTerms.deferred_days || '0'));
  const [deferredStart, setDeferredStart] = useState(
    String(previousTerms.deferred_start_event || ''),
  );
  const [serviceFeePercent, setServiceFeePercent] = useState(
    String(previousAdjustment.service_fee_percent || '0'),
  );
  const [vatDeductible, setVatDeductible] = useState(
    Boolean(previousInput.vat_deductible ?? previousSnapshot.vat_deductible ?? true),
  );
  const [deliveryDays, setDeliveryDays] = useState(
    String(
      (Object.prototype.hasOwnProperty.call(previousInput, 'delivery_days')
        ? previousInput.delivery_days
        : previousSnapshot.delivery_days) ?? '',
    ),
  );
  const [deliveryRequired, setDeliveryRequired] = useState(
    Boolean(previousInput.delivery_required ?? true),
  );
  const [deliveryCity, setDeliveryCity] = useState(String(previousInput.delivery_city || ''));
  const tariffs = useApi<Page>('/delivery-tariffs');
  const [preview, setPreview] = useState<Entity>();
  const [showDetails, setShowDetails] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const autoCalculate = useRef<() => void>(() => {});
  const currentSignature = useRef('');
  const dirty = Boolean(
    profileId ||
    Object.keys(selections).length ||
    expenses.length ||
    prepayment !== '100' ||
    deferred !== '0' ||
    deferredDays !== '0' ||
    deferredStart ||
    Number(serviceFeePercent) > 0 ||
    vatDeductible ||
    deliveryDays,
  );
  useDirtyProtection(dirty);
  const rows = [...(initialQuotes.data?.items || []), ...(quotes.data?.items || [])].filter(
    (row, index, all) => all.findIndex((candidate) => candidate.id === row.id) === index,
  );
  const selectedRows = rows.filter((row) => selections[row.id]);
  useEffect(() => {
    setSelections((current) => {
      let changed = false;
      const next = { ...current };
      for (const row of [...(initialQuotes.data?.items || []), ...(quotes.data?.items || [])]) {
        if (next[row.id] && next[row.id].quantity === undefined) {
          next[row.id] = {
            ...next[row.id],
            quantity: String(row.request_quantity || row.quantity || '1'),
          };
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [quotes.data, initialQuotes.data]);

  const neededCurrencies = [
    ...new Set(
      [
        ...selectedRows.map(currencyOf),
        ...expenses.flatMap((expense) => [
          expense.calculation_type === 'PERCENTAGE' ? '' : expense.currency,
          Number(expense.minimum_amount) > 0 ? expense.minimum_currency : '',
        ]),
      ].filter((code) => code && code !== 'RUB'),
    ),
  ];
  const profileOptions =
    profiles.data?.items.filter(
      (profile) =>
        profile.status === 'published' &&
        (profile.definition as Entity | undefined)?.methodology === 'itemized_v2',
    ) || [];
  const selectedProfile = profileOptions.find((profile) => profile.id === profileId);
  const hasImportSelections = selectedRows.some(
    (row) => !row.calculation_type || row.calculation_type === 'IMPORT',
  );
  const profileHydrationKey = `${profileId}:${hasImportSelections}`;
  useEffect(() => {
    if (
      !selectedProfile ||
      hydratedProfileId === profileHydrationKey ||
      waveExpenses.loading ||
      quotes.loading
    )
      return;
    const defaults = (((selectedProfile.definition as Entity).default_expenses || []) as Entity[])
      .map(normalizeLogisticsExpense)
      .filter((expense) => hasImportSelections || expense.scope === 'REQUEST');
    const shared = hasImportSelections
      ? waveExpenses.data?.expenses?.map(normalizeLogisticsExpense)
      : undefined;
    const previousExpenses =
      Array.isArray(previousInput.expenses) &&
      profileId ===
        String(
          previousInput.profile_id || previousSnapshot.profile_id || previous?.profile_id || '',
        )
        ? (previousInput.expenses as Entity[]).map(normalizeLogisticsExpense)
        : undefined;
    const requestExpenses = (previousExpenses || defaults).filter((row) => row.scope === 'REQUEST');
    setExpenses(
      (shared === null || shared === undefined
        ? previousExpenses || defaults
        : waveExpenses.data?.source_calculation_id || waveExpenses.data?.complete_budget
          ? [...shared, ...requestExpenses]
          : [
              ...defaults.filter(
                (row) => row.scope !== 'REQUEST' && row.stage !== 'INTERNATIONAL_LOGISTICS',
              ),
              ...requestExpenses,
              ...shared,
            ]
      ).map(expenseFromReference),
    );
    setHydratedProfileId(profileHydrationKey);
  }, [
    selectedProfile,
    profileId,
    hydratedProfileId,
    waveExpenses.loading,
    waveExpenses.data,
    quotes.loading,
    hasImportSelections,
    profileHydrationKey,
    previousInput.expenses,
    previousInput.profile_id,
    previousSnapshot.profile_id,
    previous?.profile_id,
  ]);
  const profileRates = ((selectedProfile?.definition as Entity | undefined)?.exchange_rates ||
    []) as Rate[];
  const serverError = command.error instanceof ApiError ? command.error : undefined;
  const serverSelectionIndex = Number(serverError?.field?.match(/^selections\.(\d+)/)?.[1]);
  const serverSelectionId = Number.isInteger(serverSelectionIndex)
    ? Object.values(selections)[serverSelectionIndex]?.quote_item_id
    : undefined;
  const errors: string[] = [];
  if (
    !request.wave_id &&
    selectedRows.some((row) => !row.calculation_type || row.calculation_type === 'IMPORT')
  )
    errors.push('Руководитель должен назначить волну поставки в заявке.');
  if (waveExpenses.loading) errors.push('Загружаются расходы волны.');
  if (waveExpenses.error)
    errors.push(
      'Не удалось загрузить расходы волны. Проверьте тариф поставщика и повторно откройте расчёт.',
    );
  if (!profileId) errors.push('Не выбран профиль расчёта.');
  if (profileId && hydratedProfileId !== profileHydrationKey)
    errors.push('Загружаются расходы профиля.');
  if (!Object.keys(selections).length) errors.push('Выберите хотя бы одну позицию квоты.');
  if (Object.keys(selections).length > selectedRows.length)
    errors.push('Часть выбранных квот не загружена. Откройте их в списке и повторите выбор.');
  selectedRows.forEach((row) => {
    const title = nameOf(row) + ' ' + String(row.packing_name || '');
    if (!currencyOf(row)) errors.push('Для позиции ' + title + ' отсутствует валюта закупки.');
    if (!row.product_group_slug)
      errors.push('Для позиции ' + title + ' не определена товарная группа.');
    if (row.unit_price == null)
      errors.push('Для позиции ' + title + ' отсутствует закупочная цена.');
    if (row.delivery_days == null)
      errors.push('Для позиции ' + title + ' не указан срок поставки.');
    if (
      !Number.isInteger(Number(selections[row.id].quantity)) ||
      Number(selections[row.id].quantity) <= 0
    )
      errors.push('Количество фасовок позиции ' + title + ' должно быть целым числом больше нуля.');
    if (!validMarkup(selections[row.id].markup_coefficient))
      errors.push('Наценка позиции ' + title + ' должна быть не меньше 1.');
    if (
      auth.can('finance.reward.read') &&
      !validMarkup(
        String(
          selections[row.id].bonus_coefficient ||
            (selectedProfile?.definition as Entity | undefined)?.default_bonus_coefficient ||
            '1',
        ),
      )
    )
      errors.push('Коэффициент бонуса позиции ' + title + ' должен быть не меньше 1.');
    if (row.expired) errors.push('Срок действия цены позиции ' + title + ' истёк.');
  });
  neededCurrencies.forEach((currency) => {
    const rate = profileRates.find((entry) => entry.currency === currency);
    if (!rate) errors.push('Для ' + currency + ' отсутствует курс к RUB.');
    else if (
      !positive(rate.management_per_unit) ||
      !positive(rate.quoted_units) ||
      !rate.date ||
      !rate.source.trim()
    )
      errors.push('Заполните курс ' + currency + ', дату и источник.');
  });
  expenses.forEach((expense, index) => {
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
    !Number.isFinite(Number(prepayment)) ||
    !Number.isFinite(Number(deferred)) ||
    Number(prepayment) < 0 ||
    Number(deferred) < 0 ||
    Number(prepayment) + Number(deferred) !== 100
  )
    errors.push('Предоплата и отсрочка должны суммарно составлять 100%.');
  if (!Number.isInteger(Number(deferredDays)) || Number(deferredDays) < 0)
    errors.push('Срок отсрочки укажите целым числом дней.');
  if (
    deliveryDays !== '' &&
    (!Number.isInteger(Number(deliveryDays)) ||
      Number(deliveryDays) < 0 ||
      Number(deliveryDays) > 3650)
  )
    errors.push('Общий срок поставки укажите целым числом от 0 до 3650 дней.');
  if (Number(deferred) > 0 && !deferredStart) errors.push('Выберите событие начала отсрочки.');
  if (!Number.isFinite(Number(serviceFeePercent)) || Number(serviceFeePercent) < 0)
    errors.push('Сервисная комиссия должна быть неотрицательным числом.');
  function invalidate() {
    setPreview(undefined);
    command.setError(undefined);
  }
  function close() {
    if (!dirty || window.confirm('Расчёт не записан. Закрыть форму?')) onClose();
  }
  function toggle(row: Entity, checked: boolean) {
    setSelections((current) => {
      const next = { ...current };
      if (checked)
        next[row.id] = {
          quote_item_id: row.id,
          quantity: String(row.request_quantity || row.quantity || '1'),
          markup_coefficient: current[row.id]?.markup_coefficient || '1.5',
        };
      else delete next[row.id];
      return next;
    });
    invalidate();
  }
  async function calculate(save = false, quiet = false) {
    if (!quiet) setAttempted(true);
    if (errors.length || (save && !preview)) return;
    const submittedSignature = inputSignature;
    try {
      const result = await command.run<Entity>(
        '/requests/' + request.id + '/calculations' + (save ? '' : '/preview'),
        {
          request_version: request.version,
          profile_id: profileId,
          selections: Object.values(selections),
          rates: [],
          expenses,
          vat_deductible: vatDeductible,
          delivery_required: deliveryRequired,
          delivery_city: deliveryCity || null,
          ...(deliveryDays !== '' ? { delivery_days: Number(deliveryDays) } : {}),
          ...(save && preview && ((preview.snapshot || preview) as Entity).wave_distribution
            ? {
                expected_wave_digest: (
                  ((preview.snapshot || preview) as Entity).wave_distribution as Entity
                ).digest,
              }
            : {}),
          payment_terms: {
            prepayment_percent: prepayment,
            deferred_percent: deferred,
            deferred_days: deferredDays,
            ...(deferredStart ? { deferred_start_event: deferredStart } : {}),
          },
          ...(auth.can('finance.reward.read')
            ? {
                internal_adjustment: {
                  enabled: Number(serviceFeePercent) > 0,
                  type: 'PERCENTAGE',
                  value: '0',
                  service_fee_percent: serviceFeePercent,
                },
              }
            : {}),
          ...(save && preview
            ? { previous_id: ((preview.snapshot || preview) as Entity).base_version_id || null }
            : previous
              ? { previous_id: previous.id }
              : {}),
        },
        'POST',
        true,
      );
      if (save && result) onSuccess();
      else if (result && submittedSignature === currentSignature.current) setPreview(result);
    } catch (error) {
      if (error instanceof ApiError && error.code === 'WAVE_CHANGED') {
        setPreview(undefined);
        setHydratedProfileId('');
        waveExpenses.refresh();
      }
      /* Сохраняем ввод, сообщение показывает ErrorBox. */
    }
  }
  autoCalculate.current = () => {
    void calculate(false, true);
  };
  const inputSignature = JSON.stringify({
    profileId,
    selections,
    expenses,
    prepayment,
    deferred,
    deferredDays,
    deferredStart,
    serviceFeePercent,
    vatDeductible,
    deliveryDays,
    deliveryRequired,
    deliveryCity,
    requestVersion: request.version,
    waveId: request.wave_id,
    selectedRows: selectedRows.map((row) => row.id),
  });
  currentSignature.current = inputSignature;
  const autoReady = !preview && !command.busy && !command.error && errors.length === 0;
  useEffect(() => {
    if (!autoReady) return;
    const timer = window.setTimeout(() => autoCalculate.current(), 450);
    return () => window.clearTimeout(timer);
  }, [autoReady, inputSignature]);
  const snapshot = (preview?.snapshot || preview) as Record<string, unknown> | undefined;
  const outputLines = (snapshot?.lines || []) as Entity[];
  const waveDistribution = (snapshot?.wave_distribution || {}) as Entity;
  const totals = snapshot?.totals as Record<string, unknown> | undefined;
  const resultLabels: Record<string, string> = {
    purchase_rub: 'Закупка, ₽',
    international_logistics: 'Международная логистика, ₽',
    domestic_logistics: detailLabels.domestic_logistics,
    client_delivery: detailLabels.client_delivery,
    domestic_logistics_total: detailLabels.domestic_logistics_total,
    duty: 'Пошлина, ₽',
    customs_fee: 'Таможенный сбор, ₽',
    customs_base: 'Таможенная стоимость, ₽',
    import_vat_base: 'База ввозного НДС (ННБ), ₽',
    import_vat: 'Ввозной НДС, ₽',
    clean_cost: 'Стоимость с пошлиной и невозмещаемым НДС, ₽',
    markup_base: detailLabels.markup_base,
    cost_after_markup: detailLabels.cost_after_markup,
    cash_need: 'Денежные затраты на заказ, ₽',
    general_expenses: 'Общие расходы, ₽',
    cost: 'Себестоимость, ₽',
    sale_net: 'Продажа без НДС, ₽',
    sale_tax: 'НДС продажи, ₽',
    sale_total: 'Цена продажи, ₽',
    profit: 'Валовая прибыль, ₽',
    total: 'Итого, ₽',
    profitability_percent: 'Рентабельность, %',
    cost_profitability_percent: 'Доходность затрат, %',
    customs_fee_1: 'Сбор 1 — обычные товары, ₽',
    customs_fee_2: 'Сбор 2 — колонки, ₽',
  };
  return (
    <Modal
      title={previous ? 'Новая версия расчёта' : 'Новый расчёт'}
      wide
      className="calculation-modal"
      onClose={close}
    >
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void calculate(false);
        }}
      >
        <div className="form-body calculation-editor">
          <div className="info-note">
            Источник: квоты заявки №{String(request.number || '')}. Основание версии:
            {previous
              ? ' последняя сохранённая версия'
              : ' определяется автоматически при сохранении'}
            .
          </div>
          <ErrorBox
            error={
              command.error ||
              quotes.error ||
              initialQuotes.error ||
              profiles.error ||
              expenseTypes.error
            }
          />
          {attempted && errors.length > 0 && (
            <div className="validation-summary" role="alert">
              <strong>Расчёт пока нельзя выполнить:</strong>
              <ul>
                {errors.map((error, index) => (
                  <li key={index}>{error}</li>
                ))}
              </ul>
            </div>
          )}
          <label className="field calculation-profile">
            Профиль расчёта
            <select
              value={profileId}
              aria-invalid={(attempted && !profileId) || serverError?.field === 'profile_id'}
              onChange={(event) => {
                setProfileId(event.target.value);
                const profile = profileOptions.find((option) => option.id === event.target.value);
                const defaults = (profile?.definition as Entity | undefined)?.default_expenses;
                setExpenses(
                  Array.isArray(defaults)
                    ? defaults.map((row) => expenseFromReference(row as Entity))
                    : [],
                );
                invalidate();
              }}
            >
              <option value="">Выберите утверждённый профиль</option>
              {profileOptions.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {label(profile)} · {date(profile.effective_from)}
                </option>
              ))}
            </select>
            {!profileOptions.length && !profiles.loading && (
              <small>Нужен опубликованный профиль с методикой itemized_v2.</small>
            )}
          </label>
          <section className="calculation-section">
            <label className="calculation-internal-toggle">
              <input
                type="checkbox"
                checked={vatDeductible}
                onChange={(event) => {
                  setVatDeductible(event.target.checked);
                  invalidate();
                }}
              />{' '}
              Расчёт с вычетом НДС
            </label>
            <p className="muted">
              При вычете ввозной НДС исключается из себестоимости и цены продажи. В потребности в
              средствах он сохраняется.
            </p>
            <label className="field">
              Общий срок поставки, дней
              <input
                type="number"
                min="0"
                max="3650"
                step="1"
                aria-label="Общий срок поставки, дней"
                value={
                  deliveryDays ||
                  (selectedRows.length
                    ? String(Math.max(...selectedRows.map((row) => Number(row.delivery_days || 0))))
                    : '')
                }
                placeholder="По срокам выбранных квот"
                onChange={(event) => {
                  setDeliveryDays(event.target.value);
                  invalidate();
                }}
              />
              <small>
                Этот срок попадёт в КП. Если поле пустое, используется максимальный срок выбранных
                квот.
              </small>
            </label>
            <label className="calculation-internal-toggle">
              <input
                type="checkbox"
                checked={!deliveryRequired}
                onChange={(e) => {
                  setDeliveryRequired(!e.target.checked);
                  invalidate();
                }}
              />{' '}
              Доставка не требуется
            </label>
            {deliveryRequired && (
              <label className="field">
                Город доставки — СДЭК ТМ-35, до 15 кг
                <select
                  value={deliveryCity}
                  onChange={(e) => {
                    setDeliveryCity(e.target.value);
                    invalidate();
                  }}
                >
                  <option value="">Определить по юридическому адресу клиента</option>
                  {tariffs.data?.items.map((row) => (
                    <option key={row.id} value={String(row.city)}>
                      {String(row.city)} — {decimal(row.amount)} ₽ + НДС
                    </option>
                  ))}
                </select>
                <small>Если город не распознан, выберите его вручную.</small>
              </label>
            )}
          </section>
          <section className="calculation-section">
            <div className="section-heading">
              <div>
                <h3>Выбранные квоты и количества</h3>
                <p>
                  Цена указана за одну фасовку. Количество продажи задайте здесь; оно не ограничено
                  количеством квоты.
                </p>
              </div>
              <span className="selection-count">Выбрано: {Object.keys(selections).length}</span>
            </div>
            <div className="calculation-summary" aria-live="polite">
              <span>
                Итого с НДС: <strong>{metric(totals?.total)} ₽</strong>
              </span>
              {auth.can('finance.profit.read') && (
                <span>
                  Рентабельность расчёта:{' '}
                  <strong>
                    {metric(displayCalculationMetrics(totals || {}).profitability_percent)} %
                  </strong>
                </span>
              )}
            </div>
            <DataTable<Entity>
              className="calculation-positions"
              rows={rows}
              rowClassName={(row) => (selections[row.id] ? 'selected-table-row' : '')}
              columns={[
                {
                  key: '__selection',
                  sortable: false,
                  label: (
                    <label className="calculation-select-all">
                      <input
                        type="checkbox"
                        aria-label="Выбрать все квоты"
                        title="Выбрать все квоты"
                        checked={
                          rows.length > 0 && rows.every((row) => Boolean(selections[row.id]))
                        }
                        onChange={(event) => {
                          const checked = event.target.checked;
                          setSelections((current) => {
                            const next = { ...current };
                            rows.forEach((row) => {
                              if (checked)
                                next[row.id] = {
                                  quote_item_id: row.id,
                                  quantity:
                                    current[row.id]?.quantity ??
                                    String(row.request_quantity || row.quantity || '1'),
                                  markup_coefficient: current[row.id]?.markup_coefficient || '1.5',
                                };
                              else delete next[row.id];
                            });
                            return next;
                          });
                          invalidate();
                        }}
                      />
                      <span>Выбрать все квоты</span>
                    </label>
                  ),
                  render: (row) => (
                    <input
                      type="checkbox"
                      aria-label={'Выбрать ' + nameOf(row)}
                      checked={Boolean(selections[row.id])}
                      onChange={(event) => toggle(row, event.target.checked)}
                    />
                  ),
                },
                {
                  key: 'nomenclature_name',
                  label: 'Товар',
                  render: (row) => (
                    <span className="calculation-product">
                      <strong>{nameOf(row)}</strong>
                      <span className="calculation-manufacturer">
                        {String(row.manufacturer || '—')}
                      </span>
                      <small>
                        {String(row.product_group_name || '')} · {String(row.packing_name || '')} ·
                        Арт. {String(row.article || '—')}
                      </small>
                      <small>
                        {decimal(row.unit_price)} {currencyOf(row)} за шт. · Срок:{' '}
                        {String(row.delivery_days ?? '—')} дней
                      </small>
                      {row.id === serverSelectionId && (
                        <small className="field-error">{serverError?.message}</small>
                      )}
                      {Boolean(row.expired) && <small className="field-error">Цена истекла</small>}
                    </span>
                  ),
                },
                {
                  key: 'supplier_name',
                  label: 'Поставщик',
                  render: (row) => String(row.supplier_name || '—'),
                },
                {
                  key: 'quantity',
                  label: 'Кол-во, шт.',
                  sortable: false,
                  render: (row) =>
                    selections[row.id] ? (
                      <input
                        className="calculation-markup"
                        inputMode="numeric"
                        aria-label={'Количество ' + nameOf(row)}
                        value={selections[row.id].quantity ?? ''}
                        aria-invalid={
                          attempted &&
                          (!Number.isInteger(Number(selections[row.id].quantity)) ||
                            Number(selections[row.id].quantity) <= 0)
                        }
                        onChange={(event) => {
                          setSelections((current) => ({
                            ...current,
                            [row.id]: { ...current[row.id], quantity: event.target.value },
                          }));
                          invalidate();
                        }}
                      />
                    ) : (
                      decimal(row.request_quantity || row.quantity)
                    ),
                },
                {
                  key: 'markup',
                  label: 'Наценка',
                  sortable: false,
                  render: (row) =>
                    selections[row.id] ? (
                      <input
                        className="calculation-markup"
                        aria-label={'Наценка ' + nameOf(row)}
                        aria-invalid={
                          attempted && !validMarkup(selections[row.id].markup_coefficient)
                        }
                        inputMode="decimal"
                        value={selections[row.id].markup_coefficient}
                        onChange={(event) => {
                          setSelections((current) => ({
                            ...current,
                            [row.id]: {
                              ...current[row.id],
                              markup_coefficient: event.target.value.replace(',', '.'),
                            },
                          }));
                          invalidate();
                        }}
                      />
                    ) : (
                      '—'
                    ),
                },
                {
                  key: 'sale_unit_price',
                  label: 'Цена за шт., ₽',
                  render: (row) =>
                    metric(outputLines.find((line) => line.quote_item_id === row.id)?.unit_price),
                },
                {
                  key: 'sale_amount',
                  label: 'Сумма, ₽',
                  render: (row) =>
                    metric(outputLines.find((line) => line.quote_item_id === row.id)?.total),
                },
                ...(auth.can('finance.profit.read')
                  ? [
                      {
                        key: 'profitability_percent',
                        label: 'Рентабельность',
                        render: (row: Entity) => {
                          const line = outputLines.find((entry) => entry.quote_item_id === row.id);
                          const detail = displayCalculationMetrics((line?.detail || {}) as Entity);
                          return (
                            <span className="calculation-product">
                              <strong>{metric(detail.profitability_percent)} %</strong>
                              <small>
                                {metric(
                                  line && detail.profit != null
                                    ? Number(detail.profit) / Number(line.quantity)
                                    : undefined,
                                )}{' '}
                                ₽ / шт.
                              </small>
                            </span>
                          );
                        },
                      },
                    ]
                  : []),
                ...(auth.can('finance.reward.read')
                  ? [
                      {
                        key: 'bonus',
                        label: 'Бонус',
                        sortable: false,
                        render: (row: Entity) =>
                          selections[row.id] ? (
                            <input
                              className="calculation-markup"
                              inputMode="decimal"
                              aria-label={'Бонус ' + nameOf(row)}
                              value={
                                selections[row.id].bonus_coefficient ||
                                String(
                                  (selectedProfile?.definition as Entity | undefined)
                                    ?.default_bonus_coefficient || '1',
                                )
                              }
                              onChange={(event) => {
                                setSelections((current) => ({
                                  ...current,
                                  [row.id]: {
                                    ...current[row.id],
                                    bonus_coefficient: event.target.value.replace(',', '.'),
                                  },
                                }));
                                invalidate();
                              }}
                            />
                          ) : (
                            '—'
                          ),
                      },
                    ]
                  : []),
              ]}
            />
            <div className="calculation-bulk-markup">
              <label>
                Наценка выбранным{' '}
                <input
                  inputMode="decimal"
                  value={bulkMarkup}
                  onChange={(event) => setBulkMarkup(event.target.value.replace(',', '.'))}
                />
              </label>
              <Button
                type="button"
                variant="secondary"
                disabled={!Object.keys(selections).length || !validMarkup(bulkMarkup)}
                onClick={() => {
                  setSelections((current) =>
                    Object.fromEntries(
                      Object.entries(current).map(([id, selection]) => [
                        id,
                        { ...selection, markup_coefficient: bulkMarkup },
                      ]),
                    ),
                  );
                  invalidate();
                }}
              >
                Применить
              </Button>
            </div>
          </section>
          <section className="calculation-section">
            <div className="section-heading">
              <div>
                <h3>Курсы валют из профиля</h3>
                <p>
                  Для нового курса создайте версию финансового профиля. В расчёте сохранится его
                  снимок.
                </p>
              </div>
            </div>
            {neededCurrencies.map((currency) => {
              const rate = profileRates.find((entry) => entry.currency === currency);
              return (
                <p key={currency}>
                  <strong>{currency} → RUB:</strong>{' '}
                  {rate
                    ? `${decimal(rate.management_per_unit)} ₽ за ${decimal(rate.quoted_units)} · ${date(rate.date)} · ${rate.source}`
                    : 'Курс не задан в выбранном профиле'}
                </p>
              );
            })}
          </section>
          <section className="calculation-section">
            <div className="section-heading">
              <div>
                <h3>Расходы сделки</h3>
                <p>
                  Общие расходы волны переходят в следующие расчёты и заново делятся на всё
                  количество волны. Вывоз из аэропорта делится по волне. СДЭК и доставка клиенту в
                  Москве суммируются и делятся только между позициями этого расчёта.
                </p>
                {waveExpenses.data?.source_calculation_id && (
                  <p>Расходы волны загружены из последнего сохранённого расчёта.</p>
                )}
              </div>
              <div className="calculation-expense-actions">
                {hasImportSelections && (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => {
                      setExpenses((current) =>
                        current.some((entry) => entry.stage === 'DOMESTIC_LOGISTICS')
                          ? current
                          : [...current, airportPickup],
                      );
                      invalidate();
                    }}
                  >
                    Вывоз из аэропорта — на всю волну
                  </Button>
                )}
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => {
                    setExpenses((current) => [
                      ...current,
                      ...standardExpenses.filter(
                        (preset) => !current.some((entry) => entry.name === preset.name),
                      ),
                    ]);
                    invalidate();
                  }}
                >
                  Добавить типовые расходы и комиссии
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => {
                    setExpenses((current) =>
                      current.some((entry) => entry.name === moscowDelivery.name)
                        ? current
                        : [...current, moscowDelivery],
                    );
                    invalidate();
                  }}
                >
                  Доставка клиенту в Москве
                </Button>
                <select
                  aria-label="Статья расхода из справочника"
                  value={expenseTypeId}
                  onChange={(event) => setExpenseTypeId(event.target.value)}
                >
                  <option value="">Статья из справочника</option>
                  {expenseTypes.data?.items
                    .filter((type) => !expenses.some((entry) => entry.name === type.name))
                    .map((type) => (
                      <option key={type.id} value={type.id}>
                        {String(type.name)}
                      </option>
                    ))}
                </select>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={!expenseTypeId}
                  onClick={() => {
                    const type = expenseTypes.data?.items.find((row) => row.id === expenseTypeId);
                    if (type) setExpenses((current) => [...current, expenseFromReference(type)]);
                    setExpenseTypeId('');
                    invalidate();
                  }}
                >
                  <Plus size={15} /> Добавить из справочника
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => {
                    setExpenses((current) => [
                      ...current,
                      {
                        name: '',
                        amount: '0',
                        currency: 'RUB',
                        method: 'BY_QUANTITY',
                        basis: '',
                        stage: 'GENERAL',
                        scope: 'WAVE',
                        calculation_type: 'FIXED',
                        minimum_amount: '0',
                        minimum_currency: 'RUB',
                        include_in_cost: true,
                        include_in_cash: true,
                      },
                    ]);
                    invalidate();
                  }}
                >
                  <Plus size={15} /> Свой расход
                </Button>
              </div>
            </div>
            {serverError?.field?.startsWith('expenses') && (
              <p className="field-error">{serverError.message}</p>
            )}
            {expenses.map((expense, index) => (
              <div className="calculation-expense-row" key={index}>
                <label>
                  Название{' '}
                  <input
                    placeholder="Например, внутренняя доставка"
                    value={expense.name}
                    onChange={(event) => {
                      setExpenses((current) =>
                        current.map((entry, i) =>
                          i === index
                            ? normalizeLogisticsExpense({ ...entry, name: event.target.value })
                            : entry,
                        ),
                      );
                      invalidate();
                    }}
                  />
                </label>
                <label>
                  {expense.calculation_type === 'PERCENTAGE'
                    ? 'Ставка, %'
                    : 'Сумма, ' + expense.currency}
                  <input
                    inputMode="decimal"
                    disabled={expense.calculation_type === 'BRACKET'}
                    value={expense.amount}
                    onChange={(event) => {
                      setExpenses((current) =>
                        current.map((entry, i) =>
                          i === index
                            ? { ...entry, amount: event.target.value.replace(',', '.') }
                            : entry,
                        ),
                      );
                      invalidate();
                    }}
                  />
                </label>
                {expense.calculation_type !== 'PERCENTAGE' && (
                  <label>
                    Валюта суммы{' '}
                    <select
                      value={expense.currency}
                      onChange={(event) => {
                        setExpenses((current) =>
                          current.map((entry, i) =>
                            i === index ? { ...entry, currency: event.target.value } : entry,
                          ),
                        );
                        invalidate();
                      }}
                    >
                      {[
                        ...new Set([
                          'RUB',
                          ...profileRates.map((rate) => rate.currency),
                          expense.currency,
                        ]),
                      ].map((currency) => (
                        <option key={currency} value={currency}>
                          {currency}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label>
                  Тип{' '}
                  <select
                    value={expense.calculation_type}
                    onChange={(event) => {
                      setExpenses((current) =>
                        current.map((entry, i) =>
                          i === index ? { ...entry, calculation_type: event.target.value } : entry,
                        ),
                      );
                      invalidate();
                    }}
                  >
                    <option value="FIXED">Фиксированный</option>
                    <option value="PERCENTAGE">Процент</option>
                    {expense.brackets?.length ? (
                      <option value="BRACKET">По диапазонам</option>
                    ) : null}
                    <option value="MANUAL">Вручную</option>
                  </select>
                </label>
                {['PERCENTAGE', 'BRACKET'].includes(expense.calculation_type) && (
                  <label>
                    База процента{' '}
                    <select
                      value={expense.percent_base || ''}
                      onChange={(event) => {
                        setExpenses((current) =>
                          current.map((entry, i) =>
                            i === index ? { ...entry, percent_base: event.target.value } : entry,
                          ),
                        );
                        invalidate();
                      }}
                    >
                      <option value="">Выберите базу</option>
                      <option value="PURCHASE">Закупка</option>
                      <option value="CUSTOMS_BASE">Таможенная стоимость</option>
                      <option value="DUTY">Пошлина</option>
                      <option value="COST">Себестоимость</option>
                    </select>
                  </label>
                )}
                <label>
                  Этап{' '}
                  <select
                    value={expense.stage}
                    onChange={(event) => {
                      setExpenses((current) =>
                        current.map((entry, i) =>
                          i === index
                            ? normalizeLogisticsExpense({ ...entry, stage: event.target.value })
                            : entry,
                        ),
                      );
                      invalidate();
                    }}
                  >
                    <option value="GENERAL">Общий расход</option>
                    <option value="INTERNATIONAL_LOGISTICS">Международная логистика</option>
                    <option value="DOMESTIC_LOGISTICS">Вывоз из аэропорта — вся волна</option>
                    <option value="CLIENT_DELIVERY">Доставка клиенту — этот расчёт</option>
                  </select>
                </label>
                <label>
                  Относится к{' '}
                  <select
                    value={expense.scope}
                    disabled={['DOMESTIC_LOGISTICS', 'CLIENT_DELIVERY'].includes(expense.stage)}
                    onChange={(event) => {
                      setExpenses((current) =>
                        current.map((entry, i) =>
                          i === index ? { ...entry, scope: event.target.value } : entry,
                        ),
                      );
                      invalidate();
                    }}
                  >
                    <option value="WAVE">Всей волне</option>
                    <option value="REQUEST">Только этому расчёту</option>
                  </select>
                </label>
                {expense.calculation_type === 'PERCENTAGE' && (
                  <>
                    <label>
                      Минимум комиссии{' '}
                      <input
                        inputMode="decimal"
                        value={expense.minimum_amount}
                        onChange={(event) => {
                          setExpenses((current) =>
                            current.map((entry, i) =>
                              i === index
                                ? { ...entry, minimum_amount: event.target.value.replace(',', '.') }
                                : entry,
                            ),
                          );
                          invalidate();
                        }}
                      />
                    </label>
                    <label>
                      Валюта минимума{' '}
                      <select
                        value={expense.minimum_currency}
                        onChange={(event) => {
                          setExpenses((current) =>
                            current.map((entry, i) =>
                              i === index
                                ? { ...entry, minimum_currency: event.target.value }
                                : entry,
                            ),
                          );
                          invalidate();
                        }}
                      >
                        {[
                          ...new Set([
                            'RUB',
                            ...profileRates.map((rate) => rate.currency),
                            expense.minimum_currency,
                          ]),
                        ].map((currency) => (
                          <option key={currency} value={currency}>
                            {currency}
                          </option>
                        ))}
                      </select>
                    </label>
                  </>
                )}
                <label>
                  Распределение{' '}
                  <select
                    value={expense.method}
                    disabled={['DOMESTIC_LOGISTICS', 'CLIENT_DELIVERY'].includes(expense.stage)}
                    onChange={(event) => {
                      setExpenses((current) =>
                        current.map((entry, i) =>
                          i === index ? { ...entry, method: event.target.value } : entry,
                        ),
                      );
                      invalidate();
                    }}
                  >
                    <option value="BY_QUANTITY">По количеству</option>
                    <option value="BY_PURCHASE_VALUE">По стоимости закупки</option>
                    <option value="EQUALLY_BY_POSITION">Поровну</option>
                  </select>
                </label>
                <label>
                  Основание{' '}
                  <input
                    value={expense.basis}
                    onChange={(event) => {
                      setExpenses((current) =>
                        current.map((entry, i) =>
                          i === index ? { ...entry, basis: event.target.value } : entry,
                        ),
                      );
                      invalidate();
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={'Удалить расход ' + (index + 1)}
                  onClick={() => {
                    setExpenses((current) => current.filter((_, i) => i !== index));
                    invalidate();
                  }}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </section>
          <section className="calculation-section">
            <div className="section-heading">
              <div>
                <h3>Условия оплаты клиента</h3>
                <p>Укажите долю предоплаты и отсрочки; сумма долей должна составлять 100%.</p>
              </div>
            </div>
            <div className="calculation-terms-grid">
              <label>
                Предоплата, %{' '}
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="0.01"
                  value={prepayment}
                  onChange={(event) => {
                    setPrepayment(event.target.value);
                    invalidate();
                  }}
                />
              </label>
              <label>
                Отсрочка, %{' '}
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="0.01"
                  value={deferred}
                  onChange={(event) => {
                    setDeferred(event.target.value);
                    invalidate();
                  }}
                />
              </label>
              <label>
                Дней отсрочки{' '}
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={deferredDays}
                  onChange={(event) => {
                    setDeferredDays(event.target.value);
                    invalidate();
                  }}
                />
              </label>
              <label>
                Начало отсрочки{' '}
                <select
                  value={deferredStart}
                  disabled={Number(deferred) === 0}
                  onChange={(event) => {
                    setDeferredStart(event.target.value);
                    invalidate();
                  }}
                >
                  <option value="">Выберите событие</option>
                  <option value="shipment">Отгрузка</option>
                  <option value="delivery">Поставка</option>
                  <option value="invoice">Счёт</option>
                </select>
              </label>
            </div>
            {serverError?.field?.startsWith('payment_terms') && (
              <p className="field-error">{serverError.message}</p>
            )}
          </section>
          {auth.can('finance.reward.read') && (
            <section className="calculation-section">
              <div className="section-heading">
                <div>
                  <h3>Сервисная комиссия</h3>
                  <p>
                    Бонус укажите один раз в строках позиций выше. Комиссия используется только
                    внутри CRM.
                  </p>
                </div>
              </div>
              <label className="field">
                Сервисная комиссия, %
                <input
                  inputMode="decimal"
                  value={serviceFeePercent}
                  onChange={(event) => {
                    setServiceFeePercent(event.target.value.replace(',', '.'));
                    invalidate();
                  }}
                />
              </label>
              {serverError?.field?.startsWith('internal_adjustment') && (
                <p className="field-error">{serverError.message}</p>
              )}
            </section>
          )}
          {Boolean(request.wave_id) && auth.can('waves.write') && Boolean(profileId) && (
            <div className="inline-actions">
              <Button
                type="button"
                variant="secondary"
                busy={budgetCommand.busy}
                disabled={!waveExpenses.data?.wave_version}
                onClick={() => {
                  void budgetCommand
                    .run(
                      `/waves/${request.wave_id}/budget`,
                      {
                        version: waveExpenses.data?.wave_version,
                        profile_id: profileId,
                        expenses: expenses.filter((row) => row.scope === 'WAVE'),
                        rates: profileRates,
                        reason: 'Сохранение общих расходов из формы расчёта',
                      },
                      'PUT',
                    )
                    .then(() => {
                      waveExpenses.refresh();
                      setHydratedProfileId('');
                      invalidate();
                    })
                    .catch(() => {});
                }}
              >
                Сохранить общие расходы в волну
              </Button>
              <small>Обновит бюджет для новых расчётов. Выпущенные КП сохраняют цены.</small>
              <ErrorBox error={budgetCommand.error} />
            </div>
          )}
          {preview && (
            <p className="info-note">
              В закупках: {decimal(waveDistribution.existing_quantity || 0)} шт. · В расчёте:{' '}
              {decimal(waveDistribution.selected_quantity || 0)} шт. · Прогноз:{' '}
              {decimal(waveDistribution.forecast_quantity || 0)} шт. · База:{' '}
              {decimal(waveDistribution.total_quantity || 0)} шт.
            </p>
          )}
          {preview && (
            <section className="calculation-section calculation-result">
              <div className="section-heading">
                <h3>
                  Предварительный результат <Badge value="Не записан" tone="amber" />
                </h3>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setShowDetails((value) => !value)}
                >
                  {showDetails ? 'Скрыть детали' : 'Показать детали'}
                </Button>
              </div>
              {totals && (
                <DetailPairs
                  formulas={{
                    'Закупка, ₽': 'Цена закупки × количество × курс валюты',
                    'Пошлина, ₽':
                      'Импорт: таможенная стоимость × ставка группы. Колонки: 0%. DAP: закупка × 5%. РФ: 0.',
                    'Таможенный сбор, ₽':
                      'Сбор 1 по шкале от таможенной стоимости обычных товаров + сбор 2: 73 800 ₽ только на колонки.',
                    'Ввозной НДС, ₽':
                      '(Таможенная стоимость + пошлина) × ставка НДС; сбор в базу НДС не входит.',
                    'НДС продажи, ₽':
                      'Из цены с НДС: сумма × ставка / (100 + ставка). Округление цены вверх входит в налоговую базу.',
                    'Таможенная стоимость, ₽': 'Закупка в рублях + международная логистика',
                    'База ввозного НДС (ННБ), ₽': 'Таможенная стоимость + пошлина',
                    'Стоимость с пошлиной и невозмещаемым НДС, ₽':
                      'Импорт с вычетом: таможенная стоимость + пошлина. Без вычета добавляется ввозной НДС.',
                    'База наценки, ₽': 'Импорт: таможенная стоимость + таможенная пошлина',
                    'Цена с наценкой, ₽':
                      'База наценки × коэффициент, до добавления перевыставляемых расходов',
                    'Логистика РФ — вывоз из аэропорта, ₽':
                      'Общая логистика РФ × количество расчёта / количество всей волны',
                    'Доставка клиенту — СДЭК и Москва, ₽':
                      'СДЭК + доставка в Москве; расходы только этого расчёта',
                    'Логистика внутри РФ всего, ₽':
                      'Доля вывоза из аэропорта + СДЭК + доставка в Москве',
                    'Себестоимость, ₽':
                      'Закупка + расходы + пошлина + сбор + финансирование + бонусы; ввозной НДС включается при отключённом вычете.',
                    'Валовая прибыль, ₽':
                      'Продажа без НДС − себестоимость; ввозной НДС при вычете в себестоимость не входит',
                    'Рентабельность, %': 'Валовая прибыль / продажа без НДС × 100%',
                    'Доходность затрат, %': 'Валовая прибыль / себестоимость × 100%',
                    'Цена продажи, ₽': 'Продажа без НДС + НДС продажи',
                    'Денежные затраты на заказ, ₽':
                      'Закупка + денежные расходы + пошлина + сбор + ввозной НДС + финансирование и бонусы. Предоплата клиента из этой суммы не вычитается.',
                  }}
                  values={Object.fromEntries(
                    Object.entries(displayCalculationMetrics(totals))
                      .filter(([key]) => key in resultLabels && key !== 'clean_cost')
                      .map(([key, value]) => [resultLabels[key], metric(value)]),
                  )}
                />
              )}
              <DataTable<Entity>
                rows={[
                  ...outputLines.map((line, index) => ({
                    ...line,
                    id: String(line.line_id || index),
                  })),
                  ...(Number(waveDistribution.existing_quantity || 0) > 0
                    ? [
                        {
                          id: 'wave-existing',
                          description: 'Другие позиции волны',
                          quantity: waveDistribution.existing_quantity,
                          detail: {},
                        },
                      ]
                    : []),
                ]}
                columns={[
                  { key: 'description', label: 'Товар' },
                  { key: 'packing', label: 'Фасовка' },
                  { key: 'quantity', label: 'Кол-во', render: (line) => decimal(line.quantity) },
                  {
                    key: 'purchase_rub',
                    label: 'Закупка, ₽',
                    render: (line) => decimal((line.detail as Entity | undefined)?.purchase_rub),
                  },
                  {
                    key: 'duty',
                    label: 'Пошлина, ₽',
                    render: (line) => decimal((line.detail as Entity | undefined)?.duty),
                  },
                  {
                    key: 'expenses_total',
                    label: 'Расходы, ₽',
                    render: (line) => decimal((line.detail as Entity | undefined)?.expenses_total),
                  },
                  {
                    key: 'customs_fee',
                    label: 'Таможенный сбор, ₽',
                    render: (line) => decimal((line.detail as Entity | undefined)?.customs_fee),
                  },
                  {
                    key: 'import_vat',
                    label: 'Ввозной НДС, ₽',
                    render: (line) => decimal((line.detail as Entity | undefined)?.import_vat),
                  },
                  {
                    key: 'markup_coefficient',
                    label: 'Наценка',
                    render: (line) =>
                      decimal((line.detail as Entity | undefined)?.markup_coefficient),
                  },
                  { key: 'total', label: 'Итог, ₽', render: (line) => decimal(line.total) },
                ]}
              />
              {showDetails && <CalculationBreakdown snapshot={snapshot as Entity} />}
            </section>
          )}
        </div>
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={close}>
            Закрыть
          </Button>
          <Button type="submit" variant="secondary" busy={command.busy}>
            Предварительный расчёт
          </Button>
          <Button
            type="button"
            busy={command.busy}
            disabled={!preview}
            title={!preview ? 'Сначала выполните предварительный расчёт' : undefined}
            onClick={() => void calculate(true)}
          >
            <Save size={16} /> Записать версию
          </Button>
        </div>
      </form>
    </Modal>
  );
}
export function RequestCalculations({
  request,
  launchQuoteItemIds = [],
  onLaunchConsumed,
}: {
  request: Entity;
  launchQuoteItemIds?: string[];
  onLaunchConsumed?: () => void;
}) {
  const auth = useAuth();
  const waves = useApi<Page>('/waves');
  const waveCommand = useCommand();
  const [waveId, setWaveId] = useState(String(request.wave_id || ''));
  const [assignedWaveId, setAssignedWaveId] = useState(String(request.wave_id || ''));
  const [requestVersion, setRequestVersion] = useState(Number(request.version));
  const openWaves = (waves.data?.items || []).filter(
    (wave) => ['planned', 'assembling'].includes(String(wave.status)) || wave.id === assignedWaveId,
  );
  const [editing, setEditing] = useState(launchQuoteItemIds.length > 0);
  const [initialIds, setInitialIds] = useState(launchQuoteItemIds);
  const [selected, setSelected] = useState<Entity>();
  const [previous, setPrevious] = useState<Entity>();
  const [revision, setRevision] = useState(0);
  function closeEditor() {
    setEditing(false);
    setInitialIds([]);
    onLaunchConsumed?.();
  }
  return (
    <>
      <section className="calculation-section">
        <div className="section-heading">
          <div>
            <h3>Волна поставки</h3>
            <p>
              Руководитель назначает волну поставщика до расчёта. Подтверждённые количества в ней
              учитываются автоматически.
            </p>
          </div>
        </div>
        <ErrorBox error={waveCommand.error || waves.error} />
        <div className="inline-actions">
          <select
            aria-label="Волна поставки"
            value={waveId}
            disabled={!auth.can('waves.write') || editing}
            onChange={(event) => setWaveId(event.target.value)}
          >
            <option value="">Выберите волну</option>
            {openWaves.map((wave) => (
              <option key={wave.id} value={wave.id}>
                {String(wave.number)} · {String(wave.supplier_name || 'Поставщик не указан')} ·{' '}
                {waveWeekLabel(wave, 'departure')}
              </option>
            ))}
          </select>
          {auth.can('waves.write') && (
            <Button
              type="button"
              variant="secondary"
              busy={waveCommand.busy}
              disabled={waveId === assignedWaveId || editing}
              onClick={() =>
                void waveCommand
                  .run<Entity>(
                    '/requests/' + request.id + '/wave',
                    { request_version: requestVersion, wave_id: waveId || null },
                    'PUT',
                    true,
                  )
                  .then((result) => {
                    if (result) {
                      setRequestVersion(Number(result.version));
                      setAssignedWaveId(String(result.wave_id || ''));
                    }
                  })
              }
            >
              Сохранить волну
            </Button>
          )}
        </div>
      </section>
      <div className="tab-actions">
        <Button
          onClick={() => {
            setPrevious(undefined);
            setInitialIds([]);
            setEditing(true);
          }}
        >
          <Plus size={16} /> Новый расчёт
        </Button>
      </div>
      <Collection
        title="Сохранённые версии расчёта"
        description="Версии сохраняют исходные квоты, курсы, расходы и результат на момент записи."
        endpoint={'/requests/' + request.id + '/calculations'}
        refreshKey={revision}
        onSelect={setSelected}
        columns={[
          {
            key: 'version_number',
            label: 'Номер',
            render: (row) =>
              String(
                row.selection_label ||
                  (row.version_number
                    ? '№' + row.version_number + ' · ' + date(row.created_at, true)
                    : 'Расчёт от ' + date(row.created_at, true)),
              ),
          },
          { key: 'created_at', label: 'Записан', render: (row) => date(row.created_at, true) },
          {
            key: 'wave_stale',
            label: 'Расходы волны',
            render: (row) =>
              row.wave_stale ? (
                <Badge value="Требуется пересчёт" tone="amber" />
              ) : (
                <Badge value="Актуальны" tone="green" />
              ),
          },
          {
            key: 'source_type',
            label: 'Источник',
            render: (row) =>
              String(
                row.source_label ||
                  (row.source_type === 'QUOTE'
                    ? 'Квоты'
                    : row.source_type === 'REQUEST'
                      ? 'Заявка'
                      : 'Предыдущая версия'),
              ),
          },
          {
            key: 'author_id',
            label: 'Автор',
            render: (row) => String(row.author_name || 'Сотрудник'),
          },
        ]}
      />
      {selected && (
        <Modal
          title={
            'Версия расчёта ' +
            String(
              selected.selection_label ||
                '№' + (selected.version_number || '') + ' · ' + date(selected.created_at, true),
            )
          }
          wide
          className="calculation-modal"
          onClose={() => setSelected(undefined)}
        >
          <div className="form-body">
            <DetailPairs
              values={{
                Дата: date(selected.created_at, true),
                Источник: selected.source_label || selected.source_type,
                'Основание версии': selected.base_version_id
                  ? 'Предыдущая сохранённая версия'
                  : 'Первая версия',
                'Контрольная сумма': selected.digest,
              }}
            />
            <p className="muted">
              Новая версия будет основана на последней сохранённой версии этой заявки.
            </p>
            {Boolean(selected.wave_stale) && (
              <p className="info-note" role="status">
                Состав или расходы волны изменились. Создайте новую версию расчёта перед новым КП.
              </p>
            )}
            <Button
              variant="secondary"
              onClick={() => {
                setPrevious(selected);
                setSelected(undefined);
                setInitialIds(
                  (((selected.snapshot as Entity | undefined)?.lines as Entity[]) || [])
                    .map((line) => String(line.quote_item_id || ''))
                    .filter(Boolean),
                );
                setEditing(true);
              }}
            >
              Создать новую версию
            </Button>
            <CalculationBreakdown snapshot={selected.snapshot as Entity} />
          </div>
        </Modal>
      )}
      {editing && (
        <CalculationEditor
          request={{ ...request, version: requestVersion, wave_id: assignedWaveId }}
          previous={previous}
          initialIds={initialIds}
          onClose={closeEditor}
          onSuccess={() => {
            closeEditor();
            setRevision((value) => value + 1);
          }}
        />
      )}
    </>
  );
}
