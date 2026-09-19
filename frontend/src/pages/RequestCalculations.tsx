import { Plus, Save, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Collection } from '../components/Collection';
import { Badge, Button, DataTable, DetailPairs, ErrorBox, Modal } from '../components/ui';
import { date, decimal, label, today, units } from '../lib/format';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
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
  include_in_cost: boolean;
  include_in_cash: boolean;
};
type Selection = {
  quote_id: string;
  quote_revision: number;
  quantity: string;
  unit: string;
  mass?: string;
  volume?: string;
  variables: Record<string, string>;
};
function CalculationEditor({
  request,
  previous,
  onClose,
  onSuccess,
}: {
  request: Entity;
  previous?: Entity;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const quotes = useApi<Page>(`/requests/${request.id}/quotes`);
  const profiles = useApi<Page>('/profiles');
  const [profileId, setProfileId] = useState('');
  const [selections, setSelections] = useState<Record<string, Selection>>({});
  const [rates, setRates] = useState<Rate[]>([]);
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [reason, setReason] = useState('');
  const [preview, setPreview] = useState<Entity>();
  const [detail, setDetail] = useState(false);
  const [localError, setLocalError] = useState<unknown>();
  const operation = useCommand();
  const dirty = Boolean(profileId) || Boolean(reason) || Object.keys(selections).length > 0;
  useDirtyProtection(dirty);
  function close() {
    if (!dirty || window.confirm('Расчёт не записан. Закрыть форму?')) onClose();
  }
  function invalidate() {
    setPreview(undefined);
  }
  async function calculate(save = false) {
    try {
      const body = {
        request_version: request.version,
        profile_id: profileId,
        selections: Object.values(selections),
        rates,
        expenses,
        reason,
        ...(previous ? { previous_id: previous.id } : {}),
      };
      const result = await operation.run<Entity>(
        `/requests/${request.id}/calculations${save ? '' : '/preview'}`,
        body,
        'POST',
        true,
      );
      if (save) onSuccess();
      else setPreview(result);
    } catch {
      /* preserve editor */
    }
  }
  const snapshot = (preview?.snapshot || preview) as Record<string, unknown> | undefined;
  const outputLines = (snapshot?.lines || []) as Entity[];
  const totals = snapshot?.totals as Record<string, unknown> | undefined;
  return (
    <Modal title={previous ? 'Новая версия расчёта' : 'Новый расчёт'} wide onClose={close}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void calculate(false);
        }}
      >
        <div className="form-body calculation-editor">
          <div className="info-note">
            Предварительный расчёт выполняет сервер. Запись создаёт неизменяемый снимок квот,
            курсов, формул и результатов.
          </div>
          <ErrorBox error={operation.error || localError || quotes.error || profiles.error} />
          <div className="form-grid">
            <label className="field">
              Профиль расчёта
              <select
                required
                value={profileId}
                onChange={(e) => {
                  setProfileId(e.target.value);
                  invalidate();
                }}
              >
                <option value="">Выберите утверждённый профиль</option>
                {profiles.data?.items.map((p) => (
                  <option key={p.id} value={p.id}>
                    {label(p)} · {date(p.effective_from)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Основание версии
              <input
                required
                minLength={3}
                value={reason}
                onChange={(e) => {
                  setReason(e.target.value);
                  invalidate();
                }}
              />
            </label>
          </div>
          <h3>Выбранные квоты и количества</h3>
          <div className="selection-lines">
            {quotes.data?.items.map((quote) => (
              <div key={quote.id} className="quote-selection">
                <label className="selection-label">
                  <input
                    type="checkbox"
                    checked={Boolean(selections[quote.id])}
                    onChange={(e) => {
                      setSelections((current) => {
                        const next = { ...current };
                        if (e.target.checked)
                          next[quote.id] = {
                            quote_id: quote.id,
                            quote_revision: Number(quote.revision),
                            quantity: String(quote.available_quantity),
                            unit: String(quote.price_unit),
                            variables: {},
                          };
                        else delete next[quote.id];
                        return next;
                      });
                      invalidate();
                    }}
                  />
                  <span>
                    <strong>{String((quote.product as Entity)?.name || quote.product_id)}</strong>
                    <small>
                      {String(quote.supplier_name || quote.supplier_id)} ·{' '}
                      {quote.currency as string} · версия {quote.revision as number}
                    </small>
                  </span>
                  <Badge value={quote.is_analogue ? 'Аналог' : 'Квота'} />
                </label>
                {selections[quote.id] && (
                  <div className="form-grid">
                    <label className="field">
                      Количество
                      <input
                        required
                        inputMode="decimal"
                        pattern="[0-9]+([.,][0-9]{1,6})?"
                        value={selections[quote.id].quantity}
                        onChange={(e) => {
                          setSelections((v) => ({
                            ...v,
                            [quote.id]: {
                              ...v[quote.id],
                              quantity: e.target.value.replace(',', '.'),
                            },
                          }));
                          invalidate();
                        }}
                      />
                    </label>
                    <label className="field">
                      Единица
                      <select
                        value={selections[quote.id].unit}
                        onChange={(e) => {
                          setSelections((v) => ({
                            ...v,
                            [quote.id]: { ...v[quote.id], unit: e.target.value },
                          }));
                          invalidate();
                        }}
                      >
                        {units.map((unit) => (
                          <option key={unit.value} value={unit.value}>
                            {unit.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      Масса для распределения
                      <input
                        inputMode="decimal"
                        value={selections[quote.id].mass || ''}
                        onChange={(e) => {
                          setSelections((v) => ({
                            ...v,
                            [quote.id]: { ...v[quote.id], mass: e.target.value || undefined },
                          }));
                          invalidate();
                        }}
                      />
                    </label>
                    <label className="field">
                      Объём для распределения
                      <input
                        inputMode="decimal"
                        value={selections[quote.id].volume || ''}
                        onChange={(e) => {
                          setSelections((v) => ({
                            ...v,
                            [quote.id]: { ...v[quote.id], volume: e.target.value || undefined },
                          }));
                          invalidate();
                        }}
                      />
                    </label>
                    <label className="field wide">
                      Дополнительные параметры строки (JSON)
                      <textarea
                        rows={2}
                        defaultValue="{}"
                        className="code-input"
                        onBlur={(e) => {
                          try {
                            const variables = JSON.parse(e.target.value) as Record<string, string>;
                            setSelections((v) => ({
                              ...v,
                              [quote.id]: { ...v[quote.id], variables },
                            }));
                            setLocalError(undefined);
                            invalidate();
                          } catch {
                            setLocalError(
                              new Error(
                                'Параметры строки должны быть объектом JSON с десятичными строками.',
                              ),
                            );
                          }
                        }}
                      />
                    </label>
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="section-heading">
            <h3>Зафиксированные курсы</h3>
            <Button
              variant="secondary"
              type="button"
              onClick={() => {
                setRates((v) => [
                  ...v,
                  {
                    currency: '',
                    management_per_unit: '',
                    quoted_units: '1',
                    date: today(),
                    source: '',
                    reason: '',
                  },
                ]);
                invalidate();
              }}
            >
              <Plus size={15} />
              Курс
            </Button>
          </div>
          {rates.map((rate, index) => (
            <div className="editor-row" key={index}>
              {Object.entries(rate).map(([key, value]) => (
                <label key={key}>
                  {
                    {
                      currency: 'Валюта',
                      management_per_unit: 'Курс в валюту учёта',
                      quoted_units: 'Единиц котировки',
                      date: 'Дата',
                      source: 'Источник',
                      reason: 'Основание',
                    }[key]
                  }
                  <input
                    required
                    type={key === 'date' ? 'date' : 'text'}
                    value={value}
                    onChange={(e) => {
                      setRates((v) =>
                        v.map((row, i) => (i === index ? { ...row, [key]: e.target.value } : row)),
                      );
                      invalidate();
                    }}
                  />
                </label>
              ))}
              <button
                type="button"
                className="icon-button"
                aria-label="Удалить курс"
                onClick={() => {
                  setRates((v) => v.filter((_, i) => i !== index));
                  invalidate();
                }}
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
          <div className="section-heading">
            <h3>Общие расходы</h3>
            <Button
              variant="secondary"
              type="button"
              onClick={() => {
                setExpenses((v) => [
                  ...v,
                  {
                    name: '',
                    amount: '',
                    currency: '',
                    method: 'purchase',
                    basis: '',
                    include_in_cost: true,
                    include_in_cash: true,
                  },
                ]);
                invalidate();
              }}
            >
              <Plus size={15} />
              Статья расходов
            </Button>
          </div>
          {expenses.map((expense, index) => (
            <div className="editor-row" key={index}>
              {['name', 'amount', 'currency', 'basis'].map((key) => (
                <label key={key}>
                  {
                    { name: 'Код статьи', amount: 'Сумма', currency: 'Валюта', basis: 'Основание' }[
                      key
                    ]
                  }
                  <input
                    required
                    value={String(expense[key as keyof Expense])}
                    onChange={(e) => {
                      setExpenses((v) =>
                        v.map((row, i) => (i === index ? { ...row, [key]: e.target.value } : row)),
                      );
                      invalidate();
                    }}
                  />
                </label>
              ))}
              <label>
                Распределение
                <select
                  value={expense.method}
                  onChange={(e) => {
                    setExpenses((v) =>
                      v.map((row, i) => (i === index ? { ...row, method: e.target.value } : row)),
                    );
                    invalidate();
                  }}
                >
                  <option value="purchase">По стоимости закупки</option>
                  <option value="mass">По массе</option>
                  <option value="volume">По объёму</option>
                </select>
              </label>
              <button
                type="button"
                className="icon-button"
                aria-label="Удалить расход"
                onClick={() => {
                  setExpenses((v) => v.filter((_, i) => i !== index));
                  invalidate();
                }}
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
          {preview && (
            <div className="calculation-result">
              <div className="section-heading">
                <h3>
                  Предварительный результат <Badge value="Не записан" tone="amber" />
                </h3>
                <Button variant="ghost" type="button" onClick={() => setDetail(!detail)}>
                  {detail ? 'Общий итог' : 'Подробный расчёт'}
                </Button>
              </div>
              {totals && (
                <DetailPairs
                  values={Object.fromEntries(
                    Object.entries(totals).map(([k, v]) => [k, decimal(v)]),
                  )}
                />
              )}
              <DataTable<Entity>
                rows={outputLines.map((line, i) => ({
                  ...line,
                  id: String(line.id || line.line_id || i),
                }))}
                columns={[
                  {
                    key: 'description',
                    label: 'Позиция',
                    render: (r) => String(r.description || r.name || r.quote_id),
                  },
                  { key: 'quantity', label: 'Количество', render: (r) => decimal(r.quantity) },
                  {
                    key: 'total',
                    label: 'Итог',
                    render: (r) => decimal(r.total ?? (r.outputs as Entity)?.total),
                  },
                ]}
              />
              {detail && <pre className="snapshot-json">{JSON.stringify(snapshot, null, 2)}</pre>}
            </div>
          )}
        </div>
        <div className="modal-footer">
          <Button variant="secondary" type="button" onClick={close}>
            Закрыть
          </Button>
          <Button
            variant="secondary"
            type="submit"
            busy={operation.busy}
            disabled={!Object.keys(selections).length}
          >
            Предварительный расчёт
          </Button>
          <Button
            type="button"
            busy={operation.busy}
            disabled={!preview}
            onClick={() => void calculate(true)}
          >
            <Save size={16} />
            Записать версию
          </Button>
        </div>
      </form>
    </Modal>
  );
}
export function RequestCalculations({ request }: { request: Entity }) {
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<Entity>();
  const [previous, setPrevious] = useState<Entity>();
  const [revision, setRevision] = useState(0);
  return (
    <>
      <div className="tab-actions">
        <Button
          onClick={() => {
            setPrevious(undefined);
            setEditing(true);
          }}
        >
          <Plus size={16} />
          Новый расчёт
        </Button>
      </div>
      <Collection
        title="Неизменяемые версии расчёта"
        description="Документы сохраняют связь с исходной версией даже после изменения квот."
        endpoint={`/requests/${request.id}/calculations`}
        refreshKey={revision}
        onSelect={setSelected}
        columns={[
          { key: 'id', label: 'Версия', render: (r) => `Расчёт ${r.id.slice(0, 8)}` },
          { key: 'created_at', label: 'Записан', render: (r) => date(r.created_at, true) },
          { key: 'reason', label: 'Основание' },
          { key: 'author_id', label: 'Автор' },
          {
            key: 'digest',
            label: 'Снимок',
            render: (r) => <span className="mono">{String(r.digest || '').slice(0, 12)}</span>,
          },
        ]}
      />
      {selected && (
        <Modal title="Сохранённая версия расчёта" wide onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Дата: date(selected.created_at, true),
                Основание: selected.reason,
                'Контрольная сумма': selected.digest,
              }}
            />
            <Button
              variant="secondary"
              onClick={() => {
                setPrevious(selected);
                setSelected(undefined);
                setEditing(true);
              }}
            >
              Создать новую версию
            </Button>
            <pre className="snapshot-json">{JSON.stringify(selected.snapshot, null, 2)}</pre>
          </div>
        </Modal>
      )}
      {editing && (
        <CalculationEditor
          request={request}
          previous={previous}
          onClose={() => setEditing(false)}
          onSuccess={() => {
            setEditing(false);
            setRevision((v) => v + 1);
          }}
        />
      )}
    </>
  );
}
