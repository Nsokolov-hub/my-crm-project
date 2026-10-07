import { useState } from 'react';
import { DirectorySelect } from './Form';
import { Button, DataTable, ErrorBox, Modal } from './ui';
import { api } from '../lib/api';
import { useCommand } from '../lib/hooks';
import type { Entity } from '../lib/types';
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
  async function add() {
    try {
      const supplier = await api<Entity>(`/counterparties/${chosen}`);
      setSuppliers((rows) => [...rows.filter((row) => row.id !== supplier.id), supplier]);
      setChosen('');
      setPreview(undefined);
    } catch (cause) {
      setError(cause);
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
          <Button variant="secondary" disabled={!chosen} onClick={() => void add()}>
            Добавить получателя
          </Button>
        </div>
        <DataTable
          rows={suppliers}
          columns={[
            { key: 'name', label: 'Поставщик' },
            {
              key: 'email',
              label: 'Почта',
              render: (row) =>
                String(
                  (row.details as Entity)?.rfq_email || row.email || 'Укажите почту в карточке',
                ),
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
