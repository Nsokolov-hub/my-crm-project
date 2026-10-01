import { Download } from 'lucide-react';
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
  summary: { total: number; checked: number; create_nomenclature: number; errors: number };
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
        if (!busy && !confirm.busy) onClose();
      }}
    >
      <form className="form-body" id="table-import-form" onSubmit={(event) => void preview(event)}>
        <p>
          {kind === 'quotes'
            ? 'Поставщик определяется по внутреннему коду, номенклатура — по артикулу. Нули в необязательных характеристиках оставят их пустыми. Если фасовка не указана, используется имеющаяся единственная фасовка или 1 шт.'
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
            disabled={busy || confirm.busy}
            onChange={(event) => {
              setFile(event.target.files?.[0]);
              setBatch(undefined);
              setMapping({});
              setError(undefined);
              confirm.setError(undefined);
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
                        key: 'product_group_name',
                        label: 'Товарная группа',
                        render: (row: Entity) =>
                          String((row.data as Record<string, unknown>).product_group_name || '—'),
                      },
                    ]
                  : []),
                {
                  key: 'errors',
                  label: 'Ошибки',
                  render: (row) => (row.errors as string[]).join('; '),
                },
              ]}
            />
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
        <Button variant="secondary" disabled={busy || confirm.busy} onClick={onClose}>
          Закрыть
        </Button>
        {batch?.status !== 'completed' && (
          <Button
            type="submit"
            form="table-import-form"
            busy={busy}
            disabled={!file || confirm.busy}
          >
            Проверить файл
          </Button>
        )}
        {batch?.status === 'preview' && (
          <Button
            busy={confirm.busy}
            disabled={busy || mappingChanged || batch.summary.errors > 0 || !batch.summary.total}
            onClick={() => void apply()}
          >
            Записать позиции: {batch.summary.total}
          </Button>
        )}
      </div>
    </Modal>
  );
}
