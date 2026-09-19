import { Download, Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, download } from '../lib/api';
import { date } from '../lib/format';
import { useApi, useCommand } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
import { Badge, Button, DataTable, DetailPairs, ErrorBox, Loading, Modal, Pagination } from './ui';

const importMapping = {
  external_id: 'Внешний ID',
  name: 'Организация',
  country: 'Страна',
  tax_id: 'ИНН',
  contact: 'Контактное лицо',
  phone: 'Телефон',
  email: 'Электронная почта',
  source: 'Источник',
  comment: 'Комментарий',
  owner_id: 'Ответственный',
};
const pageSize = 100;
const statusLabels: Record<string, string> = {
  preview: 'Проверка перед импортом',
  queued: 'В очереди на импорт',
  completed: 'Импорт завершён',
  failed: 'Импорт не выполнен',
  create: 'Создать',
  update: 'Обновить',
  skip: 'Пропустить',
  conflict: 'Выберите решение',
  error: 'Ошибка в строке',
  created: 'Создано',
  updated: 'Обновлено',
};
type ImportRow = Entity & {
  row_number: number;
  data: Record<string, string>;
  errors: { field: string; message: string }[];
  candidate_ids: string[];
  match_id: string | null;
  action: string;
};
type ImportBatch = Entity & {
  source_name: string;
  status: string;
  summary: Record<string, number>;
  rows?: ImportRow[];
};
type Decision = {
  row_id: string;
  action: 'create' | 'update' | 'skip';
  match_id: string | null;
};
type DraftDecision = Decision & { originalAction: string };

function ImportStatus({ value }: { value: string }) {
  return (
    <Badge
      value={statusLabels[value] || value}
      tone={
        value === 'completed' ? 'green' : value === 'failed' || value === 'error' ? 'red' : 'purple'
      }
    />
  );
}

