import { Building2, MapPin, Ship } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, ErrorBox, Modal } from '../../components/ui';
import { date, decimal, display } from '../../lib/format';
import { useApi } from '../../lib/hooks';
import type { Entity } from '../../lib/types';
import { waveKindLabel, waveWeekLabel } from '../../lib/waves';
import './WavePicker.scss';

type SupplierOption = {
  key: string;
  id?: string;
  name: string;
  card?: Entity;
};

function text(value: unknown) {
  return String(value ?? '').trim();
}

function supplierKey(id: unknown, name: unknown) {
  const supplierId = text(id);
  return supplierId || (text(name) ? `name:${text(name)}` : '');
}

function sameSupplier(wave: Entity, supplier: SupplierOption | undefined) {
  if (!supplier) return false;
  if (supplier.id && wave.supplier_id) return String(wave.supplier_id) === supplier.id;
  return Boolean(
    supplier.name &&
    wave.supplier_name &&
    text(wave.supplier_name).toLocaleLowerCase('ru-RU') ===
      supplier.name.toLocaleLowerCase('ru-RU'),
  );
}

function waveSelectable(wave: Entity) {
  return !wave.archived && ['planned', 'assembling'].includes(String(wave.status || 'assembling'));
}

function forecastFigures(wave: Entity) {
  const rows = ((wave.forecasts || []) as Entity[]).filter((row) => row.active !== false);
  const target = rows.reduce((sum, row) => sum + Number(row.target_quantity || 0), 0);
  const remaining = rows.every((row) => row.remaining_quantity != null)
    ? rows.reduce((sum, row) => sum + Number(row.remaining_quantity || 0), 0)
    : null;
  return { rows, target, remaining };
}

function WaveCard({
  wave,
  waves,
  chosen,
  disabled,
  onChoose,
}: {
  wave: Entity;
  waves: Entity[];
  chosen: boolean;
  disabled: boolean;
  onChoose: () => void;
}) {
  const plan = wave.kind === 'plan';
  const forecast = plan ? forecastFigures(wave) : null;
  const linked = plan
    ? [
        ...((wave.working_waves || []) as Entity[]),
        ...waves.filter(
          (candidate) =>
            candidate.plan_id === wave.id &&
            !((wave.working_waves || []) as Entity[]).some((row) => row.id === candidate.id),
        ),
      ]
    : [];
  const orderLines = (wave.order_lines || {}) as Entity;
  const orderCount = Object.values(orderLines).reduce<number>(
    (sum, value) => sum + Number(value || 0),
    0,
  );
  const orderedQuantity = ((wave.allocations || []) as Entity[]).reduce(
    (sum, row) => sum + Number(row.quantity || 0),
    0,
  );
  return (
    <button
      type="button"
      className={`wave-picker-wave${chosen ? ' chosen' : ''}`}
      aria-pressed={chosen}
      disabled={disabled || !waveSelectable(wave)}
      title={
        !waveSelectable(wave) ? 'Закрытую или архивную волну нельзя назначить заново' : undefined
      }
      onClick={onChoose}
    >
      <span className="wave-picker-wave-title">
        <strong>{text(wave.number) || 'Без номера'}</strong>
        <span className="wave-picker-badges">
          <Badge value={wave.status} />
          {Boolean(wave.archived) && <Badge value="В архиве" tone="gray" />}
        </span>
      </span>
      <span className="wave-picker-route">
        <Ship size={14} /> {text(wave.route) || 'Маршрут не указан'}
      </span>
      {!plan && Boolean(wave.plan_number) && (
        <small>
          Плановая волна: <strong>{text(wave.plan_number)}</strong>
        </small>
      )}
      <span className="wave-picker-schedule">
        {(['close', 'departure', 'arrival'] as const).map((prefix) => (
          <span key={prefix}>
            <small>
              {prefix === 'close'
                ? 'Приём заказов'
                : prefix === 'departure'
                  ? 'Отправка'
                  : 'Прибытие'}
            </small>
            <b>{date(wave[`${prefix}_date`])}</b>
            <small>{waveWeekLabel(wave, prefix)}</small>
          </span>
        ))}
      </span>
      {forecast && (
        <span className="wave-picker-forecast">
          <b>
            План: {decimal(forecast.target)} шт. · Остаток:{' '}
            {forecast.remaining === null ? '—' : `${decimal(forecast.remaining)} шт.`}
          </b>
          {forecast.rows.map((row) => (
            <small key={row.id}>
              {text(row.product_group_name) || 'Товарная группа'}: {decimal(row.target_quantity)}{' '}
              шт.; остаток{' '}
              {row.remaining_quantity == null ? '—' : `${decimal(row.remaining_quantity)} шт.`}
            </small>
          ))}
        </span>
      )}
      {plan && linked.length > 0 && (
        <small>
          Связанные рабочие:{' '}
          {linked.map((row) => `${text(row.number)} (${display(row.status)})`).join(', ')}
        </small>
      )}
      {!plan && (orderCount > 0 || orderedQuantity > 0 || text(wave.origin_country)) && (
        <small>
          {text(wave.origin_country) && <>Страна отправления: {text(wave.origin_country)}</>}
          {orderedQuantity > 0 && ` · Заказано: ${decimal(orderedQuantity)} шт.`}
          {orderCount > 0 && ` · Позиций в заказах: ${orderCount}`}
        </small>
      )}
    </button>
  );
}

