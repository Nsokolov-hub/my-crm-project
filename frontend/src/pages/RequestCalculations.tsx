import { Copy, Flag, Plus, Save, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import { Badge, Button, DataTable, DetailPairs, ErrorBox, Modal } from '../components/ui';
import { api, ApiError } from '../lib/api';
import { displayCalculationMetrics } from '../lib/calculations';
import { normalizeLogisticsExpense } from '../lib/logistics';
import { date, decimal, display, label, metric } from '../lib/format';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
import { waveKindLabel, waveWeekLabel } from '../lib/waves';
import { CalculationBreakdown } from './calculations/CalculationBreakdown';
import {
  airportPickup,
  currencyOf,
  detailLabels,
  expenseFromReference,
  moscowDelivery,
  nameOf,
  plainQuantity,
  plannedVolumeChanged,
  plannedVolumeErrors,
  withCalculationGroups,
  standardExpenses,
  validMarkup,
  type Expense,
  type PlannedVolumeRow,
  type Rate,
  type Selection,
} from './calculations/model';
import { calculationErrors } from './calculations/validation';

/** Business remark of 21 October 2026: the quantity of the request position, in packings of the quote. */
function requestedQuantity(row: Entity): string {
  return row.request_packings ? plainQuantity(row.request_packings) : '1';
}

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
    planned_volume?: PlannedVolumeRow[];
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
            ...(old?.quantity ? { quantity: plainQuantity(old.quantity) } : {}),
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
  // Business remark of 21 October 2026: a client in Belarus is sold to with VAT 0 %, without VAT deduction.
  const belarus = Boolean(request.client_in_belarus);
  const [vatDeductible, setVatDeductible] = useState(
    !belarus && Boolean(previousInput.vat_deductible ?? previousSnapshot.vat_deductible ?? true),
  );
  const [deliveryDays, setDeliveryDays] = useState(
    String(
      (Object.prototype.hasOwnProperty.call(previousInput, 'delivery_days')
        ? previousInput.delivery_days
        : previousSnapshot.delivery_days) ?? '',
    ),
  );
  const [divideWaveExpenses, setDivideWaveExpenses] = useState(
    previousInput.divide_wave_expenses !== false,
  );
  const [deliveryRequired, setDeliveryRequired] = useState(
    Boolean(previousInput.delivery_required ?? true),
  );
  const [deliveryCity, setDeliveryCity] = useState(String(previousInput.delivery_city || ''));
  // Business remark of 28 October 2026: the planned volume of the wave changed by hand for this calculation.
  const [plannedVolume, setPlannedVolume] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      (Array.isArray(previousInput.planned_volume)
        ? (previousInput.planned_volume as Entity[])
        : []
      ).map((row) => [String(row.product_group_id), plainQuantity(row.quantity)]),
    ),
  );
  const [plannedComment, setPlannedComment] = useState(
    String(previousInput.planned_volume_comment || ''),
  );
  const tariffs = useApi<Page>('/delivery-tariffs');
  const [preview, setPreview] = useState<Entity>();
  const [showDetails, setShowDetails] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [saveAttempted, setSaveAttempted] = useState(false);
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
            quantity: requestedQuantity(row),
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
  // Business remark of 30 October 2026: the block is always there for an import, right under the positions;
  // a product group of the calculation the plan has no volume for can get one here too.
  const plannedRows = withCalculationGroups(waveExpenses.data?.planned_volume || [], selectedRows);
  const volumeVisible = hasImportSelections;
  const volumeShown = Boolean(request.wave_id) && volumeVisible && divideWaveExpenses;
  const volumeChanged = plannedVolumeChanged(plannedRows, plannedVolume);
  const volumeErrors = volumeShown
    ? plannedVolumeErrors(plannedRows, plannedVolume, plannedComment)
    : [];
  const errors = [
    ...calculationErrors({
      waveId: request.wave_id,
      rows: selectedRows,
      selections,
      waveExpenses: { loading: waveExpenses.loading, error: waveExpenses.error },
      profileId,
      profileReady: hydratedProfileId === profileHydrationKey,
      profile: selectedProfile?.definition as Record<string, unknown> | undefined,
      canReadReward: auth.can('finance.reward.read'),
      currencies: neededCurrencies,
      rates: profileRates,
      expenses,
      prepayment,
      deferred,
      deferredDays,
      deferredStart,
      deliveryDays,
      serviceFeePercent,
    }),
    ...volumeErrors,
  ];
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
          quantity: requestedQuantity(row),
          markup_coefficient: current[row.id]?.markup_coefficient || '1.5',
        };
      else delete next[row.id];
      return next;
    });
    invalidate();
  }
  async function calculate(save = false, quiet = false) {
    if (!quiet) setAttempted(true);
    if (save) setSaveAttempted(true);
    if (errors.length || (save && !preview) || (save && deliveryDays === '')) return;
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
          ...(divideWaveExpenses ? {} : { divide_wave_expenses: false }),
          ...(volumeShown && volumeChanged
            ? {
                planned_volume: plannedRows.map((row) => ({
                  product_group_id: row.product_group_id,
                  quantity: plannedVolume[row.product_group_id] ?? plainQuantity(row.wave_quantity),
                })),
                planned_volume_comment: plannedComment.trim(),
              }
            : {}),
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
          // Business remark of 22 October 2026: any saved calculation may be copied; the copy continues the
          // numbering of the request's versions from the latest one.
          ...(previous ? { copy_of_id: previous.id } : {}),
          ...(save && preview
            ? { previous_id: ((preview.snapshot || preview) as Entity).base_version_id || null }
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
    divideWaveExpenses,
    plannedVolume: volumeShown && volumeChanged ? plannedVolume : null,
    plannedComment: volumeShown && volumeChanged ? plannedComment.trim() : '',
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
      title={
        previous
          ? `Копия расчёта ${String(previous.calculation_number || previous.selection_label || '')}`
          : 'Новый расчёт'
      }
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
          {(attempted || Object.keys(selections).length > 0) && errors.length > 0 && (
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
                disabled={belarus}
                onChange={(event) => {
                  setVatDeductible(event.target.checked);
                  invalidate();
                }}
              />{' '}
              Расчёт с вычетом НДС
            </label>
            {belarus ? (
              <p className="info-note">
                Клиент находится в Республике Беларусь: продажа с НДС 0 % (Приложение № 18 к
                Договору о ЕАЭС), расчёт с вычетом НДС недоступен. КП и счёт выйдут с НДС 0 % и
                надписью об основании.
              </p>
            ) : (
              <p className="muted">
                При вычете ввозной НДС исключается из себестоимости и цены продажи. В потребности в
                средствах он сохраняется.
              </p>
            )}
            <label className="field">
              Общий срок поставки, дней
              <input
                type="number"
                min="0"
                max="3650"
                step="1"
                aria-label="Общий срок поставки, дней"
                required
                aria-invalid={
                  (saveAttempted && deliveryDays === '') || serverError?.field === 'delivery_days'
                }
                value={deliveryDays}
                placeholder="Укажите срок"
                onChange={(event) => {
                  setDeliveryDays(event.target.value);
                  invalidate();
                }}
              />
              {saveAttempted && deliveryDays === '' ? (
                <small className="field-error">Проверьте и укажите общий срок поставки.</small>
              ) : (
                <small>
                  Этот срок попадёт в КП. Проверьте его сами: сроки квот сюда не подставляются
                  {selectedRows.length
                    ? ` (самый долгий срок выбранных квот — ${Math.max(
                        ...selectedRows.map((row) => Number(row.delivery_days || 0)),
                      )} дн.)`
                    : ''}
                  .
                </small>
              )}
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
                  Цена указана за одну фасовку. Количество подставлено из заявки (в фасовках квоты),
                  его можно изменить; оно не ограничено количеством квоты.
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
              {auth.can('finance.reward.read') && Number(totals?.internal_bonus) > 0 && (
                <span>
                  Бонус: <strong>{metric(totals?.internal_bonus)} ₽</strong>
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
                                  quantity: current[row.id]?.quantity ?? requestedQuantity(row),
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
                      <strong>
                        {nameOf(row)}
                        {Boolean(row.last_calculation) && (
                          <Flag
                            className="calculated-flag"
                            size={12}
                            aria-label="Расчёт уже записан"
                            role="img"
                          >
                            <title>
                              {`Уже записан расчёт ${String(
                                (row.last_calculation as Entity).number || '',
                              )} от ${date((row.last_calculation as Entity).created_at)}`}
                            </title>
                          </Flag>
                        )}
                      </strong>
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
                      '1'
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
                        render: (row: Entity) => {
                          if (!selections[row.id]) return '—';
                          const coefficient =
                            selections[row.id].bonus_coefficient ||
                            String(
                              (selectedProfile?.definition as Entity | undefined)
                                ?.default_bonus_coefficient || '1',
                            );
                          const line = outputLines.find((entry) => entry.quote_item_id === row.id);
                          const bonus = ((line?.detail || {}) as Entity).internal_bonus;
                          return (
                            <span className="calculation-bonus">
                              <input
                                className="calculation-markup"
                                inputMode="decimal"
                                aria-label={'Бонус ' + nameOf(row)}
                                value={coefficient}
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
                              {Number(coefficient) > 1 && bonus != null && line && (
                                <small
                                  title={`Бонус за ${decimal(line.quantity)} шт.: ${metric(
                                    Number(bonus) / Number(line.quantity),
                                  )} ₽ за штуку`}
                                >
                                  {metric(bonus)} ₽
                                </small>
                              )}
                            </span>
                          );
                        },
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
          {volumeVisible && (
            <section className="calculation-section planned-volume-section" id="planned-volume">
              <div className="section-heading">
                <div>
                  <h3>Плановый объём волны</h3>
                  <p>
                    Общие расходы волны (логистика, декларант и т. п.) делятся на этот объём.
                    Впишите свой объём для этого расчёта — он сохранится только с комментарием. Сама
                    волна
                    {plannedRows[0]?.plan_number ? ` «${plannedRows[0].plan_number}»` : ''} не
                    меняется.
                  </p>
                </div>
              </div>
              {!request.wave_id ? (
                <p className="info-note">
                  Заявке не назначена волна. Выберите её внизу вкладки «Расчёты», в разделе «Волна
                  поставки», и откройте расчёт снова.
                </p>
              ) : waveExpenses.loading ? (
                <p className="muted">Загрузка планового объёма…</p>
              ) : (
                <>
                  {!divideWaveExpenses && (
                    <p className="info-note">
                      Включено «Не делить расходы»: все расходы волны на этом расчёте, плановый
                      объём не используется.
                    </p>
                  )}
                  <div className="table-scroll">
                    <table className="data-table planned-volume">
                      <thead>
                        <tr>
                          <th>Товарная группа</th>
                          <th>В плановой волне, шт.</th>
                          <th>В этом расчёте, шт.</th>
                        </tr>
                      </thead>
                      <tbody>
                        {plannedRows.map((row) => (
                          <tr key={row.product_group_id}>
                            <td>{row.product_group_name}</td>
                            <td>
                              {row.in_plan === false ? 'нет в плане' : decimal(row.wave_quantity)}
                            </td>
                            <td>
                              <input
                                aria-label={`Плановый объём в расчёте: ${row.product_group_name}`}
                                inputMode="numeric"
                                disabled={!divideWaveExpenses}
                                value={
                                  plannedVolume[row.product_group_id] ??
                                  plainQuantity(row.wave_quantity)
                                }
                                aria-invalid={serverError?.field?.startsWith('planned_volume.')}
                                onChange={(event) => {
                                  const value = event.target.value.replace(/\s/g, '');
                                  setPlannedVolume((current) => ({
                                    ...current,
                                    [row.product_group_id]: value,
                                  }));
                                  invalidate();
                                }}
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {plannedRows.some((row) => row.in_plan === false) && (
                    <small className="muted">
                      «Нет в плане» — у плановой волны нет объёма по этой группе. Объём, указанный
                      здесь, считается по цене и весу позиции этого расчёта.
                    </small>
                  )}
                  {preview && divideWaveExpenses && (
                    <p className="info-note">
                      Расходы волны сейчас делятся на{' '}
                      <strong>{decimal(waveDistribution.total_quantity || 0)} шт.</strong>: в этом
                      расчёте {decimal(waveDistribution.selected_quantity || 0)}, в закупках{' '}
                      {decimal(waveDistribution.existing_quantity || 0)}, по плану{' '}
                      {decimal(waveDistribution.forecast_quantity || 0)}.
                    </p>
                  )}
                  {volumeShown && volumeChanged && (
                    <label className="field">
                      Комментарий к плановому объёму
                      <textarea
                        value={plannedComment}
                        required
                        aria-invalid={
                          (attempted && plannedComment.trim().length < 3) ||
                          serverError?.field === 'planned_volume_comment'
                        }
                        placeholder="Например: клиент подтвердил ещё 10 шт. в ноябре"
                        onChange={(event) => {
                          setPlannedComment(event.target.value);
                          invalidate();
                        }}
                      />
                      <small>Обязателен: без комментария объём не сохранится.</small>
                    </label>
                  )}
                  {volumeShown && volumeChanged && (
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() => {
                        setPlannedVolume({});
                        setPlannedComment('');
                        invalidate();
                      }}
                    >
                      Вернуть объём волны
                    </Button>
                  )}
                  {serverError?.field?.startsWith('planned_volume') && (
                    <p className="field-error">{serverError.message}</p>
                  )}
                </>
              )}
            </section>
          )}
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
          {Boolean(request.wave_id) && auth.can('waves.write') && hasImportSelections && (
            <section className="calculation-section">
              <label className="calculation-internal-toggle">
                <input
                  type="checkbox"
                  checked={!divideWaveExpenses}
                  onChange={(event) => {
                    setDivideWaveExpenses(!event.target.checked);
                    invalidate();
                  }}
                />{' '}
                Не делить расходы
              </label>
              <p className="muted">
                Расчёт остаётся привязан к волне, но все её общие расходы и таможенный сбор ложатся
                только на этот расчёт: другие заказы и плановый объём волны их не делят.
              </p>
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
                      true,
                    )
                    .then(() => {
                      waveExpenses.refresh();
                      setHydratedProfileId('');
                      invalidate();
                    })
                    .catch((cause) => {
                      // Someone saved the wave meanwhile: reload its version for the next attempt.
                      if (cause instanceof ApiError && cause.status === 409) waveExpenses.refresh();
                    });
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
              {!divideWaveExpenses && ' · Расходы волны не делятся: все на этом расчёте.'}
              {Boolean(waveDistribution.customs_fee_scale_waived) &&
                ' · В волне есть колонки: платится только сбор 73 800 ₽ на колонки, сбор по шкале не начисляется.'}
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
                      'Сбор 1 по шкале от таможенной стоимости обычных товаров. Если в волне есть колонки, платится только сбор 2: 73 800 ₽ делится между колонками, другие группы сбором не облагаются.',
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
  const waves = useApi<Page>('/waves?open=true');
  const waveCommand = useCommand();
  const [waveId, setWaveId] = useState(String(request.wave_id || ''));
  const [assignedWaveId, setAssignedWaveId] = useState(String(request.wave_id || ''));
  const [requestVersion, setRequestVersion] = useState(Number(request.version));
  const seenVersion = useRef(Number(request.version));
  // The tab may first receive the cached request while it reloads; a newer copy replaces the stale
  // version, otherwise «Сохранить волну» fails with «Данные изменились» until the page is reopened.
  useEffect(() => {
    const version = Number(request.version);
    if (!(version > seenVersion.current)) return;
    seenVersion.current = version;
    setRequestVersion((current) => Math.max(current, version));
    setAssignedWaveId(String(request.wave_id || ''));
    setWaveId(String(request.wave_id || ''));
  }, [request.version, request.wave_id]);
  async function saveWave(): Promise<boolean> {
    const path = '/requests/' + request.id + '/wave';
    const save = (version: number) =>
      waveCommand.run<Entity>(
        path,
        { request_version: version, wave_id: waveId || null },
        'PUT',
        true,
      );
    let result: Entity | undefined;
    try {
      result = await save(requestVersion);
    } catch (cause) {
      if (!(cause instanceof ApiError && cause.code === 'VERSION_CONFLICT')) return false;
      // Quotes, items or stages also change the request version. If the wave itself was not
      // reassigned by someone else, repeat the assignment on the current version.
      const fresh = await api<Entity>('/requests/' + request.id).catch(() => undefined);
      if (!fresh) return false;
      setRequestVersion(Number(fresh.version));
      if (String(fresh.wave_id || '') !== assignedWaveId) {
        setAssignedWaveId(String(fresh.wave_id || ''));
        return false;
      }
      result = await save(Number(fresh.version)).catch(() => undefined);
    }
    if (result) {
      setRequestVersion(Number(result.version));
      setAssignedWaveId(String(result.wave_id || ''));
    }
    return Boolean(result);
  }
  // Business remark of 30 October 2026: a wave picked but not saved left the request without a wave, and
  // the calculation silently did not compute. Opening a calculation saves the picked wave first.
  const waveUnsaved = auth.can('waves.write') && waveId !== assignedWaveId;
  async function withSavedWave(open: () => void) {
    if (waveUnsaved && !(await saveWave())) return;
    open();
  }
  const openWaves = waves.data?.items || [];
  // A closed or archived wave is no longer offered, but the request may still name it.
  const missingWave = useApi<Entity>(
    waves.data && assignedWaveId && !openWaves.some((wave) => wave.id === assignedWaveId)
      ? `/waves/${assignedWaveId}`
      : null,
  );
  const assignedWave =
    openWaves.find((wave) => wave.id === assignedWaveId) ||
    (missingWave.data?.id === assignedWaveId ? missingWave.data : undefined);
  const workingWaves = (assignedWave?.working_waves || []) as Entity[];
  const [editing, setEditing] = useState(launchQuoteItemIds.length > 0);
  const [initialIds, setInitialIds] = useState(launchQuoteItemIds);
  const [selected, setSelected] = useState<Entity>();
  const [previous, setPrevious] = useState<Entity>();
  const [revision, setRevision] = useState(0);
  function copy(row: Entity) {
    void withSavedWave(() => openCopy(row));
  }
  function openCopy(row: Entity) {
    setPrevious(row);
    setSelected(undefined);
    setInitialIds(
      (((row.snapshot as Entity | undefined)?.lines as Entity[]) || [])
        .map((line) => String(line.quote_item_id || ''))
        .filter(Boolean),
    );
    setEditing(true);
  }
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
              Волна нужна для импорта: руководитель назначает её до расчёта. Позиции поставщиков с
              типом расчёта DAP или «Перепродажа внутри РФ» (задаётся в карточке поставщика)
              считаются без волны. По плановой волне общие расходы делятся на плановый объём, а
              подтверждённый заказ попадает в её рабочую волну — она создаётся автоматически. По
              рабочей волне расходы делятся на её заказы и, пока она комплектуется, на остаток
              плана. Заявки плана можно перенести в рабочую волну из её карточки или выбрать её
              здесь.
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
            {assignedWave && !openWaves.includes(assignedWave) && (
              <option value={assignedWave.id}>
                {waveKindLabel(assignedWave)} · {String(assignedWave.number)} ·{' '}
                {assignedWave.archived ? 'в архиве' : display(assignedWave.status)}
              </option>
            )}
            {(['plan', 'work'] as const).map((kind) => {
              const rows = openWaves.filter((wave) => (wave.kind || 'work') === kind);
              return rows.length > 0 ? (
                <optgroup key={kind} label={kind === 'plan' ? 'Плановые' : 'Рабочие'}>
                  {rows.map((wave) => (
                    <option key={wave.id} value={wave.id}>
                      {String(wave.number)} · {String(wave.supplier_name || 'Поставщик не указан')}{' '}
                      · {waveWeekLabel(wave, 'departure')}
                    </option>
                  ))}
                </optgroup>
              ) : null;
            })}
          </select>
          {auth.can('waves.write') && (
            <Button
              type="button"
              variant="secondary"
              busy={waveCommand.busy}
              disabled={waveId === assignedWaveId || editing}
              onClick={() => void saveWave()}
            >
              Сохранить волну
            </Button>
          )}
        </div>
        {waveUnsaved && (
          <p className="field-error" role="status">
            Волна выбрана, но не сохранена в заявке. Нажмите «Сохранить волну» — или она сохранится
            сама, когда вы откроете новый расчёт.
          </p>
        )}
        {Boolean(assignedWave?.archived) && (
          <p className="field-error">
            Назначенная волна в архиве. Выберите действующую волну перед расчётом.
          </p>
        )}
        {workingWaves.length > 0 && (
          <p className="muted">
            Рабочие волны этой плановой:{' '}
            {workingWaves
              .map((wave) => `${String(wave.number)} (${display(wave.status)})`)
              .join(', ')}
          </p>
        )}
      </section>
      <div className="tab-actions">
        <Button
          busy={waveCommand.busy}
          onClick={() =>
            void withSavedWave(() => {
              setPrevious(undefined);
              setInitialIds([]);
              setEditing(true);
            })
          }
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
              row.wave_moved ? (
                <Badge value="Волна заявки изменена" tone="amber" />
              ) : row.wave_stale ? (
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
          {
            key: 'copy',
            label: 'Действия',
            sortable: false,
            render: (row) => (
              <Button
                type="button"
                variant="ghost"
                title="Открыть новый расчёт с теми же настройками, расходами и позициями"
                onClick={(event) => {
                  event.stopPropagation();
                  copy(row);
                }}
              >
                <Copy size={15} /> Копировать
              </Button>
            ),
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
              Копия откроется с теми же квотами, количествами, наценками, расходами и условиями
              оплаты. Отметьте новые позиции, пересчитайте и сохраните — это будет следующая версия
              расчёта заявки.
            </p>
            {Boolean(selected.wave_moved) && (
              <p className="info-note" role="status">
                Заявку перенесли в другую волну после этого расчёта. Скопируйте его в новый расчёт —
                общие расходы разделятся с заказами новой волны.
              </p>
            )}
            {Boolean(selected.wave_stale) && (
              <p className="info-note" role="status">
                Состав или расходы волны изменились. Скопируйте расчёт в новый и пересчитайте перед
                новым КП.
              </p>
            )}
            <Button
              variant="secondary"
              onClick={() => {
                copy(selected);
              }}
            >
              <Copy size={16} /> Скопировать в новый расчёт
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