export function ImportDialog({
  open,
  onClose,
  onSuccess,
}: {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [view, setView] = useState<'upload' | 'batch' | 'history'>('upload');
  const [file, setFile] = useState<File>();
  const [mapping, setMapping] = useState<Record<string, string>>({ ...importMapping });
  const [selectedId, setSelectedId] = useState<string>();
  const [batch, setBatch] = useState<ImportBatch>();
  const [page, setPage] = useState(1);
  const [historyPage, setHistoryPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [drafts, setDrafts] = useState<Record<string, Record<string, DraftDecision>>>({});
  const [candidates, setCandidates] = useState<Record<string, Entity>>({});
  const notified = useRef(new Set<string>());
  const command = useCommand();
  const history = useApi<Page<ImportBatch>>(
    open && view === 'history' ? `/imports?page=${historyPage}&page_size=25` : null,
  );

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true);
    async function refresh() {
      try {
        const result = await api<ImportBatch>(
          `/imports/${selectedId}?page=${page}&page_size=${pageSize}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setBatch(result);
        setError(undefined);
        if (result.status === 'queued') timer = setTimeout(() => void refresh(), 2000);
      } catch (e) {
        if (!controller.signal.aborted) {
          setError(e);
          timer = setTimeout(() => void refresh(), 5000);
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selectedId, page, revision]);

  useEffect(() => {
    if (batch?.status === 'completed' && !notified.current.has(batch.id)) {
      notified.current.add(batch.id);
      onSuccess();
    }
  }, [batch, onSuccess]);

  useEffect(() => {
    if (!open || view !== 'batch' || !batch?.rows) return;
    const controller = new AbortController();
    const ids = [
      ...new Set(
        batch.rows.flatMap((row) => [
          ...row.candidate_ids,
          ...(row.match_id ? [row.match_id] : []),
        ]),
      ),
    ].filter((id) => !candidates[id]);
    if (!ids.length) return;
    async function loadCandidates() {
      const clients: Record<string, Entity> = {};
      for (let offset = 0; offset < ids.length && !controller.signal.aborted; offset += 6) {
        const group = ids.slice(offset, offset + 6);
        const results = await Promise.allSettled(group.map((id) =>
          api<Entity>(`/counterparties/${id}`, { signal: controller.signal }),
        ));
        results.forEach((result, index) => {
          const id = group[index];
          clients[id] = result.status === 'fulfilled' ? result.value : { id, name: `Карточка ${id}` };
        });
      }
      if (!controller.signal.aborted) setCandidates((current) => ({ ...current, ...clients }));
    }
    void loadCandidates();
    return () => controller.abort();
  }, [open, view, batch, candidates]);

  async function preview(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    if (!mapping.name.trim() && !mapping.contact.trim()) {
      setError(new Error('Сопоставьте колонку организации или контактного лица.'));
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const body = new FormData();
      body.set('file', file);
      body.set('mapping', JSON.stringify(mapping));
      const result = await api<ImportBatch>('/imports/preview', { method: 'POST', body });
      setBatch(result);
      setSelectedId(result.id);
      setPage(1);
      setView('batch');
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!batch || batch.status !== 'preview') return;
    setError(undefined);
    try {
      const decisions = Object.values(drafts[batch.id] || {}).map(
        ({ row_id, action, match_id }) => ({
          row_id,
          action,
          match_id,
        }),
      );
      const result = await command.run<ImportBatch>(`/imports/${batch.id}/confirm`, { decisions });
      if (result) {
        setBatch((current) => ({ ...result, rows: current?.rows }));
        setRevision((value) => value + 1);
      }
    } catch (e) {
      setError(e);
    }
  }

  function choose(row: ImportRow, value: string) {
    if (!batch) return;
    const action = value.startsWith('update:') ? 'update' : (value as Decision['action']);
    setDrafts((current) => {
      const choices = { ...current[batch.id] };
      if (!value) delete choices[row.id];
      else
        choices[row.id] = {
          row_id: row.id,
          action,
          match_id: action === 'update' ? value.slice('update:'.length) : null,
        originalAction: row.action,
        };
      return { ...current, [batch.id]: choices };
    });
  }

  function openBatch(item: ImportBatch) {
    setBatch(item);
    setSelectedId(item.id);
    setPage(1);
    setError(undefined);
    setView('batch');
    setRevision((value) => value + 1);
  }

  if (!open) return null;
  const decisions = batch ? drafts[batch.id] || {} : {};
  const summary = { ...batch?.summary };
  if (batch?.status === 'preview') Object.values(decisions).forEach((decision) => {
    summary[decision.originalAction] = (summary[decision.originalAction] || 0) - 1;
    summary[decision.action] = (summary[decision.action] || 0) + 1;
  });
  const unresolved = Math.max(0, summary.conflict || 0);
  const rows = batch?.rows || [];
  const total = batch?.summary.total || 0;
  const editable = batch?.status === 'preview' && !command.busy && !loading;
  return (
    <Modal title="Импорт клиентской базы" wide onClose={onClose}>
      <div className="form-body">
        <div className="tabs">
          <button
            className={view === 'upload' ? 'active' : ''}
            onClick={() => setView('upload')}
            disabled={busy || command.busy}
          >
            Загрузить файл
          </button>
          {batch && (
            <button
              className={view === 'batch' ? 'active' : ''}
              onClick={() => setView('batch')}
              disabled={busy || command.busy}
            >
              Текущий импорт
            </button>
          )}
          <button
            className={view === 'history' ? 'active' : ''}
            onClick={() => setView('history')}
            disabled={busy || command.busy}
          >
            История импортов
          </button>
        </div>
        <ErrorBox error={error} />
        {view === 'upload' && (
          <>
            <p className="muted">
              Загрузите XLSX, сопоставьте столбцы и проверьте совпадения. Импорт начнётся после
              подтверждения.
            </p>
            <Button
              variant="secondary"
              onClick={() =>
                void download('/imports/template.xlsx', 'Шаблон_клиентов.xlsx').catch(setError)
              }
            >
              <Download size={16} /> Скачать шаблон
            </Button>
            <form id="import-form" onSubmit={(e) => void preview(e)}>
              <label className="upload-area">
                <Upload size={28} />
                <strong>{file?.name || 'Выберите файл XLSX'}</strong>
                <span>До 10 000 строк. Замените формулы значениями перед загрузкой.</span>
                <input
                  type="file"
                  accept=".xlsx"
                  required
                  disabled={busy}
                  onChange={(e) => setFile(e.target.files?.[0])}
                />
              </label>
              <p className="muted">
                Укажите точные заголовки из первой строки файла. Оставьте поле пустым, чтобы не
                импортировать столбец.
              </p>
              <div className="form-grid">
                {Object.entries(importMapping).map(([key, title]) => (
                  <label className="field" key={key}>
                    {title}
                    <input
                      value={mapping[key]}
                      disabled={busy}
                      onChange={(e) =>
                        setMapping((current) => ({ ...current, [key]: e.target.value }))
                      }
                    />
                    {key === 'owner_id' && (
                      <small>
                        Значения столбца — ID активных сотрудников. Без столбца новые карточки
                        назначаются вам.
                      </small>
                    )}
                  </label>
                ))}
              </div>
            </form>
          </>
        )}
        {view === 'history' && (
          <>
            <ErrorBox error={history.error} retry={history.refresh} />
            <Button variant="secondary" onClick={history.refresh}>
              Обновить историю
            </Button>
            {history.loading ? (
              <Loading />
            ) : (
              <>
                <DataTable<ImportBatch>
                  rows={history.data?.items || []}
                  onRow={openBatch}
                  columns={[
                    { key: 'source_name', label: 'Файл' },
                    {
                      key: 'created_at',
                      label: 'Загружен',
                      render: (row) => date(row.created_at, true),
                    },
                    {
                      key: 'status',
                      label: 'Статус',
                      render: (row) => <ImportStatus value={row.status} />,
                    },
                    {
                      key: 'summary',
                      label: 'Всего строк',
                      render: (row) => row.summary.total || 0,
                    },
                  ]}
                />
                <Pagination
                  page={historyPage}
                  total={history.data?.total || 0}
                  onChange={setHistoryPage}
                />
              </>
            )}
          </>
        )}
        {view === 'batch' && batch && (
          <>
            <DetailPairs
              values={{
                Файл: batch.source_name,
                Статус: statusLabels[batch.status] || batch.status,
                'Всего строк': total,
              'К созданию': summary.create || 0,
              'К обновлению': summary.update || 0,
                'Требуют решения':
                  batch.status === 'preview' ? unresolved : batch.summary.conflict || 0,
                Ошибки: batch.summary.error || 0,
              Пропущено: summary.skip || 0,
                Создано: batch.summary.created || 0,
                Обновлено: batch.summary.updated || 0,
              }}
            />
            {batch.status === 'queued' && (
              <p role="status">
                Импорт выполняется в фоне. Статус обновляется автоматически. Окно можно закрыть и
                открыть снова через историю импортов.
              </p>
            )}
            {batch.status === 'completed' && (
              <p role="status">Импорт завершён. База контрагентов обновлена.</p>
            )}
            {batch.status === 'failed' && (
              <p role="alert">
                Не удалось завершить импорт. Администратор может проверить фоновую операцию и
                повторить её.
              </p>
            )}
            {batch.status === 'preview' && (batch.summary.error || 0) > 0 && (
              <p className="muted">
                Строки с ошибками не будут импортированы. Скачайте файл ошибок, исправьте исходный
                XLSX и загрузите его повторно.
              </p>
            )}
            {batch.status === 'preview' && (
              <p className="muted">
                Решения сохраняются при переходе между страницами и закрытии этого окна.
                Неразрешённых совпадений: {unresolved}.
              </p>
            )}
            {loading ? (
              <Loading />
            ) : (
              <DataTable<ImportRow>
                rows={rows}
                columns={[
                  { key: 'row_number', label: 'Строка' },
                  {
                    key: 'data',
                    label: 'Данные',
                    render: (row) => (
                      <span className="entity-cell">
                        <span>
                          <strong>{row.data.name || row.data.contact || 'Без названия'}</strong>
                          <small>
                            {[row.data.contact, row.data.phone, row.data.email, row.data.tax_id]
                              .filter(Boolean)
                              .join(' · ')}
                          </small>
                        </span>
                      </span>
                    ),
                  },
                  {
                    key: 'errors',
                    label: 'Ошибки',
                    render: (row) =>
                      row.errors.length ? row.errors.map((item) => item.message).join('; ') : '—',
                  },
                  {
                    key: 'action',
                    label: 'Решение',
                    render: (row) => {
                      if (!editable || row.errors.length)
                        return <ImportStatus value={row.action} />;
                      const decision = decisions[row.id];
                      const action = String(decision?.action || row.action);
                      const matchId = decision?.match_id || row.match_id;
                      const value =
                        action === 'update'
                          ? `update:${matchId}`
                          : action === 'conflict'
                            ? ''
                            : action;
                      const ids = [
                        ...new Set([...row.candidate_ids, ...(row.match_id ? [row.match_id] : [])]),
                      ];
                      return (
                        <select
                          aria-label={`Решение строки ${row.row_number}`}
                          value={value}
                          onChange={(e) => choose(row, e.target.value)}
                        >
                          {row.action === 'conflict' && <option value="">Выберите решение</option>}
                          <option value="create">Создать отдельно</option>
                          <option value="skip">Пропустить</option>
                          {ids.map((id) => (
                            <option key={id} value={`update:${id}`}>
                              Обновить: {candidates[id]?.name ? String(candidates[id].name) : id}
                              {candidates[id]?.tax_id ? ` · ИНН ${candidates[id].tax_id}` : ''}
                            </option>
                          ))}
                        </select>
                      );
                    },
                  },
                ]}
              />
            )}
            <Pagination page={page} total={total} pageSize={pageSize} onChange={(next) => { if (!command.busy) setPage(next); }} />
            <div className="inline-actions">
              <Button
                variant="secondary"
                onClick={() => setRevision((value) => value + 1)}
                disabled={loading || command.busy}
              >
                Обновить статус
              </Button>
              <Button
                variant="secondary"
                onClick={() =>
                  void download(`/imports/${batch.id}/errors.xlsx`, 'Ошибки_импорта.xlsx').catch(
                    setError,
                  )
                }
              >
                <Download size={16} /> Файл ошибок
              </Button>
            </div>
          </>
        )}
      </div>
      <div className="modal-footer">
        <Button variant="secondary" onClick={onClose}>
          Закрыть
        </Button>
        {view === 'upload' && (
          <Button form="import-form" type="submit" busy={busy}>
            Проверить файл
          </Button>
        )}
        {view === 'batch' && batch?.status === 'preview' && (
          <Button
            busy={command.busy}
            disabled={loading || unresolved > 0 || total === 0}
            onClick={() => void confirm()}
          >
            Подтвердить импорт
          </Button>
        )}
      </div>
    </Modal>
  );
}
