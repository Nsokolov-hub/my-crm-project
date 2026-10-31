import { Archive, ArchiveRestore, History, Merge, Pencil, Phone, Plus, Upload } from 'lucide-react';
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
import { CounterpartyDocuments } from '../components/CounterpartyDocuments';
// The API requires a reason to change the owner and keeps it in the counterparty history.
const reassignmentReasonField: Field = {
  name: 'reason',
  label: 'Причина изменения',
  type: 'textarea',
  wide: true,
  help: 'Обязательна, если меняете ответственного. Сохраняется в истории контрагента.',
};
const archiveReasonField: Field = {
  name: 'reason',
  label: 'Причина',
  type: 'textarea',
  required: true,
  minLength: 3,
};
const mergeFields: Field[] = [
  {
    name: 'target_id',
    label: 'Основная карточка',
    type: 'select',
    source: '/counterparties?client_base=working',
    required: true,
    help: 'Карточка, которая останется. Найдите её по названию, ИНН или коду.',
  },
  archiveReasonField,
];
function mergeNotice(moved: Record<string, number>) {
  return (
    `Дубль объединён. Перенесено: заявок — ${moved.requests}, контактов — ${moved.contacts}, ` +
    `звонков — ${moved.calls}, документов — ${moved.documents}, задач — ${moved.tasks}.`
  );
}
const callProspectFields: Field[] = [
  { name: 'name', label: 'Название', required: true, wide: true },
  { name: 'tax_id', label: 'ИНН', required: true },
  { name: 'phone', label: 'Телефон', type: 'tel' },
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
  const canReassign = auth.can('requests.assign');
  const editableClientFields = clientFields.filter(
    (field) => field.name !== 'owner_id' || canReassign,
  );
  const [selected, setSelected] = useState<Entity>();
  const [form, setForm] = useState<'edit' | 'call' | 'archive' | 'merge'>();
  const [notice, setNotice] = useState('');
  const [archive, setArchive] = useState(false);
  const [importing, setImporting] = useState(false);
  const [revision, setRevision] = useState(0);
  const refreshClients = useCallback(() => setRevision((value) => value + 1), []);
  function open(row: Entity | undefined) {
    setNotice('');
    setSelected(row);
  }
  function changed(row: Entity) {
    setSelected(row);
    setForm(undefined);
    setRevision((value) => value + 1);
  }
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
      <div className="tabs">
        <button className={!archive ? 'active' : ''} onClick={() => setArchive(false)}>
          Действующие
        </button>
        <button className={archive ? 'active' : ''} onClick={() => setArchive(true)}>
          Архив
        </button>
      </div>
      <Collection
        key={archive ? 'archive' : 'working'}
        title={archive ? 'Архив контрагентов' : 'База контрагентов'}
        description={
          archive
            ? 'Контрагенты из архива не предлагаются в заявках, квотах и волнах. Их можно вернуть, кроме объединённых дублей.'
            : undefined
        }
        endpoint="/counterparties"
        query={archive ? '&archived=true' : '&client_base=working'}
        fields={editableClientFields}
        createLabel="Добавить контрагента"
        canCreate={!archive && auth.can('clients.write')}
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
          {
            key: 'owner_name',
            label: 'Ответственный',
            render: (row) => String(row.owner_name || 'Не назначен'),
          },
          { key: 'country', label: 'Страна' },
          { key: 'phone', label: 'Телефон' },
          { key: 'email', label: 'Почта' },
          ...(archive
            ? [
                {
                  key: 'merged_into_name',
                  label: 'Объединён с',
                  render: (row: Entity) => String(row.merged_into_name || '—'),
                },
              ]
            : [
                {
                  key: 'created_at',
                  label: 'Добавлен',
                  render: (row: Entity) => date(row.created_at),
                },
              ]),
        ]}
        onSelect={open}
      />
      {selected && !form && (
        <Modal title={String(selected.name)} wide onClose={() => open(undefined)}>
          <div className="form-body">
            {notice && (
              <p className="info-note" role="status">
                {notice}
              </p>
            )}
            {Boolean(selected.archived) && (
              <p className="info-note">
                {selected.merged_into_id
                  ? `Дубль объединён с «${String(selected.merged_into_name || '')}»: заявки, контакты, звонки и документы перенесены туда. Выпущенные КП, счета и оплаты остаются за этой карточкой и засчитываются основной.`
                  : 'Контрагент в архиве: его не предлагают в заявках, квотах и волнах. Верните его из архива, если он снова нужен.'}
              </p>
            )}
            <div className="inline-actions">
              {!selected.archived && auth.can('clients.write') && (
                <Button variant="secondary" onClick={() => setForm('edit')}>
                  <Pencil size={15} />
                  Редактировать
                </Button>
              )}
              {!selected.archived && auth.can('calls.write') && auth.can('clients.write') && (
                <Button onClick={() => setForm('call')}>
                  <Phone size={15} />
                  Записать звонок
                </Button>
              )}
              {selected.merged_into_id ? (
                <Button
                  variant="secondary"
                  onClick={() =>
                    void api<Entity>(`/counterparties/${String(selected.merged_into_id)}`).then(
                      open,
                    )
                  }
                >
                  Открыть основную карточку
                </Button>
              ) : (
                <Link
                  className="button secondary"
                  to={`/requests?client_id=${selected.id}`}
                  onClick={() => open(undefined)}
                >
                  Заявки клиента
                </Link>
              )}
              {!selected.archived && canReassign && auth.can('clients.write') && (
                <Button variant="secondary" onClick={() => setForm('merge')}>
                  <Merge size={15} />
                  Объединить с основной
                </Button>
              )}
              {!selected.merged_into_id && auth.can('clients.write') && (
                <Button variant="secondary" onClick={() => setForm('archive')}>
                  {selected.archived ? <ArchiveRestore size={15} /> : <Archive size={15} />}
                  {selected.archived ? 'Вернуть из архива' : 'В архив'}
                </Button>
              )}
            </div>
            <div className="client-card-columns">
              <div>
                <DetailPairs
                  values={{
                    Код: selected.internal_code,
                    Тип: selected.kind,
                    ИНН: selected.tax_id,
                    Страна: selected.country,
                    ...(selected.in_belarus
                      ? { 'НДС продажи': '0 % — клиент в Республике Беларусь' }
                      : {}),
                    Телефон: selected.phone,
                    Почта: selected.email,
                    Ответственный: selected.owner_name || 'Не назначен',
                  }}
                />
              </div>
              <aside>
                <Collection
                  title="Контакты"
                  endpoint={`/counterparties/${selected.id}/contacts`}
                  fields={contactFields}
                  createLabel="Добавить контакт"
                  canCreate={!selected.archived && auth.can('clients.write')}
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
            <CounterpartyDocuments
              counterpartyId={selected.id}
              readOnly={Boolean(selected.archived)}
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
      {selected && form === 'archive' && (
        <RecordForm
          title={selected.archived ? 'Вернуть контрагента из архива' : 'Убрать контрагента в архив'}
          endpoint={`/counterparties/${selected.id}/archive`}
          command
          extra={{ version: selected.version, archived: !selected.archived }}
          fields={[archiveReasonField]}
          note={
            selected.archived
              ? 'Контрагент вернётся в списки и в выбор в заявках. Вместе с ним вернутся заявки, ушедшие в архив с ним.'
              : 'Контрагент пропадёт из списков и из выбора в заявках, квотах и волнах, история сохранится. Его завершённые заявки уйдут в архив вместе с ним. Пока идёт работа по заявкам, волнам или заказам поставщику, система их назовёт и в архив не пустит. Дубль лучше объединить с основной карточкой.'
          }
          onClose={() => setForm(undefined)}
          onSuccess={changed}
        />
      )}
      {selected && form === 'merge' && (
        <RecordForm
          title="Объединить дубль с основной карточкой"
          endpoint={`/counterparties/${selected.id}/merge`}
          command
          extra={{ version: selected.version }}
          fields={mergeFields}
          submitLabel="Объединить"
          note={`«${String(selected.name)}» уйдёт в архив. Его заявки, контакты, звонки, задачи и документы перейдут в основную карточку, а пустые реквизиты основной заполнятся из дубля. Выпущенные КП, счета и оплаты останутся за дублем и будут засчитываться основной карточке.`}
          onClose={() => setForm(undefined)}
          onSuccess={(result) => {
            changed(result);
            setNotice(mergeNotice(result.moved as Record<string, number>));
          }}
        />
      )}
      {selected && (form === 'edit' || form === 'call') && (
        <RecordForm
          title={form === 'edit' ? 'Редактировать контрагента' : 'Записать звонок'}
          endpoint={form === 'edit' ? `/counterparties/${selected.id}` : '/calls'}
          method={form === 'edit' ? 'PATCH' : 'POST'}
          fields={
            form === 'edit'
              ? [...editableClientFields, ...(canReassign ? [reassignmentReasonField] : [])]
              : callFields
          }
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
export { Tasks } from './Tasks';