function SupplierCard({ supplier }: { supplier: SupplierOption | undefined }) {
  const detail = useApi<Entity>(supplier?.id ? `/counterparties/${supplier.id}` : null);
  const card = detail.data || supplier?.card;
  const details = (card?.details || {}) as Entity;
  const logistics = text(details['Логистика по умолчанию']);
  const logisticsCurrency = text(details['Валюта логистики']);
  const comment =
    text(details['Дополнительные сведения']) ||
    text(details['Комментарий']) ||
    text(details.comment);
  const conditions = [
    ['Тип расчёта', text(details.calculation_type)],
    ['Условия оплаты', text(details.payment_terms)],
    ['Условия доставки', text(details.delivery_terms)],
    ['Контракт', text(details.contract)],
  ].filter(([, value]) => value);
  return (
    <aside className="wave-picker-supplier-card" aria-label="Карточка поставщика">
      <div className="wave-picker-column-title">
        <Building2 size={17} />
        <h3>Поставщик</h3>
      </div>
      {!supplier ? (
        <p className="muted">Выберите поставщика слева, чтобы увидеть его условия.</p>
      ) : (
        <>
          <h4>{supplier.name}</h4>
          {detail.loading && <p className="muted">Загружаем карточку…</p>}
          <ErrorBox error={detail.error} />
          <dl>
            {(text(card?.country) || text(waveCountry(supplier))) && (
              <div>
                <dt>Страна</dt>
                <dd>{text(card?.country) || text(waveCountry(supplier))}</dd>
              </div>
            )}
            {text(details.city) && (
              <div>
                <dt>Город</dt>
                <dd>{text(details.city)}</dd>
              </div>
            )}
            {text(details['Юридический адрес']) && (
              <div>
                <dt>Адрес</dt>
                <dd>{text(details['Юридический адрес'])}</dd>
              </div>
            )}
            <div>
              <dt>Логистика по умолчанию</dt>
              <dd>{logistics ? `${decimal(logistics)} ${logisticsCurrency}`.trim() : '—'}</dd>
            </div>
          </dl>
          {conditions.length > 0 && (
            <dl>
              {conditions.map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          )}
          {comment && (
            <div className="wave-picker-comment">
              <MapPin size={14} />
              <p>{comment}</p>
            </div>
          )}
          {!card && !detail.loading && !detail.error && (
            <p className="muted">Дополнительные сведения в карточке не заполнены.</p>
          )}
        </>
      )}
    </aside>
  );
}

// A list row already contains the country; the detail request remains optional.
function waveCountry(supplier: SupplierOption) {
  return supplier.card?.country;
}

export function WavePickerDialog({
  waves,
  quoteItems,
  counterparties,
  value,
  canWrite,
  busy = false,
  loading = false,
  error,
  onChoose,
  onClose,
}: {
  waves: Entity[];
  quoteItems: Entity[];
  counterparties: Entity[];
  value: string;
  canWrite: boolean;
  busy?: boolean;
  loading?: boolean;
  error?: unknown;
  onChoose: (waveId: string) => void | boolean | Promise<void | boolean>;
  onClose: () => void;
}) {
  const suppliers = useMemo<SupplierOption[]>(() => {
    const directory = new Map<string, Entity>();
    for (const card of counterparties) {
      directory.set(String(card.id), card);
      if (card.name) directory.set(`name:${text(card.name)}`, card);
    }
    const result = new Map<string, SupplierOption>();
    for (const quote of quoteItems) {
      const key = supplierKey(quote.supplier_id, quote.supplier_name);
      if (!key || result.has(key)) continue;
      const id = text(quote.supplier_id) || undefined;
      const name = text(quote.supplier_name) || text(directory.get(key)?.name) || 'Поставщик';
      result.set(key, { key, id, name, card: directory.get(key) });
    }
    return [...result.values()].sort((a, b) => a.name.localeCompare(b.name, 'ru'));
  }, [quoteItems, counterparties]);
  const currentWave = waves.find((wave) => wave.id === value);
  const initialSupplier = currentWave
    ? supplierKey(currentWave.supplier_id, currentWave.supplier_name)
    : '';
  const [supplier, setSupplier] = useState(initialSupplier);
  const [draft, setDraft] = useState(value);
  useEffect(() => {
    if (supplier) return;
    if (initialSupplier) setSupplier(initialSupplier);
    else if (suppliers.length === 1) setSupplier(suppliers[0].key);
  }, [initialSupplier, supplier, suppliers]);
  const selectedSupplier =
    suppliers.find((row) => row.key === supplier) ||
    (currentWave && supplier === initialSupplier
      ? {
          key: supplier,
          id: text(currentWave.supplier_id) || undefined,
          name: text(currentWave.supplier_name) || 'Поставщик',
        }
      : undefined);
  const supplierWaves = waves.filter((wave) => sameSupplier(wave, selectedSupplier));
  const draftWave = waves.find((wave) => wave.id === draft);
  const close = () => {
    if (!busy) onClose();
  };
  async function confirm() {
    if (draft && (!draftWave || !waveSelectable(draftWave))) return;
    const accepted = await onChoose(draft);
    if (accepted !== false) onClose();
  }
  return (
    <Modal title="Выбор волны поставки" wide className="wave-picker-modal" onClose={close}>
      <div className="form-body">
        <ErrorBox error={error} />
        <div className="wave-picker-layout">
          <section className="wave-picker-suppliers" aria-label="Поставщики заявки">
            <div className="wave-picker-column-title">
              <h3>Поставщики</h3>
              <Badge value={`${suppliers.length}`} tone="gray" />
            </div>
            {loading ? (
              <p className="muted">Загружаем квоты…</p>
            ) : suppliers.length ? (
              <div className="wave-picker-supplier-list">
                {suppliers.map((row) => (
                  <button
                    type="button"
                    key={row.key}
                    className={row.key === supplier ? 'chosen' : ''}
                    aria-pressed={row.key === supplier}
                    onClick={() => {
                      setSupplier(row.key);
                      if (draftWave && !sameSupplier(draftWave, row)) setDraft('');
                    }}
                  >
                    <Building2 size={15} />
                    <span>{row.name}</span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="muted">В заявке пока нет квот с поставщиками.</p>
            )}
          </section>
          <section className="wave-picker-waves" aria-label="Волны поставщика">
            <div className="wave-picker-column-title">
              <h3>{selectedSupplier ? `Волны · ${selectedSupplier.name}` : 'Волны'}</h3>
            </div>
            <button
              type="button"
              className={`wave-picker-none${draft === '' ? ' chosen' : ''}`}
              aria-pressed={draft === ''}
              disabled={!canWrite}
              onClick={() => setDraft('')}
            >
              Без волны
              <small>Для DAP и перепродажи внутри РФ</small>
            </button>
            {(['plan', 'work'] as const).map((kind) => {
              const rows = supplierWaves.filter((wave) => (wave.kind || 'work') === kind);
              return (
                <div className="wave-picker-kind" key={kind}>
                  <h4>{kind === 'plan' ? 'Плановые волны' : 'Рабочие волны'}</h4>
                  {rows.length ? (
                    rows.map((wave) => (
                      <WaveCard
                        key={wave.id}
                        wave={wave}
                        waves={waves}
                        chosen={draft === wave.id}
                        disabled={!canWrite}
                        onChoose={() => setDraft(String(wave.id))}
                      />
                    ))
                  ) : (
                    <p className="muted">
                      {selectedSupplier ? 'Нет доступных волн.' : 'Сначала выберите поставщика.'}
                    </p>
                  )}
                </div>
              );
            })}
          </section>
          <SupplierCard supplier={selectedSupplier} />
        </div>
      </div>
      <div className="modal-footer">
        <span className="wave-picker-selection">
          {draftWave
            ? `${waveKindLabel(draftWave)} · ${text(draftWave.number)}`
            : 'Выбрано: без волны'}
        </span>
        <Button type="button" variant="secondary" disabled={busy} onClick={close}>
          Отмена
        </Button>
        <Button
          type="button"
          busy={busy}
          disabled={!canWrite || Boolean(draft && (!draftWave || !waveSelectable(draftWave)))}
          title={!canWrite ? 'Для изменения волны нужно право waves.write' : undefined}
          onClick={() => void confirm()}
        >
          Выбрать волну
        </Button>
      </div>
    </Modal>
  );
}
