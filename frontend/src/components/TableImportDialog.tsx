import { Download, Trash2 } from 'lucide-react';
import { useRef, useState } from 'react';
import { api, download } from '../lib/api';
import { useCommand } from '../lib/hooks';
import type { Entity } from '../lib/types';
import { Button, DataTable, ErrorBox, Modal } from './ui';

type Batch = Entity & {
  columns: string[];
  mapping: Record<string, string>;
  rows: (Entity & {
    row_number: number;
    action: string;
    errors: string[];
    data: Record<string, unknown>;
  })[];
  summary: {
    total: number;
    checked: number;
    create_nomenclature: number;
    errors: number;
    request_positions?: number;
    missing_positions?: number[];
  };
  status: string;
  result: { imported?: number; nomenclatures_created?: number };
};
const quoteFields: Record<string, string> = {
  source_row: 'Позиция заявки',
  supplier_code: 'Код поставщика',
  article: 'Артикул',
  name: 'Наименование',
  manufacturer: 'Производитель',
  cas: 'CAS',
  linear_formula: 'Линейная формула',
  purity: 'Чистота',
  packing_value: 'Фасовка',
  packing_unit: 'Единица фасовки',
  quantity: 'Количество',
  unit_price: 'Цена',
  currency: 'Валюта',
  delivery_days: 'Срок поставки, дней',
  product_group: 'Товарная группа',
  storage_temperature: 'Температурный режим',
  transport_hazard: 'UN (опасность)',
};
const itemFields: Record<string, string> = {
  description: 'Наименование',
  article: 'Артикул',
  quantity: 'Количество',
  unit: 'Единица',
  packaging: 'Фасовка',
  cas: 'CAS',
};
export function TableImportDialog({
  requestId,
  kind,
  onClose,
  onSuccess,
}: {
  requestId: string;
  kind: 'items' | 'quotes';
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [file, setFile] = useState<File>();
  const [batch, setBatch] = useState<Batch>();
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState<unknown>();
  const confirm = useCommand();
  // Business remark of 13 November 2026: rows that should not get into the request are removed before writing.
  const removing = useCommand();
  const transport = useCommand();
  const [transportDrafts, setTransportDrafts] = useState<
    Record<number, { storage_temperature: string; transport_hazard: string }>
  >({});
  function transportValues(row: Entity) {
    const data = row.data as Record<string, unknown>;
    return (
      transportDrafts[Number(row.row_number)] || {
        storage_temperature: String(data.storage_temperature || ''),
        transport_hazard: String(data.transport_hazard || ''),
      }
    );
  }
  function editTransport(
    row: Entity,
    key: 'storage_temperature' | 'transport_hazard',
    value: string,
  ) {
    const number = Number(row.row_number);
    const data = row.data as Record<string, unknown>;
    setTransportDrafts((current) => ({
      ...current,
      [number]: {
        ...(current[number] || {
          storage_temperature: String(data.storage_temperature || ''),
          transport_hazard: String(data.transport_hazard || ''),
        }),
        [key]: value,
      },
    }));
  }
  async function saveTransport(row: Entity) {
    if (!batch) return;
    try {
      const result = await transport.run<Batch>(
        `/requests/${requestId}/table-imports/${batch.id}/transport`,
        {
          version: batch.version,
          row_number: Number(row.row_number),
          ...transportValues(row),
        },
      );
      if (result) {
        setBatch(result);
        setTransportDrafts((current) => {
          const next = { ...current };
          delete next[Number(row.row_number)];
          return next;
        });
      }
    } catch {
      /* displayed below */
    }
  }
  async function removeRow(rowNumber: number) {
    if (!batch) return;
    try {
      const result = await removing.run<Batch>(
        `/requests/${requestId}/table-imports/${batch.id}/remove-rows`,
        { row_numbers: [rowNumber] },
      );
      if (result) {
        setBatch(result);
        setTransportDrafts((current) => {
          const next = { ...current };
          delete next[rowNumber];
          return next;
        });
      }
    } catch {
      /* displayed below */
    }
  }
  const [mappingChanged, setMappingChanged] = useState(false);
  async function preview(event: React.FormEvent) {
    event.preventDefault();
    if (!file || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(undefined);
    try {
      const body = new FormData();
      body.append('file', file);
      body.append('kind', kind);
      body.append('mapping', JSON.stringify(mapping));
      const result = await api<Batch>(`/requests/${requestId}/table-imports/preview`, {
        method: 'POST',
        body,
      });
      setBatch(result);
      setMapping(result.mapping);
      setMappingChanged(false);
      setTransportDrafts({});
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
      pending.current = false;
    }
  }
  async function apply() {
    if (!batch) return;
    try {
      const result = await confirm.run<Batch>(
        `/requests/${requestId}/table-imports/${batch.id}/confirm`,
        {},
      );
      if (result) {
        setBatch(result);
        onSuccess();
      }
    } catch {
      /* displayed below */
    }
  }
  return (
    <Modal
      title={kind === 'quotes' ? 'Загрузить квоты из Excel' : 'Загрузить таблицу заявки'}
      wide
      onClose={() => {
        if (!busy && !confirm.busy && !transport.busy) onClose();
      }}
    >
      <form className="form-body" id="table-import-form" onSubmit={(event) => void preview(event)}>
        <p>
          {kind === 'quotes'
            ? 'Поставщик определяется по внутреннему коду, номенклатура — по артикулу. Нули в CAS, формуле, чистоте и производителе оставят эти поля пустыми. Если фасовка не указана, используется имеющаяся единственная фасовка или 1 шт.'
            : 'Загрузите XLSX или текст UTF-8 с колонками через табуляцию (TSV). Все исходные столбцы сохранятся и попадут в Excel запроса поставщику.'}
        </p>
        {kind === 'quotes' && (
          <p>
            Для новых товаров укажите название или код действующей товарной группы. Если поле пустое
            или равно 0, используется группа исходной позиции либо «Прочее». Существующая
            номенклатура сохраняет свою группу.
          </p>
        )}
        {kind === 'quotes' && (
          <Button
            type="button"
            variant="secondary"
            onClick={() =>
              void download(
                `/requests/${requestId}/quote-import/template.xlsx`,
                'Шаблон_квот.xlsx',
              ).catch(setError)
            }
          >
            <Download size={16} /> Шаблон квот с позициями заявки
          </Button>
        )}
        <label className="field">
          Файл таблицы
          <input
            type="file"
            accept={kind === 'quotes' ? '.xlsx' : '.xlsx,.txt,.tsv,.csv'}
            required
            disabled={busy || confirm.busy || transport.busy}
            onChange={(event) => {
              setFile(event.target.files?.[0]);
              setBatch(undefined);
              setMapping({});
              setError(undefined);
              confirm.setError(undefined);
              setTransportDrafts({});
            }}
          />
        </label>
        <ErrorBox error={error || confirm.error} />
        {batch && (
          <>
            <div className="import-counts" role="status">
              <span>
                Загружено: <strong>{batch.summary.total}</strong>
              </span>
              <span>
                Проверено: <strong>{batch.summary.checked}</strong>
              </span>
              {kind === 'quotes' && (
                <span>
                  Создать номенклатуру: <strong>{batch.summary.create_nomenclature}</strong>
                </span>
              )}
              <span>
                Ошибки: <strong>{batch.summary.errors}</strong>
              </span>
              {batch.status === 'completed' && (
                <>
                  <span>
                    Записано: <strong>{batch.result.imported}</strong>
                  </span>
                  {kind === 'quotes' && (
                    <span>
                      Номенклатур создано: <strong>{batch.result.nomenclatures_created}</strong>
                    </span>
                  )}
                </>
              )}
            </div>
            {kind === 'quotes' && Boolean(batch.summary.missing_positions?.length) && (
              <p className="info-note" role="note">
                Строк в файле: {batch.summary.total}. В заявке позиций:{' '}
                {batch.summary.request_positions}. Без строки в файле остались позиции заявки:{' '}
                {batch.summary.missing_positions?.join(', ')}.
              </p>
            )}
            {kind === 'quotes' && batch.status === 'preview' && (
              <p className="info-note" role="note">
                Для товаров, кроме колонок, обязательны температурный режим и UN (опасность).
                Известные значения подставлены из номенклатуры. Недостающее можно заполнить в строке
                ниже и нажать «Сохранить параметры». Данные сохранятся в номенклатуре при записи
                квот; изменение справочника требует права «Изменение каталога».
              </p>
            )}
            {Object.keys(transportDrafts).length > 0 && (
              <p className="info-note" role="status">
                Сохраните параметры изменённых строк перед записью позиций.
              </p>
            )}
            {batch.status === 'preview' && (
              <details>
                <summary>Сопоставление столбцов</summary>
                <div className="form-grid">
                  {Object.entries(kind === 'quotes' ? quoteFields : itemFields).map(
                    ([key, title]) => (
                      <label key={key} className="field">
                        {title}
                        <select
                          value={mapping[key] || ''}
                          onChange={(event) => {
                            setMapping((current) => ({ ...current, [key]: event.target.value }));
                            setMappingChanged(true);
                          }}
                        >
                          <option value="">Не указан</option>
                          {batch.columns.map((column) => (
                            <option key={column} value={column}>
                              {column}
                            </option>
                          ))}
                        </select>
                      </label>
                    ),
                  )}
                </div>
              </details>
            )}
            <DataTable
              rows={batch.rows.map((row) => ({ ...row, id: String(row.row_number) }))}
              columns={[
                { key: 'row_number', label: 'Строка' },
                {
                  key: 'name',
                  label: 'Позиция',
                  render: (row) =>
                    String(
                      (row.data as Record<string, unknown>).name ||
                        (row.data as Record<string, unknown>).description ||
                        (row.data as Record<string, unknown>).article ||
                        '',
                    ),
                },
                {
                  key: 'action',
                  label: 'Результат',
                  render: (row) =>
                    ({
                      checked: 'Проверено',
                      create_nomenclature: 'Создать номенклатуру',
                      error: 'Ошибка',
                    })[String(row.action)] || String(row.action),
                },
                ...(kind === 'quotes'
                  ? [
                      {
                        key: 'supplier_name',
                        label: 'Поставщик',
                        render: (row: Entity) =>
                          String((row.data as Record<string, unknown>).supplier_name || '—'),
                      },
                      {
                        // Business remark of 21 October 2026: the row goes to the position of its product.
                        key: 'source_position',
                        label: 'Позиция заявки',
                        render: (row: Entity) => {
                          const data = row.data as Record<string, unknown>;
                          if (!data.source_position) return 'Новая позиция по квоте';
                          const place = `${String(data.source_position)}. ${String(data.source_description || '')}`;
                          return data.source_position_stated
                            ? `${place} (в файле: ${String(data.source_position_stated)} — по товару другая позиция)`
                            : place;
                        },
                      },
                      {
                        key: 'product_group_name',
                        label: 'Товарная группа',
                        render: (row: Entity) => {
                          const data = row.data as Record<string, unknown>;
                          const name = String(data.product_group_name || '—');
                          return data.product_group_changed
                            ? `${name} (в номенклатуре было: ${String(data.product_group_previous || 'без группы')})`
                            : name;
                        },
                      },
                      ...(['storage_temperature', 'transport_hazard'] as const).map((key) => ({
                        key,
                        label: quoteFields[key],
                        render: (row: Entity) => {
                          const data = row.data as Record<string, unknown>;
                          if (data.product_group_slug === 'columns')
                            return 'Не требуется для колонки';
                          if (batch.status !== 'preview') return String(data[key] || '—');
                          return (
                            <input
                              aria-label={`${quoteFields[key]}, строка ${String(row.row_number)}`}
                              value={transportValues(row)[key]}
                              maxLength={key === 'storage_temperature' ? 80 : 32}
                              placeholder={
                                key === 'storage_temperature'
                                  ? '2-8; -20; -70; 1-25'
                                  : 'UN, UN1234 или Не опасный'
                              }
                              disabled={
                                busy ||
                                mappingChanged ||
                                transport.busy ||
                                confirm.busy ||
                                removing.busy
                              }
                              onChange={(event) => editTransport(row, key, event.target.value)}
                            />
                          );
                        },
                      })),
                      ...(batch.status === 'preview'
                        ? [
                            {
                              key: 'save_transport',
                              label: '',
                              render: (row: Entity) =>
                                (row.data as Record<string, unknown>).product_group_slug ===
                                'columns' ? null : (
                                  <Button
                                    type="button"
                                    variant="secondary"
                                    disabled={
                                      busy ||
                                      mappingChanged ||
                                      transport.busy ||
                                      confirm.busy ||
                                      removing.busy
                                    }
                                    onClick={() => void saveTransport(row)}
                                  >
                                    Сохранить параметры
                                  </Button>
                                ),
                            },
                          ]
                        : []),
                    ]
                  : []),
                {
                  key: 'errors',
                  label: 'Ошибки',
                  render: (row) => (row.errors as string[]).join('; '),
                },
                ...(batch.status === 'preview' && batch.summary.total > 1
                  ? [
                      {
                        key: 'remove',
                        label: '',
                        render: (row: Entity) => (
                          <button
                            type="button"
                            className="icon-button"
                            aria-label={`Убрать строку ${String(row.row_number)}`}
                            title="Убрать строку: она не попадёт в заявку"
                            disabled={removing.busy || confirm.busy || transport.busy}
                            onClick={() => void removeRow(Number(row.row_number))}
                          >
                            <Trash2 size={15} />
                          </button>
                        ),
                      },
                    ]
                  : []),
              ]}
            />
            <ErrorBox error={removing.error} />
            <ErrorBox error={transport.error} />
            {batch.status === 'preview' && batch.summary.total > 1 && (
              <p className="muted">
                Лишнюю строку можно убрать корзиной справа — проверка пересчитается без неё. Если
                проверить файл заново, убранные строки вернутся, а правки параметров в этом окне
                сбросятся.
              </p>
            )}
            {batch.summary.total > 100 && (
              <p>Показаны первые 100 строк. Счётчики и проверка учитывают весь файл.</p>
            )}
            {batch.summary.errors > 0 && (
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  void download(
                    `/requests/${requestId}/table-imports/${batch.id}/errors.xlsx`,
                    'Ошибки_квот.xlsx',
                  ).catch(setError)
                }
              >
                Скачать все ошибки
              </Button>
            )}
          </>
        )}
      </form>
      <div className="modal-footer">
        <Button
          variant="secondary"
          disabled={busy || confirm.busy || transport.busy}
          onClick={onClose}
        >
          Закрыть
        </Button>
        {batch?.status !== 'completed' && (
          <Button
            type="submit"
            form="table-import-form"
            busy={busy}
            disabled={!file || confirm.busy || transport.busy || removing.busy}
          >
            Проверить файл
          </Button>
        )}
        {batch?.status === 'preview' && (
          <Button
            busy={confirm.busy}
            disabled={
              busy ||
              transport.busy ||
              mappingChanged ||
              Object.keys(transportDrafts).length > 0 ||
              batch.summary.errors > 0 ||
              !batch.summary.total
            }
            onClick={() => void apply()}
          >
            Записать позиции: {batch.summary.total}
          </Button>
        )}
      </div>
    </Modal>
  );
}
