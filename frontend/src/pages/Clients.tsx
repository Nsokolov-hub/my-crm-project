import { History, Pencil, Phone, Plus, Upload } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { ImportDialog } from '../components/ImportDialog';
import {
  Badge,
  Button,
  DataTable,
  DetailPairs,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  PageHeading,
  Pagination,
} from '../components/ui';
import { callFields, clientFields, contactFields } from '../lib/fields';
import { date } from '../lib/format';
import { api } from '../lib/api';
import { useApi, useCommand } from '../lib/hooks';
import type { Entity, Field, Page, User } from '../lib/types';
const callProspectFields: Field[] = [
  { name: 'name', label: 'Название', required: true, wide: true },
  { name: 'tax_id', label: 'ИНН', required: true },
  { name: 'phone', label: 'Телефон' },
  { name: 'email', label: 'E-mail', type: 'email' },
];

function CallHistory({ clientId }: { clientId: string }) {
  const [page, setPage] = useState(1);
  const client = useApi<Entity>(`/counterparties/${clientId}`);
  const calls = useApi<Page>(`/calls?client_id=${clientId}&page=${page}&page_size=25`);
  return (
    <div className="form-body">
      <p>{String(client.data?.name || '')}</p>
      {calls.loading ? (
        <Loading />
      ) : calls.error ? (
        <ErrorBox error={calls.error} retry={calls.refresh} />
      ) : calls.data?.items.length ? (
        <DataTable
          rows={calls.data.items}
          columns={[
            {
              key: 'occurred_at',
              label: 'Дата и время',
              render: (row) => date(row.occurred_at, true),
            },
            { key: 'author_name', label: 'Кто звонил' },
            { key: 'result', label: 'Результат', render: (row) => <Badge value={row.result} /> },
            { key: 'comment', label: 'Комментарий' },
            {
              key: 'next_at',
              label: 'Следующий контакт',
              render: (row) => date(row.next_at, true),
            },
          ]}
        />
      ) : (
        <Empty title="Звонков ещё нет" />
      )}
      <Pagination page={page} total={calls.data?.total || 0} pageSize={25} onChange={setPage} />
    </div>
  );
}
export function Clients() {
  const auth = useAuth();
  const [selected, setSelected] = useState<Entity>();
  const [form, setForm] = useState<'edit' | 'call'>();
  const [importing, setImporting] = useState(false);
  const [revision, setRevision] = useState(0);
  const refreshClients = useCallback(() => setRevision((value) => value + 1), []);
  return (
    <>
      <PageHeading
        title="Клиенты и поставщики"
        description="Рабочая база компаний. Холодные клиенты находятся в разделе «База обзвона»."
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
        query="&client_base=working"
        fields={clientFields}
        createLabel="Добавить контрагента"
        canCreate={auth.can('clients.write')}
        refreshKey={revision}
        columns={[
          { key: 'internal_code', label: 'Код' },
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
              {auth.can('clients.write') && (
                <Button variant="secondary" onClick={() => setForm('edit')}>
                  <Pencil size={15} />
                  Редактировать
                </Button>
              )}
              {auth.can('calls.write') && auth.can('clients.write') && (
                <Button onClick={() => setForm('call')}>
                  <Phone size={15} />
                  Записать звонок
                </Button>
              )}
              <Link
                className="button secondary"
                to={`/requests?client_id=${selected.id}`}
                onClick={() => setSelected(undefined)}
              >
                Заявки клиента
              </Link>
            </div>
            <div className="client-card-columns">
              <div>
                <DetailPairs
                  values={{
                    Код: selected.internal_code,
                    Тип: selected.kind,
                    ИНН: selected.tax_id,
                    Страна: selected.country,
                    Телефон: selected.phone,
                    Почта: selected.email,
                  }}
                />
              </div>
              <aside>
                <Collection
                  title="Контакты"
                  endpoint={`/counterparties/${selected.id}/contacts`}
                  fields={contactFields}
                  createLabel="Добавить контакт"
                  canCreate={auth.can('clients.write') && selected.kind !== 'supplier'}
                  columns={[
                    {
                      key: 'name',
                      label: 'Контакт',
                      render: (contact) => (
                        <Link to={`/contacts?contact_id=${contact.id}`}>
                          {String(contact.name)}
                        </Link>
                      ),
                    },
                    { key: 'department', label: 'Отдел' },
                  ]}
                />
              </aside>
            </div>
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
  const auth = useAuth();
  const navigate = useNavigate();
  const promotion = useCommand();
  const [searchParams] = useSearchParams();
  const [selected, setSelected] = useState<Entity>();
  const [prospect, setProspect] = useState<Entity>();
  const [historyClientId, setHistoryClientId] = useState<string | undefined>(
    () => searchParams.get('client_id') || undefined,
  );
  useEffect(() => {
    if (searchParams.get('client_id'))
      setHistoryClientId(searchParams.get('client_id') || undefined);
  }, [searchParams]);
  const [recording, setRecording] = useState(false);
  const [importing, setImporting] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkAssignee, setBulkAssignee] = useState('');
  const [bulkDueAt, setBulkDueAt] = useState('');
  const [bulkTitle, setBulkTitle] = useState('Обзвонить клиента');
  const [bulkNotice, setBulkNotice] = useState('');
  const users = useApi<Page<User>>(bulkOpen ? '/users' : null);
  const bulkCommand = useCommand();
  const [revision, setRevision] = useState(0);
  const canBulkAssign = auth.can('requests.assign') && auth.can('tasks.write');
  async function promoteClient(clientId: string, createRequestFromCall?: string) {
    try {
      const client = await api<Entity>(`/counterparties/${clientId}`);
      await promotion.run(`/counterparties/${clientId}/promote`, { version: client.version });
      setProspect(undefined);
      setRevision((value) => value + 1);
      setBulkNotice('Клиент перенесён в рабочую базу. Код и история сохранены.');
      if (createRequestFromCall)
        navigate(`/requests?new=1&client_id=${clientId}&source_call_id=${createRequestFromCall}`);
    } catch {
      /* visible error */
    }
  }
  function toggleClient(id: string) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function selectPage(ids: string[], checked: boolean) {
    setSelectedIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => {
        if (checked) next.add(id);
        else next.delete(id);
      });
      return next;
    });
  }
  async function assignSelected(event: React.FormEvent) {
    event.preventDefault();
    if (!bulkAssignee || !bulkDueAt || !selectedIds.size) return;
    try {
      const result = await bulkCommand.run<{ count: number }>('/tasks/bulk-calls', {
        client_ids: [...selectedIds],
        assignee_id: bulkAssignee,
        due_at: new Date(bulkDueAt).toISOString(),
        title: bulkTitle,
      });
      if (result) {
        setBulkNotice(`Создано задач: ${result.count}. Клиенты назначены сотруднику.`);
        setSelectedIds(new Set());
        setBulkOpen(false);
        setRevision((value) => value + 1);
      }
    } catch {
      /* ErrorBox shows the API error. */
    }
  }
  return (
    <>
      <PageHeading
        title="База обзвона"
        description="Клиенты для обзвона, история разговоров и следующие действия."
        actions={
          auth.can('calls.write') &&
          auth.can('clients.write') && (
            <Button variant="secondary" onClick={() => setImporting(true)}>
              <Upload size={17} /> Загрузить XLSX
            </Button>
          )
        }
      />
      {bulkNotice && (
        <p role="status" className="info-note">
          {bulkNotice}
        </p>
      )}
      <Collection
        title="Клиенты для обзвона"
        endpoint="/counterparties"
        query="&kind=client&client_base=cold"
        extra={{ client_base: 'cold' }}
        fields={callProspectFields}
        createLabel="Добавить клиента"
        canCreate={auth.can('clients.write')}
        refreshKey={revision}
        onSelect={setProspect}
        filterKeys={['name', 'tax_id', 'profile', 'city', 'phone', 'email']}
        selection={
          canBulkAssign
            ? { selectedIds, onToggle: toggleClient, onSelectPage: selectPage }
            : undefined
        }
        action={
          canBulkAssign && (
            <Button
              variant="secondary"
              disabled={!selectedIds.size}
              onClick={() => setBulkOpen(true)}
            >
              Назначить выбранных ({selectedIds.size})
            </Button>
          )
        }
        columns={[
          { key: 'internal_code', label: 'Код' },
          { key: 'name', label: 'Название' },
          { key: 'tax_id', label: 'ИНН' },
          {
            key: 'profile',
            label: 'Профиль',
            render: (row) => String((row.details as Entity | undefined)?.profile || '—'),
          },
          {
            key: 'city',
            label: 'Регион/город',
            render: (row) => String((row.details as Entity | undefined)?.city || '—'),
          },
          { key: 'phone', label: 'Телефон' },
          { key: 'email', label: 'E-mail' },
          {
            key: 'history',
            label: 'История',
            sortable: false,
            render: (row) => (
              <Button variant="secondary" onClick={() => setHistoryClientId(row.id)}>
                <History size={15} /> История
              </Button>
            ),
          },
        ]}
      />
      {bulkOpen && (
        <Modal
          title={`Назначить обзвон: ${selectedIds.size} клиентов`}
          onClose={() => setBulkOpen(false)}
        >
          <form className="form-body" onSubmit={(event) => void assignSelected(event)}>
            <p>
              Для каждого выбранного клиента создаётся задача одному сотруднику. Клиенты будут
              закреплены за ним.
            </p>
            <label className="field">
              Исполнитель
              <select
                required
                value={bulkAssignee}
                onChange={(event) => setBulkAssignee(event.target.value)}
              >
                <option value="">Выберите сотрудника</option>
                {users.data?.items.map((employee) => (
                  <option key={employee.id} value={employee.id}>
                    {employee.name}
                  </option>
                ))}
              </select>
            </label>
            {users.loading && <Loading />}
            <ErrorBox error={users.error || bulkCommand.error} />
            <label className="field">
              Название задачи
              <input
                required
                maxLength={250}
                value={bulkTitle}
                onChange={(event) => setBulkTitle(event.target.value)}
              />
            </label>
            <label className="field">
              Срок
              <input
                type="datetime-local"
                required
                value={bulkDueAt}
                onChange={(event) => setBulkDueAt(event.target.value)}
              />
            </label>
            <div className="inline-actions">
              <Button type="submit" busy={bulkCommand.busy} disabled={!users.data}>
                Назначить {selectedIds.size} задач
              </Button>
              <Button type="button" variant="ghost" onClick={() => setBulkOpen(false)}>
                Отмена
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {historyClientId && (
        <Modal title="История взаимодействия" wide onClose={() => setHistoryClientId(undefined)}>
          <CallHistory clientId={historyClientId} />
        </Modal>
      )}
      <Collection
        title="Журнал звонков"
        endpoint="/calls"
        fields={callFields}
        createLabel="Записать звонок"
        canCreate={auth.can('calls.write') && auth.can('clients.write')}
        refreshKey={revision}
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
      {prospect && !recording && (
        <Modal title={String(prospect.name)} onClose={() => setProspect(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Код: prospect.internal_code,
                ИНН: prospect.tax_id,
                Профиль: (prospect.details as Entity | undefined)?.profile,
                Категория: (prospect.details as Entity | undefined)?.category,
                'Регион/город': (prospect.details as Entity | undefined)?.city,
                ОГРН: (prospect.details as Entity | undefined)?.registration_number,
                ОКВЭД: (prospect.details as Entity | undefined)?.okved,
                Сайт: (prospect.details as Entity | undefined)?.website,
                Телефон: prospect.phone,
                'E-mail': prospect.email || (prospect.details as Entity | undefined)?.raw_email,
                'Телефон закупок': (prospect.details as Entity | undefined)?.procurement_phone,
                'E-mail закупок': (prospect.details as Entity | undefined)?.procurement_email,
                Примечание: (prospect.details as Entity | undefined)?.comment,
              }}
            />
            <ErrorBox error={promotion.error} />
            {auth.can('clients.write') && (
              <Button
                variant="secondary"
                busy={promotion.busy}
                onClick={() => void promoteClient(prospect.id)}
              >
                Перенести в рабочую базу
              </Button>
            )}
            {auth.can('calls.write') && (
              <Button onClick={() => setRecording(true)}>
                <Phone size={15} /> Записать звонок
              </Button>
            )}
          </div>
        </Modal>
      )}
      {prospect && recording && (
        <RecordForm
          title="Записать звонок"
          endpoint="/calls"
          fields={callFields}
          initial={{ client_id: prospect.id }}
          onClose={() => setRecording(false)}
          onSuccess={() => {
            setRecording(false);
            setProspect(undefined);
            setRevision((value) => value + 1);
          }}
        />
      )}
      <ImportDialog
        open={importing}
        mode="calls"
        onClose={() => setImporting(false)}
        onSuccess={() => setRevision((value) => value + 1)}
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
              <Button
                busy={promotion.busy}
                onClick={() => void promoteClient(String(selected.client_id), selected.id)}
              >
                <Plus size={16} />
                Создать заявку из звонка
              </Button>
            )}
            <ErrorBox error={promotion.error} />
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
          {
            key: 'client_name',
            label: 'Клиент',
            render: (r) =>
              r.client_name ? (
                <Link to={`/calls?client_id=${r.entity_id}`}>{String(r.client_name)}</Link>
              ) : (
                '—'
              ),
          },
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
          initial={selected}
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
