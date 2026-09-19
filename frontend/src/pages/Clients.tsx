import { Pencil, Phone, Plus, Upload } from 'lucide-react';
import { useCallback, useState } from 'react';
import { Link } from 'react-router-dom';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { ImportDialog } from '../components/ImportDialog';
import { Badge, Button, DetailPairs, Modal, PageHeading } from '../components/ui';
import { callFields, clientFields, contactFields } from '../lib/fields';
import { date } from '../lib/format';
import type { Entity, Field } from '../lib/types';
export function Clients() {
  const [selected, setSelected] = useState<Entity>();
  const [form, setForm] = useState<'edit' | 'call'>();
  const [importing, setImporting] = useState(false);
  const [revision, setRevision] = useState(0);
  const refreshClients = useCallback(() => setRevision((value) => value + 1), []);
  return (
    <>
      <PageHeading
        title="Клиенты и поставщики"
        description="Контакты, договорённости и история отношений — в одной карточке."
        actions={
          <Button variant="secondary" onClick={() => setImporting(true)}>
            <Upload size={17} />
            Импорт XLSX
          </Button>
        }
      />
      <Collection
        title="База контрагентов"
        endpoint="/counterparties"
        fields={clientFields}
        createLabel="Добавить контрагента"
        refreshKey={revision}
        columns={[
          {
            key: 'name',
            label: 'Организация',
            render: (row) => (
              <span className="entity-cell">
                <span className="company-icon">{String(row.name).slice(0, 1)}</span>
                <span>
                  <strong>{String(row.name)}</strong>
                  <small>{String(row.tax_id || 'Реквизиты не указаны')}</small>
                </span>
              </span>
            ),
          },
          { key: 'kind', label: 'Тип', render: (row) => <Badge value={row.kind} /> },
          { key: 'country', label: 'Страна' },
          { key: 'phone', label: 'Телефон' },
          { key: 'email', label: 'Почта' },
          { key: 'created_at', label: 'Добавлен', render: (row) => date(row.created_at) },
        ]}
        onSelect={setSelected}
      />
      {selected && !form && (
        <Modal title={String(selected.name)} wide onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <div className="inline-actions">
              <Button variant="secondary" onClick={() => setForm('edit')}>
                <Pencil size={15} />
                Редактировать
              </Button>
              <Button onClick={() => setForm('call')}>
                <Phone size={15} />
                Записать звонок
              </Button>
              <Link
                className="button secondary"
                to={`/requests?client_id=${selected.id}`}
                onClick={() => setSelected(undefined)}
              >
                Заявки клиента
              </Link>
            </div>
            <DetailPairs
              values={{
                Тип: selected.kind,
                ИНН: selected.tax_id,
                Страна: selected.country,
                Телефон: selected.phone,
                Почта: selected.email,
              }}
            />
            <Collection
              title="Контакты"
              endpoint={`/counterparties/${selected.id}/contacts`}
              fields={contactFields}
              createLabel="Добавить контакт"
              columns={[
                { key: 'name', label: 'Имя' },
                { key: 'position', label: 'Должность' },
                { key: 'phone', label: 'Телефон' },
                { key: 'email', label: 'Почта' },
              ]}
            />
            <Collection
              title="История звонков"
              endpoint={`/calls?client_id=${selected.id}`}
              columns={[
                { key: 'created_at', label: 'Дата', render: (r) => date(r.created_at, true) },
                { key: 'result', label: 'Результат', render: (r) => <Badge value={r.result} /> },
                { key: 'comment', label: 'Комментарий' },
                {
                  key: 'next_at',
                  label: 'Следующее действие',
                  render: (r) => date(r.next_at, true),
                },
              ]}
            />
          </div>
        </Modal>
      )}
      {selected && form && (
        <RecordForm
          title={form === 'edit' ? 'Редактировать контрагента' : 'Записать звонок'}
          endpoint={form === 'edit' ? `/counterparties/${selected.id}` : '/calls'}
          method={form === 'edit' ? 'PATCH' : 'POST'}
          fields={form === 'edit' ? clientFields : callFields}
          initial={form === 'edit' ? selected : { client_id: selected.id }}
          extra={form === 'edit' ? { version: selected.version } : {}}
          onClose={() => setForm(undefined)}
          onSuccess={(result) => {
            if (form === 'edit') setSelected(result);
            setForm(undefined);
            setRevision((v) => v + 1);
          }}
        />
      )}
      <ImportDialog
        open={importing}
        onClose={() => setImporting(false)}
        onSuccess={refreshClients}
      />
    </>
  );
}
export function Calls() {
  const [selected, setSelected] = useState<Entity>();
  return (
    <>
      <PageHeading
        title="База обзвона"
        description="Зафиксируйте разговор и назначьте следующее действие."
      />
      <Collection
        title="Журнал звонков"
        endpoint="/calls"
        fields={callFields}
        createLabel="Записать звонок"
        onSelect={setSelected}
        columns={[
          {
            key: 'client_name',
            label: 'Клиент',
            render: (r) => String(r.client_name || r.client_id),
          },
          { key: 'result', label: 'Результат', render: (r) => <Badge value={r.result} /> },
          { key: 'comment', label: 'Комментарий' },
          { key: 'created_at', label: 'Дата', render: (r) => date(r.created_at, true) },
          { key: 'next_at', label: 'Следующий контакт', render: (r) => date(r.next_at, true) },
        ]}
      />
      {selected && (
        <Modal title="Запись звонка" onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Клиент: selected.client_name || selected.client_id,
                Результат: selected.result,
                Комментарий: selected.comment,
                'Следующее действие': date(selected.next_at, true),
              }}
            />
            {selected.result === 'request_received' && (
              <Link
                to={`/requests?new=1&client_id=${selected.client_id}&source_call_id=${selected.id}`}
                className="button primary"
              >
                <Plus size={16} />
                Создать заявку из звонка
              </Link>
            )}
          </div>
        </Modal>
      )}
    </>
  );
}
export function Tasks() {
  const [selected, setSelected] = useState<Entity>();
  const [revision, setRevision] = useState(0);
  const editFields: Field[] = [
    { name: 'title', label: 'Название', required: true },
    {
      name: 'status',
      label: 'Статус',
      required: true,
      type: 'select',
      options: ['assigned', 'in_progress', 'completed', 'cancelled'].map((value) => ({
        value,
        label:
          {
            assigned: 'Назначена',
            in_progress: 'В работе',
            completed: 'Завершена',
            cancelled: 'Отменена',
          }[value] || value,
      })),
    },
    { name: 'due_at', label: 'Срок', type: 'datetime-local', required: true },
    {
      name: 'result',
      label: 'Результат / причина отмены',
      type: 'textarea',
      help: 'Обязательно при завершении и отмене задачи.',
    },
  ];
  return (
    <>
      <PageHeading
        title="Задачи"
        description="Договорённости, следующие контакты и сроки команды."
      />
      <Collection
        title="Все задачи"
        endpoint="/tasks"
        fields={taskFieldsForPage}
        createLabel="Создать задачу"
        onSelect={setSelected}
        refreshKey={revision}
        columns={[
          { key: 'title', label: 'Задача' },
          { key: 'status', label: 'Состояние', render: (r) => <Badge value={r.status} /> },
          { key: 'priority', label: 'Приоритет', render: (r) => <Badge value={r.priority} /> },
          {
            key: 'due_at',
            label: 'Срок',
            render: (r) => (
              <span
                className={
                  new Date(String(r.due_at)) < new Date() && r.status !== 'completed'
                    ? 'overdue'
                    : ''
                }
              >
                {date(r.due_at, true)}
              </span>
            ),
          },
          {
            key: 'assignee_name',
            label: 'Исполнитель',
            render: (r) => String(r.assignee_name || r.assignee_id || '—'),
          },
        ]}
      />
      {selected && (
        <RecordForm
          title="Изменить задачу"
          endpoint={`/tasks/${selected.id}`}
          method="PATCH"
          fields={editFields}
          initial={{ ...selected, due_at: String(selected.due_at || '').slice(0, 16) }}
          extra={{ version: selected.version }}
          onClose={() => setSelected(undefined)}
          onSuccess={() => {
            setSelected(undefined);
            setRevision((v) => v + 1);
          }}
        />
      )}
    </>
  );
}
import { taskFields as taskFieldsForPage } from '../lib/fields';
