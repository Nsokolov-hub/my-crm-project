import { Archive, Download, Paperclip } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../app/Auth';
import { api, download } from '../lib/api';
import { date } from '../lib/format';
import { useApi, useCommand } from '../lib/hooks';
import { createRequestKey } from '../lib/requestKey';
import type { Entity, Page } from '../lib/types';
import { Badge, Button, DataTable, ErrorBox, Loading, Pagination, Section } from './ui';

const pageSize = 25;

const categories: Record<string, string> = {
  founding: 'Учредительные документы',
  contract: 'Договоры',
  other: 'Прочие документы',
};

export function CounterpartyDocuments({ counterpartyId }: { counterpartyId: string }) {
  const auth = useAuth();
  const [page, setPage] = useState(1);
  const documents = useApi<Page>(
    `/counterparties/${counterpartyId}/documents?page=${page}&page_size=${pageSize}`,
  );
  const [category, setCategory] = useState('founding');
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const pending = useRef<{ signature: string; key: string } | undefined>(undefined);
  const uploading = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const archive = useCommand();
  const canWrite = auth.can('clients.write');
  const canUpload = canWrite && auth.can('files.upload');

  useEffect(() => {
    if (
      !documents.data?.items.some((row) => ['pending', 'quarantined'].includes(String(row.status)))
    )
      return;
    const timer = setInterval(documents.refresh, 5000);
    return () => clearInterval(timer);
  }, [documents.data, documents.refresh]);

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file || uploading.current) return;
    uploading.current = true;
    setBusy(true);
    setError(undefined);
    try {
      const signature = [counterpartyId, category, file.name, file.size, file.lastModified].join(
        ':',
      );
      if (pending.current?.signature !== signature)
        pending.current = { signature, key: createRequestKey() };
      const form = new FormData();
      form.set('file', file);
      form.set('category', category);
      await api<Entity>(`/counterparties/${counterpartyId}/documents`, {
        method: 'POST',
        body: form,
        key: pending.current.key,
      });
      pending.current = undefined;
      setFile(undefined);
      if (fileInput.current) fileInput.current.value = '';
      setPage(1);
      documents.refresh();
    } catch (cause) {
      setError(cause);
    } finally {
      uploading.current = false;
      setBusy(false);
    }
  }

  async function remove(document: Entity) {
    try {
      await archive.run(
        `/counterparty-documents/${document.id}`,
        {
          version: document.version,
          archived: true,
        },
        'PATCH',
      );
      if (page > 1 && documents.data?.items.length === 1) setPage(page - 1);
      documents.refresh();
    } catch {
      /* ErrorBox keeps the failed operation visible. */
    }
  }

  return (
    <Section
      title="Документы контрагента"
      description="Учредительные документы, договоры и другие файлы компании. Скачивание доступно после проверки файла."
    >
      <ErrorBox error={error || archive.error || documents.error} retry={documents.refresh} />
      {canUpload && (
        <form onSubmit={(event) => void upload(event)} className="form-grid">
          <label className="field">
            Категория документа
            <select
              value={category}
              disabled={busy}
              onChange={(event) => setCategory(event.target.value)}
            >
              {Object.entries(categories).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Файл документа
            <input
              ref={fileInput}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.gif,.txt,.csv,.xlsx,.docx"
              disabled={busy}
              onChange={(event) => setFile(event.target.files?.[0])}
            />
          </label>
          <div className="inline-actions wide">
            <Button type="submit" busy={busy} disabled={!file}>
              <Paperclip size={16} /> Загрузить документ
            </Button>
          </div>
        </form>
      )}
      {documents.loading ? (
        <Loading />
      ) : (
        <DataTable
          rows={documents.data?.items || []}
          columns={[
            { key: 'name', label: 'Документ' },
            {
              key: 'category',
              label: 'Категория',
              render: (row) => categories[String(row.category)] || String(row.category),
            },
            { key: 'created_at', label: 'Добавлен', render: (row) => date(row.created_at) },
            {
              key: 'status',
              label: 'Проверка',
              render: (row) =>
                ['pending', 'quarantined'].includes(String(row.status)) ? (
                  'Проверяется…'
                ) : row.status === 'scan_failed' ? (
                  'Не удалось проверить файл'
                ) : (
                  <Badge value={row.status} />
                ),
            },
            {
              key: 'actions',
              label: 'Действия',
              sortable: false,
              render: (row) => (
                <div className="inline-actions compact">
                  {auth.can('exports.download') && (
                    <Button
                      variant="ghost"
                      disabled={row.status !== 'clean'}
                      onClick={() =>
                        void download(
                          `/counterparty-documents/${row.id}/download`,
                          String(row.name),
                        ).catch(setError)
                      }
                    >
                      <Download size={15} /> Скачать
                    </Button>
                  )}
                  {canWrite && (
                    <Button
                      variant="ghost"
                      disabled={archive.busy}
                      onClick={() => void remove(row)}
                    >
                      <Archive size={15} /> В архив
                    </Button>
                  )}
                </div>
              ),
            },
          ]}
        />
      )}
      <Pagination
        page={page}
        pageSize={pageSize}
        total={documents.data?.total || 0}
        onChange={setPage}
      />
    </Section>
  );
}
