import { Download, Plus, Trash2, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { Collection } from '../components/Collection';
import { DirectorySelect, RecordForm } from '../components/Form';
import { Badge, Button, DetailPairs, ErrorBox, Modal } from '../components/ui';
import { ApiError, api, download } from '../lib/api';
import { date, decimal, nowLocal } from '../lib/format';
import { useApi, useCommand, useDebounced, useDirtyProtection } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';
import { useAuth } from '../app/Auth';
import { SupplierMailEditor } from '../components/SupplierMailEditor';
import { TableImportDialog } from '../components/TableImportDialog';
export function RequestRfqs({
  requestId,
  requestNumber,
  launchItemIds = [],
  onLaunchConsumed,
}: {
  requestId: string;
  requestNumber?: string;
  launchItemIds?: string[];
  onLaunchConsumed?: () => void;
}) {
  const [selected, setSelected] = useState<Entity>();
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<unknown>();
  const [revision, setRevision] = useState(0);
  const [launching, setLaunching] = useState(launchItemIds.length > 0);
  const [mailing, setMailing] = useState(false);
  const items = useApi<Page>(`/requests/${requestId}/supplier-mail/items`);
  const fields: Field[] = [
    {
      name: 'supplier_id',
      label: 'Поставщик (необязательно)',
      help: 'Оставьте пустым, чтобы выгрузить единый запрос для всех поставщиков.',
      type: 'select',
      source: '/counterparties?kind=supplier',
    },
    {
      name: 'item_ids',
      label: 'Позиции запроса',
      required: true,
      type: 'multiselect',
      source: `/requests/${requestId}/items`,
      labelKey: 'description',
      wide: true,
    },
    { name: 'response_due', label: 'Ответ до', required: true, type: 'date' },
    { name: 'comment', label: 'Комментарий поставщику', type: 'textarea' },
  ];
  return (
    <>
      <ErrorBox error={error} />
      <Button onClick={() => setMailing(true)}>Отправить запрос по электронной почте</Button>
      <Collection
        title="Отправленные письма"
        endpoint={`/requests/${requestId}/supplier-mail`}
        refreshKey={revision}
        columns={[
          { key: 'recipient', label: 'Кому' },
          {
            key: 'cc',
            label: 'Копия',
            render: (row) => ((row.cc || []) as string[]).join(', ') || '—',
          },
          { key: 'subject', label: 'Тема' },
          { key: 'status', label: 'Отправка', render: (r) => <Badge value={r.status} /> },
          { key: 'sent_at', label: 'Дата', render: (r) => date(r.sent_at, true) },
        ]}
      />
      {mailing && (
        <SupplierMailEditor
          requestId={requestId}
          requestNumber={requestNumber}
          itemIds={
            launchItemIds.length ? launchItemIds : (items.data?.items || []).map((r) => r.id)
          }
          onClose={() => setMailing(false)}
          onSuccess={() => {
            setMailing(false);
            setRevision((v) => v + 1);
          }}
        />
      )}
      <Collection
        title="Запросы поставщикам"
        description="Единый файл можно отправить всем поставщикам. При необходимости создайте запрос отдельному поставщику."
        endpoint={`/requests/${requestId}/rfqs`}
        fields={fields}
        createLabel="Создать запрос"
        command
        refreshKey={revision}
        onSelect={setSelected}
        columns={[
          {
            key: 'id',
            label: 'Запрос',
            render: (r) =>
              String(r.number || r.request_number || requestNumber || 'Запрос поставщику'),
          },
          {
            key: 'supplier_id',
            label: 'Поставщик',
            render: (r) => String(r.supplier_name || r.supplier_id || 'Общий запрос'),
          },
          { key: 'revision', label: 'Редакция' },
          { key: 'created_at', label: 'Создан', render: (r) => date(r.created_at) },
          {
            key: 'sent_at',
            label: 'Отправка',
            render: (r) => (r.sent_at ? date(r.sent_at, true) : <Badge value="draft" />),
          },
          {
            key: 'file',
            label: 'Файл',
            sortable: false,
            render: (r) => (
              <Button
                variant="ghost"
                onClick={() =>
                  void download(
                    `/rfqs/${r.id}/file`,
                    `RFQ_${String(r.request_number || requestNumber || 'request')}.xlsx`,
                  ).catch(setError)
                }
              >
                <Download size={16} />
                XLSX
              </Button>
            ),
          },
        ]}
      />
      {launching && (
        <RecordForm
          title="Запрос по выбранным позициям"
          endpoint={`/requests/${requestId}/rfqs`}
          fields={fields}
          initial={{ item_ids: launchItemIds }}
          command
          onClose={() => {
            setLaunching(false);
            onLaunchConsumed?.();
          }}
          onSuccess={() => {
            setLaunching(false);
            onLaunchConsumed?.();
            setRevision((value) => value + 1);
          }}
        />
      )}
      {selected && !sending && (
        <Modal
          title={selected.supplier_id ? 'Запрос поставщику' : 'Общий запрос поставщикам'}
          onClose={() => setSelected(undefined)}
        >
          <div className="form-body">
            <DetailPairs
              values={{
                Поставщик: selected.supplier_name || selected.supplier_id || 'Все поставщики',
                Редакция: selected.revision,
                Создан: date(selected.created_at),
                Отправлен: date(selected.sent_at, true),
                Канал: selected.sent_channel,
              }}
            />
            <Button onClick={() => setSending(true)}>Отметить отправку</Button>
          </div>
        </Modal>
      )}
      {selected && sending && (
        <RecordForm
          title="Отметить отправку запроса"
          endpoint={`/rfqs/${selected.id}/sent`}
          fields={[
            { name: 'channel', label: 'Канал и адрес получателя', required: true },
            {
              name: 'sent_at',
              label: 'Дата и время отправки',
              type: 'datetime-local',
              required: true,
              value: nowLocal(),
            },
          ]}
          command
          extra={{ version: selected.version }}
          onClose={() => setSending(false)}
          onSuccess={() => {
            setSelected(undefined);
            setSending(false);
            setRevision((v) => v + 1);
          }}
        />
      )}
    </>
  );
}
type QuoteDraft = {
  key: number;
  source_request_item_id: string;
  nomenclature_id: string;
  packing_id: string;
  quantity: string;
  unit_price: string;
  currency_id: string;
  delivery_days: string;
  historyHint?: string;
};
function blankQuoteRow(key: number): QuoteDraft {
  return {
    key,
    source_request_item_id: '',
    nomenclature_id: '',
    packing_id: '',
    quantity: '1',
    unit_price: '',
    currency_id: '',
    delivery_days: '',
  };
}
function quoteName(row: Entity) {
  return String(
    row.nomenclature_name || (row.nomenclature as Entity | undefined)?.name || row.name || 'Товар',
  );
}
function QuoteSheetEditor({
  requestId,
  onClose,
  onSaved,
}: {
  requestId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const currencies = useApi<Page>('/currencies?page_size=100');
  const [itemSearch, setItemSearch] = useState('');
  const [knownItems, setKnownItems] = useState<Record<string, Entity>>({});
  const debouncedItemSearch = useDebounced(itemSearch);
  const requestItems = useApi<Page>(
    `/requests/${requestId}/items?page_size=100&q=${encodeURIComponent(debouncedItemSearch)}`,
  );
  const requestItemOptions = [
    ...Object.values(knownItems),
    ...(requestItems.data?.items || []),
  ].filter(
    (item, index, all) =>
      !item.archived && all.findIndex((candidate) => candidate.id === item.id) === index,
  );
  const rfqs = useApi<Page>(`/requests/${requestId}/rfqs?page_size=100`);
  const operation = useCommand();
  const nextKey = useRef(1);
  const [supplierId, setSupplierId] = useState('');
  const [rfqId, setRfqId] = useState('');
  const [rows, setRows] = useState<QuoteDraft[]>([blankQuoteRow(0)]);
  const [localError, setLocalError] = useState<unknown>();
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const dirty = Boolean(
    supplierId ||
    rfqId ||
    rows.some((row) => row.nomenclature_id || row.unit_price || row.source_request_item_id),
  );
  useDirtyProtection(dirty);
  const close = () => {
    if (!dirty || window.confirm('Есть несохранённые строки. Закрыть без сохранения?')) onClose();
  };
  function patchRow(key: number, values: Partial<QuoteDraft>) {
    setRows((current) =>
      current.map((row) =>
        row.key === key ? { ...row, ...values, historyHint: values.historyHint } : row,
      ),
    );
    setRowErrors((current) => ({ ...current, [key]: '' }));
    setLocalError(undefined);
  }
  async function findLatest(
    key: number,
    supplier: string,
    nomenclature: string,
    packing: string,
    currency = '',
  ) {
    if (!supplier || !nomenclature || !packing) return;
    const query = new URLSearchParams({
      supplier_id: supplier,
      nomenclature_id: nomenclature,
      packing_id: packing,
    });
    if (currency) query.set('currency_id', currency);
    try {
      const result = await api<{ item: Entity | null }>(`/quote-items/latest?${query}`);
      const latest = result.item;
      if (!latest) return;
      setRows((current) =>
        current.map((row) =>
          row.key === key &&
          row.nomenclature_id === nomenclature &&
          row.packing_id === packing &&
          !row.unit_price
            ? {
                ...row,
                unit_price: String(latest.unit_price ?? ''),
                currency_id: String(latest.currency_id || row.currency_id),
                delivery_days: String(latest.delivery_days ?? row.delivery_days),
                historyHint: 'Подставлена актуальная цена из истории; её можно изменить.',
              }
            : row,
        ),
      );
    } catch {
      // История ускоряет ввод, но недоступность подсказки не блокирует ручное заполнение.
    }
  }
  async function save() {
    const active = rows.filter(
      (row) =>
        row.source_request_item_id ||
        row.nomenclature_id ||
        row.packing_id ||
        row.unit_price ||
        row.currency_id ||
        row.delivery_days,
    );
    const errors: Record<number, string> = {};
    if (!supplierId) setLocalError(new Error('Выберите поставщика для квоты.'));
    else if (!active.length) setLocalError(new Error('Добавьте хотя бы одну строку квоты.'));
    if (!supplierId || !active.length) return;
    active.forEach((row) => {
      if (
        !row.nomenclature_id ||
        !row.packing_id ||
        !row.quantity ||
        !row.currency_id ||
        !row.unit_price ||
        row.delivery_days === ''
      )
        errors[row.key] = 'Выберите товар, фасовку, количество, цену, валюту и срок поставки.';
      else if (
        !Number.isInteger(Number(row.quantity)) ||
        Number(row.quantity) <= 0 ||
        !Number.isFinite(Number(row.unit_price.replace(',', '.'))) ||
        Number(row.unit_price.replace(',', '.')) < 0
      )
        errors[row.key] =
          'Количество фасовок должно быть целым числом больше нуля, цена не может быть отрицательной.';
      else if (
        row.delivery_days &&
        (!Number.isInteger(Number(row.delivery_days)) || Number(row.delivery_days) < 0)
      )
        errors[row.key] = 'Срок поставки должен быть целым числом дней.';
    });
    setRowErrors(errors);
    if (Object.keys(errors).length) {
      setLocalError(new Error('Исправьте отмеченные строки квоты.'));
      return;
    }
    try {
      await operation.run(
        `/requests/${requestId}/quote-sheets`,
        {
          supplier_id: supplierId,
          ...(rfqId ? { supplier_request_id: rfqId } : {}),
          items: active.map((row) => ({
            source_request_item_id: row.source_request_item_id || null,
            nomenclature_id: row.nomenclature_id,
            packing_id: row.packing_id,
            quantity: row.quantity.replace(',', '.'),
            unit_price: row.unit_price.replace(',', '.'),
            currency_id: row.currency_id,
            ...(row.delivery_days ? { delivery_days: Number(row.delivery_days) } : {}),
          })),
        },
        'POST',
      );
      onSaved();
    } catch (error) {
      if (error instanceof ApiError && error.field) {
        const index = Number(error.field.match(/items(?:\.|\[)([0-9]+)/)?.[1]);
        if (Number.isInteger(index) && active[index])
          setRowErrors((current) => ({ ...current, [active[index].key]: error.message }));
      }
    }
  }
  return (
    <Modal title="Новая квота поставщика" wide onClose={close}>
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="form-body quote-sheet-editor">
          <p className="muted">
            Заполните строки подряд и сохраните квоту одним действием. Цены из актуальной истории
            подставляются при выборе фасовки.
          </p>
          <ErrorBox
            error={operation.error || localError || currencies.error || requestItems.error}
          />
          <div className="form-grid quote-sheet-header">
            <div className="field">
              <label htmlFor="field-supplier_id">Поставщик</label>
              <DirectorySelect
                field={{
                  name: 'supplier_id',
                  label: 'Поставщик',
                  type: 'select',
                  source: '/counterparties?kind=supplier',
                  required: true,
                }}
                value={supplierId}
                onChange={(value) => {
                  const nextSupplier = String(value);
                  setSupplierId(nextSupplier);
                  setRfqId('');
                  const needsHistory = rows.filter((row) => row.historyHint || !row.unit_price);
                  setRows((current) =>
                    current.map((row) =>
                      row.historyHint
                        ? {
                            ...row,
                            unit_price: '',
                            currency_id: '',
                            delivery_days: '',
                            historyHint: undefined,
                          }
                        : row,
                    ),
                  );
                  needsHistory.forEach(
                    (row) =>
                      void findLatest(
                        row.key,
                        nextSupplier,
                        row.nomenclature_id,
                        row.packing_id,
                        row.historyHint ? '' : row.currency_id,
                      ),
                  );
                }}
              />
            </div>
            <label className="field">
              Запрос поставщику
              <select value={rfqId} onChange={(event) => setRfqId(event.target.value)}>
                <option value="">Без связи с запросом</option>
                {rfqs.data?.items
                  .filter(
                    (rfq) => !supplierId || !rfq.supplier_id || rfq.supplier_id === supplierId,
                  )
                  .map((rfq) => (
                    <option key={rfq.id} value={rfq.id}>
                      {String(rfq.number || rfq.request_number || date(rfq.created_at))}
                      {!rfq.supplier_id && ' · Общий запрос'}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <label className="field quote-item-search">
            Поиск позиции заявки
            <input
              value={itemSearch}
              onChange={(event) => setItemSearch(event.target.value)}
              placeholder="Название, артикул или описание"
            />
          </label>
          <div className="quote-sheet-table-scroll">
            <table className="quote-sheet-table">
              <thead>
                <tr>
                  <th scope="col">№</th>
                  <th scope="col">Позиция заявки</th>
                  <th scope="col">Номенклатура</th>
                  <th scope="col">Фасовка</th>
                  <th scope="col">Кол-во</th>
                  <th scope="col">Цена</th>
                  <th scope="col">Валюта</th>
                  <th scope="col">Срок, дней</th>
                  <th scope="col">
                    <span className="sr-only">Удалить</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => {
                  const sourceItem = requestItemOptions.find(
                    (item) => item.id === row.source_request_item_id,
                  );
                  return (
                    <tr key={row.key} className={rowErrors[row.key] ? 'quote-row-error' : ''}>
                      <td>{index + 1}</td>
                      <td>
                        <select
                          aria-label={`Позиция заявки, строка ${index + 1}`}
                          value={row.source_request_item_id}
                          onChange={(event) => {
                            const item = requestItemOptions.find(
                              (entry) => entry.id === event.target.value,
                            );
                            if (item) setKnownItems((current) => ({ ...current, [item.id]: item }));
                            const nomenclatureId = String(item?.nomenclature_id || '');
                            const packingId = String(item?.packing_id || '');
                            patchRow(row.key, {
                              source_request_item_id: event.target.value,
                              nomenclature_id: nomenclatureId,
                              packing_id: packingId,
                              quantity: String(item?.quantity || row.quantity),
                              unit_price: '',
                              currency_id: '',
                            });
                            if (item && (!nomenclatureId || !packingId || item.unit !== 'pcs'))
                              setRowErrors((current) => ({
                                ...current,
                                [row.key]:
                                  'Сначала укажите номенклатуру и фасовку у позиции заявки.',
                              }));
                            void findLatest(row.key, supplierId, nomenclatureId, packingId);
                          }}
                        >
                          <option value="">Отдельное предложение</option>
                          {requestItemOptions.map((item) => (
                            <option key={item.id} value={item.id}>
                              {String(item.nomenclature_name || item.description)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>{String(sourceItem?.nomenclature_name || '—')}</td>
                      <td>{String(sourceItem?.packing_name || '—')}</td>
                      <td>
                        <input
                          aria-label={`Количество, строка ${index + 1}`}
                          type="number"
                          min="1"
                          step="1"
                          value={row.quantity}
                          onChange={(event) => patchRow(row.key, { quantity: event.target.value })}
                        />
                      </td>
                      <td>
                        <input
                          aria-label={`Цена, строка ${index + 1}`}
                          inputMode="decimal"
                          value={row.unit_price}
                          onChange={(event) =>
                            patchRow(row.key, {
                              unit_price: event.target.value.replace(',', '.'),
                              historyHint: undefined,
                            })
                          }
                        />
                        {(rowErrors[row.key] || row.historyHint) && (
                          <small className="quote-row-message">
                            {rowErrors[row.key] || row.historyHint}
                          </small>
                        )}
                      </td>
                      <td>
                        <select
                          aria-label={`Валюта, строка ${index + 1}`}
                          value={row.currency_id}
                          onChange={(event) => {
                            patchRow(row.key, { currency_id: event.target.value });
                            if (!row.unit_price)
                              void findLatest(
                                row.key,
                                supplierId,
                                row.nomenclature_id,
                                row.packing_id,
                                event.target.value,
                              );
                          }}
                        >
                          <option value="">Валюта</option>
                          {currencies.data?.items.map((currency) => (
                            <option key={currency.id} value={currency.id}>
                              {String(currency.code || currency.name)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          aria-label={`Срок поставки, строка ${index + 1}`}
                          type="number"
                          min="0"
                          value={row.delivery_days}
                          onChange={(event) =>
                            patchRow(row.key, { delivery_days: event.target.value })
                          }
                        />
                      </td>
                      <td>
                        <button
                          type="button"
                          className="icon-button"
                          aria-label={`Удалить строку ${index + 1}`}
                          disabled={rows.length === 1}
                          onClick={() =>
                            setRows((current) => current.filter((entry) => entry.key !== row.key))
                          }
                        >
                          <Trash2 size={15} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Button
            type="button"
            variant="secondary"
            disabled={rows.length >= 10000}
            onClick={() => setRows((current) => [...current, blankQuoteRow(nextKey.current++)])}
          >
            <Plus size={15} /> Добавить строку
          </Button>
          <p className="quote-sheet-count">
            Строк: {rows.length}. Количество строк не связано с исходным запросом; до 10 000 позиций
            в одной квоте.
          </p>
        </div>
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={close}>
            Отмена
          </Button>
          <Button type="submit" busy={operation.busy}>
            Сохранить квоту
          </Button>
        </div>
      </form>
    </Modal>
  );
}
export function RequestQuotes({
  requestId,
  onCalculate,
}: {
  requestId: string;
  onCalculate: (quoteItemIds: string[]) => void;
}) {
  const auth = useAuth();
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState<Entity>();
  const [creating, setCreating] = useState(false);
  const [revision, setRevision] = useState(0);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const deleteCommand = useCommand();
  async function deleteQuote(whole: boolean) {
    if (
      !selected ||
      !window.confirm(
        whole
          ? `Удалить квоту ${String(selected.quote_number)} поставщика ${String(selected.supplier_name)} целиком?`
          : 'Удалить эту ошибочную позицию квоты?',
      )
    )
      return;
    try {
      const result = await deleteCommand.run<{ deleted_ids: string[] }>(
        whole
          ? `/quote-sheets/${String(selected.quote_sheet_id)}/delete`
          : `/quote-items/${selected.id}/delete`,
        { version: whole ? selected.quote_sheet_version : selected.version },
        'POST',
        true,
      );
      if (result) {
        setSelectedIds(
          (current) => new Set([...current].filter((id) => !result.deleted_ids.includes(id))),
        );
        setSelected(undefined);
        setRevision((value) => value + 1);
      }
    } catch {
      /* ErrorBox displays the failure. */
    }
  }
  return (
    <>
      <div className="tab-actions">
        <Button variant="secondary" onClick={() => setCreating(true)}>
          <Plus size={16} /> Новая квота
        </Button>
        {auth.can('quotes.write') && auth.can('finance.purchase.read') && (
          <Button variant="secondary" onClick={() => setImporting(true)}>
            <Upload size={16} /> Загрузить квоты XLSX
          </Button>
        )}
        <span className="selection-count">Выбрано позиций: {selectedIds.size}</span>
        <Button
          disabled={!selectedIds.size || selectedIds.size > 100}
          title={
            !selectedIds.size
              ? 'Сначала выберите позиции квот'
              : selectedIds.size > 100
                ? 'Один расчёт содержит до 100 позиций'
                : undefined
          }
          onClick={() => onCalculate([...selectedIds])}
        >
          Сформировать расчёт
        </Button>
        {selectedIds.size > 100 && (
          <small className="field-error">Один расчёт содержит до 100 позиций.</small>
        )}
      </div>
      <Collection
        title="Позиции квот"
        description="Отметьте нужные строки разных поставщиков и валют. Выбор сохраняется при перелистывании страниц."
        endpoint={`/requests/${requestId}/quote-items`}
        refreshKey={revision}
        pageSize={100}
        selection={{
          selectedIds,
          onToggle: (id) =>
            setSelectedIds((current) => {
              const next = new Set(current);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            }),
          onSelectPage: (ids, checked) =>
            setSelectedIds((current) => {
              const next = new Set(current);
              ids.forEach((id) => {
                if (checked) next.add(id);
                else next.delete(id);
              });
              return next;
            }),
        }}
        onSelect={setSelected}
        columns={[
          { key: 'quote_number', label: 'Квота' },
          { key: 'nomenclature_name', label: 'Номенклатура', render: quoteName },
          { key: 'article', label: 'Артикул' },
          { key: 'manufacturer', label: 'Производитель' },
          { key: 'packing_name', label: 'Фасовка' },
          { key: 'quantity', label: 'Количество', render: (row) => decimal(row.quantity) },
          { key: 'supplier_name', label: 'Поставщик' },
          { key: 'unit_price', label: 'Цена', render: (row) => decimal(row.unit_price) },
          { key: 'currency_code', label: 'Валюта' },
          { key: 'delivery_days', label: 'Срок, дней' },
        ]}
      />
      {creating && (
        <QuoteSheetEditor
          requestId={requestId}
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            setRevision((value) => value + 1);
          }}
        />
      )}
      {importing && (
        <TableImportDialog
          requestId={requestId}
          kind="quotes"
          onClose={() => setImporting(false)}
          onSuccess={() => setRevision((value) => value + 1)}
        />
      )}
      {selected && (
        <Modal
          title={`Квота ${String(selected.quote_number)} · ${String(selected.supplier_name)}`}
          onClose={() => setSelected(undefined)}
        >
          <div className="form-body">
            <ErrorBox error={deleteCommand.error} />
            <DetailPairs
              values={{
                Номенклатура: quoteName(selected),
                Артикул: selected.article,
                Производитель: selected.manufacturer,
                Фасовка: selected.packing_name,
                Поставщик: selected.supplier_name,
                Количество: decimal(selected.quantity),
                Цена: `${decimal(selected.unit_price)} ${String(selected.currency_code || '')}`,
                'Срок поставки': selected.delivery_days,
                'Действует до': date(selected.valid_until),
              }}
            />
            {auth.can('quotes.write') && (
              <div className="inline-actions">
                <Button
                  variant="secondary"
                  busy={deleteCommand.busy}
                  onClick={() => void deleteQuote(false)}
                >
                  <Trash2 size={16} /> Удалить позицию
                </Button>
                <Button
                  variant="secondary"
                  busy={deleteCommand.busy}
                  onClick={() => void deleteQuote(true)}
                >
                  Удалить квоту целиком
                </Button>
              </div>
            )}
          </div>
        </Modal>
      )}
    </>
  );
}
