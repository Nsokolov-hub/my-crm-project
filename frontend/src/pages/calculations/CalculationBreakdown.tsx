import { useAuth } from '../../app/Auth';
import { DataTable, DetailPairs } from '../../components/ui';
import { displayCalculationMetrics } from '../../lib/calculations';
import { date, decimal, metric } from '../../lib/format';
import type { Entity } from '../../lib/types';
import { detailLabels } from './model';

export function CalculationBreakdown({ snapshot }: { snapshot: Entity }) {
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
