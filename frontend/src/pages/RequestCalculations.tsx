import { Plus, Save, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import { Badge, Button, DataTable, DetailPairs, ErrorBox, Modal } from '../components/ui';
import { ApiError } from '../lib/api';
import { date, decimal, label } from '../lib/format';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
import { waveWeekLabel } from '../lib/waves';

type Selection = { quote_item_id: string; markup_coefficient: string; bonus_coefficient?: string };
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
  customs_base: 'Таможенная стоимость, ₽',
  duty: 'Пошлина, ₽',
  duty_per_unit: 'Пошлина за единицу, ₽',
  customs_fee: 'Таможенный сбор, ₽',
  general_expenses: 'Прочие расходы, ₽',
  expenses_total: 'Все распределённые расходы, ₽',
  cash_expenses: 'Денежные расходы, ₽',
  import_vat: 'Ввозной НДС, ₽',
  clean_cost: 'Чистая стоимость до наценки, ₽',
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
  markup_amount: 'Наценка, ₽',
  bonus_coefficient: 'Коэффициент бонуса',
  sale_net: 'Продажа без НДС, ₽',
  sale_tax: 'НДС продажи, ₽',
  sale_total: 'Продажа с НДС, ₽',
  profit: 'Прибыль, ₽',
  vat_payable: 'НДС к уплате, ₽',
  cash_need: 'Потребность в средствах, ₽',
  profitability_percent: 'Рентабельность, %',
  expense_share_percent: 'Доля расходов, %',
  investment_efficiency_percent: 'Эффективность средств, %',
};

