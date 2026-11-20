import { useEffect, useState } from 'react';
import { Star } from 'lucide-react';
import { DirectorySelect } from './Form';
import { Button, DataTable, ErrorBox, Modal, Pagination } from './ui';
import { SupplierFavoriteButton } from './SupplierFavoriteButton';
import { api } from '../lib/api';
import { useApi, useCommand } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
export function SupplierMailEditor({
  requestId,
  itemIds,
  requestNumber,
  onClose,
  onSuccess,
}: {
  requestId: string;
  itemIds: string[];
  requestNumber?: string;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [suppliers, setSuppliers] = useState<Entity[]>([]);
  const [chosen, setChosen] = useState('');
  const [intro, setIntro] = useState('Dear Colleagues,\n\nPlease send us offer for:');
  const [subject, setSubject] = useState(`Request ${requestNumber || ''}`);
  const [cc, setCc] = useState('info@ogk-chem.ru');
  const [preview, setPreview] = useState<Entity[]>();
  const [error, setError] = useState<unknown>();
  const [adding, setAdding] = useState(false);
  const [favoritePage, setFavoritePage] = useState(1);
  const favoritePageSize = 12;
  const favorites = useApi<Page>(
    `/counterparties?kind=supplier&favorite=true&page=${favoritePage}&page_size=${favoritePageSize}`,
  );
  useEffect(() => {
    if (favorites.data?.total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(favorites.data.total / favoritePageSize));
    if (favoritePage > lastPage) setFavoritePage(lastPage);
  }, [favorites.data?.total, favoritePage]);
  const command = useCommand();
  const body = {
    supplier_ids: suppliers.map((s) => s.id),
    item_ids: itemIds,
    introduction: intro,
    cc: cc
      .split(/[;,\s]+/)
      .map((email) => email.trim())
      .filter(Boolean),
  };
  async function add(supplierId = chosen) {
    if (!supplierId || adding) return;
    setAdding(true);
    setError(undefined);
    try {
      const supplier = await api<Entity>(`/counterparties/${supplierId}`);
      if (
        supplier.archived ||
        supplier.merged_into_id ||
        (supplier.kind && !['supplier', 'both'].includes(String(supplier.kind)))
      )
        throw new Error('Выберите действующего поставщика из справочника.');
      setSuppliers((rows) => [...rows.filter((row) => row.id !== supplier.id), supplier]);
      setChosen((value) => (value === supplierId ? '' : value));
      setPreview(undefined);
    } catch (cause) {
      setError(cause);
    } finally {
      setAdding(false);
    }
  }
  async function showPreview() {
    try {
      const result = await command.run<{ items: Entity[] }>(
        `/requests/${requestId}/supplier-mail/preview`,
        body,
        'POST',
        true,
      );
      setPreview(result?.items);
    } catch {
      /* shown below */
    }
  }
  async function send() {
    try {
      await command.run(
        `/requests/${requestId}/supplier-mail/send`,
        { ...body, subject },
        'POST',
        true,
      );
      onSuccess();
    } catch {
      /* shown below */
    }
  }
  return (
    <Modal title="Письма поставщикам" wide onClose={onClose}>
      <div className="form-body">
        <ErrorBox error={error || command.error} />
        <p>
          Для каждого поставщика создаётся отдельное письмо с таблицей в тексте. Выбрано позиций:{' '}
          {itemIds.length}.
        </p>
        <div className="inline-actions">
          <DirectorySelect
            field={{
              name: 'mail_supplier',
              label: 'Поставщик',
              source: '/counterparties?kind=supplier',
            }}
            value={chosen}
            onChange={(v) => setChosen(String(v))}
          />
          <Button variant="secondary" disabled={!chosen} busy={adding} onClick={() => void add()}>
            Добавить получателя
          </Button>
        </div>
        <section className="supplier-favorites" aria-labelledby="mail-favorites-title">
          <h3 id="mail-favorites-title">Избранные поставщики</h3>
          <p>Ваш личный список. Нажмите на поставщика, чтобы добавить получателя.</p>
          {favorites.error ? (
            <ErrorBox error={favorites.error} retry={favorites.refresh} />
          ) : favorites.loading ? (
            <p role="status">Загрузка избранных поставщиков…</p>
          ) : favorites.data?.items.length ? (
            <div className="inline-actions">
              {favorites.data.items.map((supplier) => (
                <Button
                  key={supplier.id}
                  variant="secondary"
                  disabled={adding || suppliers.some((row) => row.id === supplier.id)}
                  onClick={() => void add(supplier.id)}
                >
                  <Star size={14} fill="currentColor" />
                  {String(supplier.name)}
                  {suppliers.some((row) => row.id === supplier.id) ? ' · добавлен' : ''}
                </Button>
              ))}
            </div>
          ) : (
            <p>Отметьте поставщика звёздочкой в его карточке или в списке получателей ниже.</p>
          )}
          {(favorites.data?.total || 0) > favoritePageSize && (
            <Pagination
              page={favoritePage}
              pageSize={favoritePageSize}
              total={favorites.data?.total || 0}
              onChange={setFavoritePage}
            />
          )}
        </section>
        <DataTable
          rows={suppliers}
          columns={[
            { key: 'name', label: 'Поставщик' },
            {
              key: 'is_favorite',
              label: 'Избранный',
              render: (row) => (
                <SupplierFavoriteButton
                  supplier={row}
                  onChange={(isFavorite) =>
                    setSuppliers((rows) =>
                      rows.map((supplier) =>
                        supplier.id === row.id
                          ? { ...supplier, is_favorite: isFavorite }
                          : supplier,
                      ),
                    )
                  }
                />
              ),
            },
            {
              key: 'email',
              label: 'Почта',
              render: (row) =>
                [
                  (row.details as Entity)?.rfq_email || row.email,
                  (row.details as Entity)?.rfq_email_manager,
                ]
                  .filter(Boolean)
                  .map(String)
                  .join(', ') || 'Укажите почту в карточке',
            },
            {
              key: 'remove',
              label: '',
              render: (row) => (
                <Button
                  variant="ghost"
                  onClick={() => {
                    setSuppliers((s) => s.filter((r) => r.id !== row.id));
                    setPreview(undefined);
                  }}
                >
                  Убрать
                </Button>
              ),
            },
          ]}
        />
        <label className="field">
          Копия
          <input
            value={cc}
            onChange={(e) => {
              setCc(e.target.value);
              setPreview(undefined);
            }}
            placeholder="info@ogk-chem.ru"
          />
          <small>Несколько адресов разделите запятой. Поле можно оставить пустым.</small>
        </label>
        <label className="field">
          Тема
          <input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={250} />
        </label>
        <label className="field">
          Текст перед таблицей
          <textarea
            rows={6}
            value={intro}
            onChange={(e) => {
              setIntro(e.target.value);
              setPreview(undefined);
            }}
            maxLength={4000}
          />
        </label>
        <Button
          disabled={!suppliers.length || !itemIds.length}
          busy={command.busy}
          onClick={() => void showPreview()}
        >
          Предпросмотр
        </Button>
        {preview && (
          <>
            <p>Получатели: {preview.map((row) => String(row.recipient)).join(', ')}</p>
            {body.cc.length > 0 && <p>Копия: {body.cc.join(', ')}</p>}
            <pre className="mail-preview">{String(preview[0]?.body || '')}</pre>
            <Button disabled={!subject.trim()} busy={command.busy} onClick={() => void send()}>
              Отправить отдельные письма ({preview.length})
            </Button>
          </>
        )}
      </div>
    </Modal>
  );
}
