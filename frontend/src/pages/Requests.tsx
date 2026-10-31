import { Columns3, List, Plus, Search } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { RecordForm } from '../components/Form';
import {
  Badge,
  Button,
  DataTable,
  Empty,
  ErrorBox,
  Loading,
  PageHeading,
  Pagination,
} from '../components/ui';
import { api } from '../lib/api';
import { requestFields } from '../lib/fields';
import { date, stages, stageLabels } from '../lib/format';
import { useApi, useCommand, useDebounced } from '../lib/hooks';
import type { Page, RequestEntity } from '../lib/types';

/** Business remark of 31 October 2026: the filters an employee saves; the list always opens with them. */
const PREFERENCE = '/me/preferences/requests.filters';
type Filters = {
  q: string;
  stage: string;
  owner_id: string;
  proposal_from: string;
  proposal_to: string;
  sort: string;
  direction: string;
  view: 'table' | 'board';
};
const EMPTY: Filters = {
  q: '',
  stage: '',
  owner_id: '',
  proposal_from: '',
  proposal_to: '',
  sort: 'created_at',
  direction: 'desc',
  view: 'table',
};
function fromSaved(value: Record<string, unknown> | null | undefined): Filters {
  const text = (key: keyof Filters) => (typeof value?.[key] === 'string' ? String(value[key]) : '');
  return {
    q: text('q'),
    stage: text('stage'),
    owner_id: text('owner_id'),
    proposal_from: text('proposal_from'),
    proposal_to: text('proposal_to'),
    sort: text('sort') || EMPTY.sort,
    direction: text('direction') === 'asc' ? 'asc' : 'desc',
    view: text('view') === 'board' ? 'board' : 'table',
  };
}
function localDay(offset = 0) {
  const day = new Date();
  day.setDate(day.getDate() + offset);
  return new Date(day.getTime() - day.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
export function Requests() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  // An address with its own filters (a link from a client card, a search) is not replaced by the saved ones.
  const linked = ['q', 'stage', 'client_id'].some((key) => params.get(key));
  const [filters, setFilters] = useState<Filters>({
    ...EMPTY,
    q: params.get('q') || '',
    stage: params.get('stage') || '',
  });
  const [ready, setReady] = useState(linked);
  const [saved, setSaved] = useState<Filters | null>(null);
  const [notice, setNotice] = useState('');
  const [creating, setCreating] = useState(params.get('new') === '1');
  const [page, setPage] = useState(1);
  const command = useCommand();
  const { q, stage, sort, direction, view } = filters;
  const debounced = useDebounced(q);
  const users = useApi<Page<{ id: string; name: string }>>('/users');
  useEffect(() => {
    let active = true;
    api<{ value: Record<string, unknown> | null }>(PREFERENCE)
      .then((result) => {
        if (!active) return;
        const value = result.value ? fromSaved(result.value) : null;
        setSaved(value);
        if (value && !linked) setFilters(value);
      })
      .catch(() => undefined)
      .finally(() => active && setReady(true));
    return () => {
      active = false;
    };
    // The saved filters are read once, when the list opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  function change(patch: Partial<Filters>) {
    setFilters((current) => ({ ...current, ...patch }));
    setPage(1);
    setNotice('');
  }
  const setView = (next: 'table' | 'board') => change({ view: next });
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(view === 'board' ? 100 : 25),
    q: debounced,
    stage,
    sort,
    direction,
  });
  if (filters.owner_id) query.set('owner_id', filters.owner_id);
  if (filters.proposal_from) query.set('proposal_from', filters.proposal_from);
  if (filters.proposal_to) query.set('proposal_to', filters.proposal_to);
  if (params.get('client_id')) query.set('client_id', String(params.get('client_id')));
  const list = useApi<Page<RequestEntity>>(ready ? `/requests?${query.toString()}` : null);
  const changed = JSON.stringify(filters) !== JSON.stringify(saved || EMPTY);
  async function saveFilters() {
    try {
      await command.run(PREFERENCE, { value: filters }, 'PUT');
      setSaved(filters);
      setNotice('Фильтры сохранены: список заявок будет открываться с ними.');
    } catch {
      /* ErrorBox shows the failure. */
    }
  }
  async function forgetFilters() {
    if (!window.confirm('Удалить сохранённые фильтры? Список заявок будет открываться без них.'))
      return;
    try {
      await command.run(PREFERENCE, {}, 'DELETE');
      setSaved(null);
      setFilters(EMPTY);
      setPage(1);
      setNotice('Сохранённые фильтры удалены.');
    } catch {
      /* ErrorBox shows the failure. */
    }
  }
  return (
    <>
      <PageHeading
        title="Заявки"
        description="От первого запроса клиента до исполнения каждой позиции."
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus size={17} />
            Новая заявка
          </Button>
        }
      />
      <div className="panel">
        <div className="request-toolbar">
          <div className="search-input">
            <Search size={17} />
            <input
              aria-label="Поиск по заявкам"
              placeholder="Найти заявку, номер или наименование…"
              value={q}
              onChange={(e) => change({ q: e.target.value })}
            />
          </div>
          <select
            aria-label="Коммерческий этап"
            value={stage}
            onChange={(e) => change({ stage: e.target.value })}
          >
            <option value="">Все этапы</option>
            {stages.map((s) => (
              <option key={s} value={s}>
                {stageLabels[s]}
              </option>
            ))}
          </select>
          <select
            aria-label="Менеджер"
            value={filters.owner_id}
            onChange={(e) => change({ owner_id: e.target.value })}
          >
            <option value="">Все менеджеры</option>
            {(users.data?.items || []).map((user) => (
              <option key={user.id} value={user.id}>
                {user.name}
              </option>
            ))}
          </select>
          <div className="segmented">
            <button
              className={view === 'table' ? 'active' : ''}
              onClick={() => setView('table')}
              aria-label="Таблица"
            >
              <List size={18} />
            </button>
            <button
              className={view === 'board' ? 'active' : ''}
              onClick={() => setView('board')}
              aria-label="Доска этапов"
            >
              <Columns3 size={18} />
            </button>
          </div>
        </div>
        <div className="filter-bar requests-filter-bar">
          <span className="filter-label">Последнее КП выпущено</span>
          <label>
            с
            <input
              type="date"
              aria-label="Последнее КП: с даты"
              value={filters.proposal_from}
              max={filters.proposal_to || undefined}
              onChange={(e) => change({ proposal_from: e.target.value })}
            />
          </label>
          <label>
            по
            <input
              type="date"
              aria-label="Последнее КП: по дату"
              value={filters.proposal_to}
              min={filters.proposal_from || undefined}
              onChange={(e) => change({ proposal_to: e.target.value })}
            />
          </label>
          <button
            type="button"
            className="chip-button"
            onClick={() =>
              change({
                proposal_from: localDay(),
                proposal_to: localDay(),
                sort: 'last_proposal_at',
                direction: 'desc',
              })
            }
          >
            Сегодня
          </button>
          <button
            type="button"
            className="chip-button"
            onClick={() =>
              change({
                proposal_from: localDay(-6),
                proposal_to: localDay(),
                sort: 'last_proposal_at',
                direction: 'desc',
              })
            }
          >
            7 дней
          </button>
          {(filters.proposal_from || filters.proposal_to) && (
            <button
              type="button"
              className="chip-button"
              onClick={() => change({ proposal_from: '', proposal_to: '' })}
            >
              Любая дата
            </button>
          )}
          <span className="filter-spacer" />
          <Button
            variant="secondary"
            busy={command.busy}
            disabled={!changed && Boolean(saved)}
            onClick={() => void saveFilters()}
          >
            Сохранить фильтры
          </Button>
          {saved && (
            <Button variant="ghost" busy={command.busy} onClick={() => void forgetFilters()}>
              Удалить сохранённые
            </Button>
          )}
        </div>
        {(notice || (saved && !changed)) && (
          <p className="filter-notice">{notice || 'Список открыт с сохранёнными фильтрами.'}</p>
        )}
        <ErrorBox error={command.error} />
        {list.loading || !ready ? (
          <Loading />
        ) : list.error ? (
          <ErrorBox error={list.error} retry={list.refresh} />
        ) : view === 'table' ? (
          <DataTable
            rows={list.data?.items || []}
            onRow={(r) => navigate(`/requests/${r.id}`)}
            sort={sort}
            onSort={(key) =>
              change({
                sort: key,
                direction: sort === key && direction === 'desc' ? 'asc' : 'desc',
              })
            }
            columns={[
              {
                key: 'number',
                label: 'Заявка',
                render: (r) => (
                  <span className="stacked">
                    <strong>{r.number}</strong>
                    <small>{r.title}</small>
                  </span>
                ),
              },
              { key: 'client_name', label: 'Клиент' },
              {
                // Business remarks of 21 and 22 October 2026: suppliers that answered, a refusal included.
                key: 'answered_suppliers',
                label: 'Ответы поставщиков',
                sortable: false,
                render: (r) => {
                  const answered = Number(r.answered_suppliers || 0);
                  const asked = Number(r.asked_suppliers || 0);
                  const priced = Number(r.quoted_suppliers || 0);
                  if (!asked && !answered) return '—';
                  return (
                    <span className="stacked">
                      <span>
                        {answered} из {asked}
                      </span>
                      {priced < answered && <small>с ценой: {priced}</small>}
                    </span>
                  );
                },
              },
              {
                key: 'commercial_stage',
                label: 'Коммерческий этап',
                render: (r) => <Badge value={r.commercial_stage} />,
              },
              { key: 'owner_name', label: 'Ответственный', sortable: false },
              {
                // Business remark of 31 October 2026: when the latest proposal of the request was issued.
                key: 'last_proposal_at',
                label: 'Последнее КП',
                render: (r) => (r.last_proposal_at ? date(r.last_proposal_at, true) : '—'),
              },
              { key: 'due_at', label: 'Срок', render: (r) => date(r.due_at) },
              { key: 'created_at', label: 'Создана', render: (r) => date(r.created_at) },
            ]}
          />
        ) : (
          <div className="kanban">
            {stages
              .filter((s) => !stage || s === stage)
              .map((s) => (
                <section key={s}>
                  <header>
                    <i />
                    <strong>{stageLabels[s]}</strong>
                    <span>
                      {list.data?.items.filter((r) => r.commercial_stage === s).length || 0}
                    </span>
                  </header>
                  {list.data?.items
                    .filter((r) => r.commercial_stage === s)
                    .map((r) => (
                      <Link to={`/requests/${r.id}`} className="kanban-card" key={r.id}>
                        <small>{r.number}</small>
                        <h3>{r.title}</h3>
                        <p>{String(r.client_name || '')}</p>
                        <footer>
                          <span>{date(r.due_at)}</span>
                          <span className="mini-avatar">{String(r.owner_name || '?')[0]}</span>
                        </footer>
                      </Link>
                    ))}
                  {!list.data?.items.some((r) => r.commercial_stage === s) && (
                    <Empty
                      compact
                      title="Пока пусто"
                      description="Заявки появятся при переходе на этот этап."
                    />
                  )}
                </section>
              ))}
          </div>
        )}
        <Pagination
          page={page}
          total={list.data?.total || 0}
          pageSize={view === 'board' ? 100 : 25}
          onChange={setPage}
        />
      </div>
      {creating && (
        <RecordForm
          title="Новая заявка"
          endpoint="/requests"
          fields={requestFields}
          initial={{ client_id: params.get('client_id') || '' }}
          extra={
            params.get('source_call_id')
              ? { source_call_id: params.get('source_call_id') }
              : undefined
          }
          onClose={() => {
            setCreating(false);
            params.delete('new');
            setParams(params);
          }}
          onSuccess={(row) => navigate(`/requests/${row.id}`)}
        />
      )}
    </>
  );
}