function CalculationBreakdown({ snapshot }: { snapshot: Entity }) {
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
      <p>
        Волна: {String(wave.number || '—')}. Расходы по количеству делятся на{' '}
        {decimal(distribution.total_quantity || 0)} шт. (
        {decimal(distribution.selected_quantity || 0)} шт. в этом расчёте +{' '}
        {decimal(distribution.existing_quantity || 0)} шт. в других заказах волны).
      </p>
      <DataTable<Entity>
        rows={rows}
        columns={[
          { key: 'description', label: 'Товар' },
          { key: 'quantity', label: 'Кол-во', render: (row) => decimal(row.quantity) },
          ...(
            [
              'purchase_rub',
              'international_logistics',
              'duty',
              'customs_fee',
              'import_vat',
              'general_expenses',
              'cost',
              'sale_total',
              'profit',
            ] as const
          ).map((key) => ({
            key,
            label: detailLabels[key],
            render: (row: Entity) =>
              (row.detail as Entity | undefined)?.[key] == null
                ? '—'
                : decimal((row.detail as Entity)[key]),
          })),
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
              values={Object.fromEntries(
                Object.entries(detail).map(([key, value]) => [
                  detailLabels[key] || key,
                  decimal(value),
                ]),
              )}
            />
            {Object.keys(expenses).length > 0 && (
              <DetailPairs
                values={Object.fromEntries(
                  Object.entries(expenses).map(([key, value]) => [key + ', ₽', decimal(value)]),
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
                key: 'wave_total_quantity',
                label: 'Кол-во в волне',
                render: (row) => decimal(row.wave_total_quantity),
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
  return {
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
  };
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
  const quotes = useApi<Page>('/requests/' + request.id + '/quote-items?page_size=100');
  const initialQuotes = useApi<Page>(
    initialIds.length
      ? '/requests/' +
          request.id +
          '/quote-items?ids=' +
          encodeURIComponent(initialIds.join(',')) +
          '&page_size=100'
      : null,
  );
  const profiles = useApi<Page>('/profiles?page_size=100');
  const expenseTypes = useApi<Page>('/expense-types?active=true');
  const waveExpenses = useApi<{ expenses: Entity[] | null; source_calculation_id: string | null }>(
    request.wave_id ? `/requests/${request.id}/wave-expenses` : null,
  );
  const command = useCommand();
  const [profileId, setProfileId] = useState(
    String(previousInput.profile_id || previousSnapshot.profile_id || previous?.profile_id || ''),
  );
  const [selections, setSelections] = useState<Record<string, Selection>>(() =>
    Object.fromEntries(
      initialIds.map((id) => {
        const old = previousSelections.find((item) => item.quote_item_id === id);
        return [
          id,
          {
            quote_item_id: id,
            markup_coefficient: String(old?.markup_coefficient || '1.5'),
            ...(old?.bonus_coefficient ? { bonus_coefficient: String(old.bonus_coefficient) } : {}),
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
  const [internalEnabled, setInternalEnabled] = useState(Boolean(previousAdjustment.enabled));
  const [internalType, setInternalType] = useState(String(previousAdjustment.type || 'PERCENTAGE'));
  const [internalValue, setInternalValue] = useState(String(previousAdjustment.value || '0'));
  const [serviceFeePercent, setServiceFeePercent] = useState(
    String(previousAdjustment.service_fee_percent || '0'),
  );
  const [vatDeductible, setVatDeductible] = useState(
    Boolean(previousInput.vat_deductible ?? previousSnapshot.vat_deductible),
  );
  const [deliveryDays, setDeliveryDays] = useState(
    String((Object.prototype.hasOwnProperty.call(previousInput, 'delivery_days')
      ? previousInput.delivery_days : previousSnapshot.delivery_days) ?? ''),
  );
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
    internalEnabled ||
    vatDeductible ||
    deliveryDays,
  );
  useDirtyProtection(dirty);
  const rows = [...(initialQuotes.data?.items || []), ...(quotes.data?.items || [])].filter(
    (row, index, all) => all.findIndex((candidate) => candidate.id === row.id) === index,
  );
  const selectedRows = rows.filter((row) => selections[row.id]);
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
  useEffect(() => {
    if (!selectedProfile || hydratedProfileId === profileId || waveExpenses.loading) return;
    const defaults = ((selectedProfile.definition as Entity).default_expenses || []) as Entity[];
    const shared = waveExpenses.data?.expenses;
    const previousExpenses = Array.isArray(previousInput.expenses) && profileId ===
      String(previousInput.profile_id || previousSnapshot.profile_id || previous?.profile_id || '')
      ? previousInput.expenses as Entity[] : undefined;
    const requestExpenses = (previousExpenses || defaults).filter((row) => row.scope === 'REQUEST');
    setExpenses(
      (shared === null || shared === undefined
        ? previousExpenses || defaults
        : waveExpenses.data?.source_calculation_id
          ? [...shared, ...requestExpenses]
          : [...defaults.filter((row) => row.scope !== 'REQUEST' && row.stage !== 'INTERNATIONAL_LOGISTICS'),
            ...requestExpenses, ...shared]
      ).map(expenseFromReference),
    );
    setHydratedProfileId(profileId);
  }, [selectedProfile, profileId, hydratedProfileId, waveExpenses.loading, waveExpenses.data,
    previousInput.expenses, previousInput.profile_id, previousSnapshot.profile_id, previous?.profile_id]);
  const profileRates = ((selectedProfile?.definition as Entity | undefined)?.exchange_rates ||
    []) as Rate[];
  const serverError = command.error instanceof ApiError ? command.error : undefined;
  const serverSelectionIndex = Number(serverError?.field?.match(/^selections\.(\d+)/)?.[1]);
  const serverSelectionId = Number.isInteger(serverSelectionIndex)
    ? Object.values(selections)[serverSelectionIndex]?.quote_item_id
    : undefined;
  const errors: string[] = [];
  if (!request.wave_id) errors.push('Руководитель должен назначить волну поставки в заявке.');
  if (waveExpenses.loading) errors.push('Загружаются расходы волны.');
  if (waveExpenses.error)
    errors.push(
      'Не удалось загрузить расходы волны. Проверьте тариф поставщика и повторно откройте расчёт.',
    );
  if (!profileId) errors.push('Не выбран профиль расчёта.');
  if (profileId && hydratedProfileId !== profileId) errors.push('Загружаются расходы профиля.');
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
  if (
    internalEnabled &&
    (!Number.isFinite(Number(internalValue)) ||
      Number(internalValue) < (internalType === 'MULTIPLIER' ? 1 : 0) ||
      !Number.isFinite(Number(serviceFeePercent)) ||
      Number(serviceFeePercent) < 0)
  )
    errors.push('Проверьте внутреннюю корректировку и комиссию.');
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
          ...(deliveryDays !== '' ? { delivery_days: Number(deliveryDays) } : {}),
          ...(save && preview && ((preview.snapshot || preview) as Entity).wave_distribution
            ? { expected_wave_digest: (((preview.snapshot || preview) as Entity).wave_distribution as Entity).digest }
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
                  enabled: internalEnabled,
                  type: internalType,
                  value: internalValue,
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
    internalEnabled,
    internalType,
    internalValue,
    serviceFeePercent,
    vatDeductible,
    deliveryDays,
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
    duty: 'Пошлина, ₽',
    customs_fee: 'Таможенный сбор, ₽',
    import_vat: 'Ввозной НДС, ₽',
    cash_need: 'Потребность в средствах, ₽',
    general_expenses: 'Общие расходы, ₽',
    cost: 'Себестоимость, ₽',
    sale_net: 'Продажа без НДС, ₽',
    sale_tax: 'НДС продажи, ₽',
    sale_total: 'Цена продажи, ₽',
    profit: 'Прибыль, ₽',
    total: 'Итого, ₽',
  };
  return (
    <Modal title={previous ? 'Новая версия расчёта' : 'Новый расчёт'} wide onClose={close}>
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
                value={deliveryDays}
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
          </section>
          <section className="calculation-section">
            <div className="section-heading">
              <div>
                <h3>Выбранные квоты и количества</h3>
                <p>Закупочная цена, валюта, поставщик и количество переносятся из квоты.</p>
              </div>
              <span className="selection-count">Выбрано: {Object.keys(selections).length}</span>
            </div>
            <DataTable<Entity>
              rows={rows}
              rowClassName={(row) => (selections[row.id] ? 'selected-table-row' : '')}
              columns={[
                {
                  key: '__selection',
                  sortable: false,
                  label: (
                    <input
                      type="checkbox"
                      aria-label="Выбрать все квоты"
                      title="Выбрать все квоты"
                      checked={rows.length > 0 && rows.every((row) => Boolean(selections[row.id]))}
                      onChange={(event) => {
                        const checked = event.target.checked;
                        setSelections((current) => {
                          const next = { ...current };
                          rows.forEach((row) => {
                            if (checked)
                              next[row.id] = {
                                quote_item_id: row.id,
                                markup_coefficient: current[row.id]?.markup_coefficient || '1.5',
                              };
                            else delete next[row.id];
                          });
                          return next;
                        });
                        invalidate();
                      }}
                    />
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
                      <Badge value="Квота" />
                      {row.id === serverSelectionId && (
                        <small className="field-error">{serverError?.message}</small>
                      )}
                      {Boolean(row.expired) && <small className="field-error">Цена истекла</small>}
                    </span>
                  ),
                },
                { key: 'packing_name', label: 'Фасовка' },
                { key: 'quantity', label: 'Кол-во', render: (row) => decimal(row.quantity) },
                { key: 'unit_price', label: 'Закупка', render: (row) => decimal(row.unit_price) },
                { key: 'currency_code', label: 'Валюта', render: currencyOf },
                { key: 'supplier_name', label: 'Поставщик' },
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
                ...(auth.can('finance.reward.read')
                  ? [
                      {
                        key: 'bonus',
                        label: 'Бонус / откат',
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
                  подтверждённое количество. Расход «Только эта заявка» подходит для доставки
                  клиенту.
                </p>
                {waveExpenses.data?.source_calculation_id && (
                  <p>Расходы волны загружены из последнего сохранённого расчёта.</p>
                )}
              </div>
              <div className="calculation-expense-actions">
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
                          i === index ? { ...entry, name: event.target.value } : entry,
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
                          i === index ? { ...entry, stage: event.target.value } : entry,
                        ),
                      );
                      invalidate();
                    }}
                  >
                    <option value="GENERAL">Общий расход</option>
                    <option value="INTERNATIONAL_LOGISTICS">Международная логистика</option>
                  </select>
                </label>
                <label>
                  Относится к{' '}
                  <select
                    value={expense.scope}
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
                    <option value="REQUEST">Только этой заявке</option>
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
                  <h3>Внутренняя корректировка</h3>
                  <p>Параметры используются только внутри CRM и не выводятся в КП.</p>
                </div>
              </div>
              <label className="calculation-internal-toggle">
                <input
                  type="checkbox"
                  checked={internalEnabled}
                  onChange={(event) => {
                    setInternalEnabled(event.target.checked);
                    invalidate();
                  }}
                />{' '}
                Применить внутренний бонус / комиссию
              </label>
              {internalEnabled && (
                <div className="calculation-terms-grid">
                  <label>
                    Тип{' '}
                    <select
                      value={internalType}
                      onChange={(event) => {
                        setInternalType(event.target.value);
                        invalidate();
                      }}
                    >
                      <option value="PERCENTAGE">Процент</option>
                      <option value="MULTIPLIER">Множитель</option>
                      <option value="FIXED">Фиксированная сумма</option>
                    </select>
                  </label>
                  <label>
                    {internalType === 'MULTIPLIER'
                      ? 'Коэффициент'
                      : internalType === 'FIXED'
                        ? 'Сумма, ₽'
                        : 'Бонус, %'}
                    <input
                      inputMode="decimal"
                      value={internalValue}
                      onChange={(event) => {
                        setInternalValue(event.target.value.replace(',', '.'));
                        invalidate();
                      }}
                    />
                  </label>
                  <label>
                    Сервисная комиссия, %{' '}
                    <input
                      inputMode="decimal"
                      value={serviceFeePercent}
                      onChange={(event) => {
                        setServiceFeePercent(event.target.value.replace(',', '.'));
                        invalidate();
                      }}
                    />
                  </label>
                </div>
              )}
              {serverError?.field?.startsWith('internal_adjustment') && (
                <p className="field-error">{serverError.message}</p>
              )}
            </section>
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
                  values={Object.fromEntries(
                    Object.entries(totals)
                      .filter(([key]) => key in resultLabels)
                      .map(([key, value]) => [resultLabels[key], decimal(value)]),
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
            label: 'Версия',
            render: (row) =>
              row.version_number
                ? 'Версия №' + row.version_number
                : 'Расчёт от ' + date(row.created_at),
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
          title={'Версия расчёта №' + String(selected.version_number || '')}
          wide
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
