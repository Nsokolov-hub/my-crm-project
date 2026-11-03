import { ArrowDown, ArrowUp, Columns3, List, Plus, Search, SlidersHorizontal } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
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
import { date, decimal, stages, stageLabels } from '../lib/format';
import { useApi, useCommand, useDebounced } from '../lib/hooks';
import type { Column, Page, RequestEntity } from '../lib/types';

/** Business remark of 31 October 2026: the filters an employee saves; the list always opens with them. */
const PREFERENCE = '/me/preferences/requests.filters';
/** Business remark of 3 November 2026: the columns an employee shows, in their order, kept for them. */
const COLUMNS_PREFERENCE = '/me/preferences/requests.columns';

type RequestColumn = Column<RequestEntity> & { title: string };
const REQUEST_COLUMNS: RequestColumn[] = [
  {
    key: 'number',
    label: 'Заявка',
    title: 'Заявка (номер и название)',
    render: (r) => (
      <span className="stacked">
        <strong>{r.number}</strong>
        <small>{r.title}</small>
      </span>
    ),
  },
  { key: 'client_name', label: 'Клиент', title: 'Клиент' },
  {
    key: 'client_tax_id',
    label: 'ИНН клиента',
    title: 'ИНН клиента',
    sortable: false,
    render: (r) => String(r.client_tax_id || '—'),
  },
  {
    key: 'contact_name',
    label: 'Контакт',
    title: 'Контактное лицо',
    sortable: false,
    render: (r) => String(r.contact_name || '—'),
  },
  {
    // Business remarks of 21 and 22 October 2026: suppliers that answered, a refusal included.
    key: 'answered_suppliers',
    label: 'Ответы поставщиков',
    title: 'Ответы поставщиков',
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
    key: 'items_count',
    label: 'Позиций',
    title: 'Позиций в заявке',
    sortable: false,
    render: (r) => String(r.items_count ?? '—'),
  },
  {
    key: 'commercial_stage',
    label: 'Коммерческий этап',
    title: 'Коммерческий этап',
    sortable: false,
    render: (r) => <Badge value={r.commercial_stage} />,
  },
  { key: 'owner_name', label: 'Ответственный', title: 'Ответственный', sortable: false },
  {
    // Business remark of 31 October 2026: when the latest proposal of the request was issued.
    key: 'last_proposal_at',
    label: 'Последнее КП',
    title: 'Последнее КП — дата',
    render: (r) => (r.last_proposal_at ? date(r.last_proposal_at, true) : '—'),
  },
  {
    key: 'last_proposal_total',
    label: 'Сумма КП',
    title: 'Сумма последнего КП',
    sortable: false,
    render: (r) =>
      r.last_proposal_total
        ? `${decimal(r.last_proposal_total)} ${String(r.last_proposal_currency || '')}`.trim()
        : '—',
  },
  {
    key: 'wave_number',
    label: 'Волна',
    title: 'Волна поставки',
    sortable: false,
    render: (r) => String(r.wave_number || '—'),
  },
  {
    key: 'source',
    label: 'Источник',
    title: 'Источник заявки',
    sortable: false,
    render: (r) => String(r.source || '—'),
  },
  { key: 'due_at', label: 'Срок', title: 'Срок', render: (r) => date(r.due_at) },
  {
    key: 'sale_confirmed_at',
    label: 'Продажа',
    title: 'Продажа подтверждена',
    sortable: false,
    render: (r) => (r.sale_confirmed_at ? date(r.sale_confirmed_at) : '—'),
  },
  {
    key: 'closed_at',
    label: 'Закрыта',
    title: 'Дата закрытия',
    sortable: false,
    render: (r) => (r.closed_at ? date(r.closed_at) : '—'),
  },
  { key: 'created_at', label: 'Создана', title: 'Создана', render: (r) => date(r.created_at) },
];
const DEFAULT_COLUMNS = [
  'number',
  'client_name',
  'answered_suppliers',
  'commercial_stage',
  'owner_name',
  'last_proposal_at',
  'due_at',
  'created_at',
];
const KNOWN_COLUMNS = new Set(REQUEST_COLUMNS.map((column) => column.key));
function savedColumns(value: Record<string, unknown> | null | undefined): string[] | null {
  if (typeof value?.columns !== 'string') return null;
  const keys = value.columns
    .split(',')
    .filter((key, index, all) => KNOWN_COLUMNS.has(key) && all.indexOf(key) === index);
  // The number opens the request: it is always shown.
  return keys.length ? (keys.includes('number') ? keys : ['number', ...keys]) : null;
}

/** The «Столбцы» menu: show or hide a column and move it; every change is saved for the employee. */
function ColumnPicker({
  columns,
  onChange,
  onReset,
  status,
}: {
  columns: string[];
  onChange: (next: string[]) => void;
  onReset: () => void;
  status: 'idle' | 'saving' | 'saved';
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false);
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);
  // Shown columns first, in their order, then the hidden ones as listed.
  const ordered = [
    ...columns.map((key) => REQUEST_COLUMNS.find((column) => column.key === key)!),
    ...REQUEST_COLUMNS.filter((column) => !columns.includes(column.key)),
  ];
  function move(key: string, step: number) {
    const index = columns.indexOf(key);
    const target = index + step;
    if (index < 0 || target < 0 || target >= columns.length) return;
    const next = [...columns];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }
  return (
    <div className="column-picker" ref={root}>
      <button
        type="button"
        className="column-picker-toggle"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((value) => !value)}
      >
        <SlidersHorizontal size={16} /> Столбцы
      </button>
      {open && (
        <div className="column-picker-menu" role="group" aria-label="Столбцы списка заявок">
          <p>Отметьте столбцы и расставьте их стрелками. Настройка сохраняется для вас.</p>
          <ul>
            {ordered.map((column) => {
              const shown = columns.includes(column.key);
              const index = columns.indexOf(column.key);
              return (
                <li key={column.key} className={shown ? 'shown' : ''}>
                  <label>
                    <input
                      type="checkbox"
                      checked={shown}
                      disabled={column.key === 'number'}
                      onChange={(event) =>
                        onChange(
                          event.target.checked
                            ? [...columns, column.key]
                            : columns.filter((key) => key !== column.key),
                        )
                      }
                    />
                    {column.title}
                  </label>
                  {shown && (
                    <span className="column-picker-moves">
                      <button
                        type="button"
                        aria-label={`Выше: ${column.title}`}
                        disabled={index === 0}
                        onClick={() => move(column.key, -1)}
                      >
                        <ArrowUp size={14} />
                      </button>
                      <button
                        type="button"
                        aria-label={`Ниже: ${column.title}`}
                        disabled={index === columns.length - 1}
                        onClick={() => move(column.key, 1)}
                      >
                        <ArrowDown size={14} />
                      </button>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
          <footer>
            <span>
              {status === 'saving'
                ? 'Сохраняем…'
                : status === 'saved'
                  ? 'Сохранено'
                  : 'Настройка сохраняется сама'}
            </span>
            <button type="button" className="chip-button" onClick={onReset}>
              Как по умолчанию
            </button>
          </footer>
        </div>
      )}
    </div>
  );
}
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
  const [columns, setColumns] = useState<string[]>(DEFAULT_COLUMNS);
  const columnsChanged = useRef(false);
  const [columnsStatus, setColumnsStatus] = useState<'idle' | 'saving' | 'saved'>('idle');
  const [columnsError, setColumnsError] = useState<unknown>();
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
  useEffect(() => {
    let active = true;
    api<{ value: Record<string, unknown> | null }>(COLUMNS_PREFERENCE)
      .then((result) => {
        const saved = savedColumns(result.value);
        if (active && saved) setColumns(saved);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);
  // Saved a moment after the last change, so quick clicks in the menu all reach the server in one request.
  useEffect(() => {
    if (!columnsChanged.current) return;
    setColumnsStatus('saving');
    const timer = window.setTimeout(() => {
      const byDefault = columns.join(',') === DEFAULT_COLUMNS.join(',');
      api(
        COLUMNS_PREFERENCE,
        byDefault
          ? { method: 'DELETE' }
          : { method: 'PUT', body: { value: { columns: columns.join(',') } } },
      )
        .then(() => {
          setColumnsStatus('saved');
          setColumnsError(undefined);
        })
        .catch((error: unknown) => {
          setColumnsStatus('idle');
          setColumnsError(error);
        });
    }, 400);
    return () => window.clearTimeout(timer);
  }, [columns]);
  function changeColumns(next: string[]) {
    columnsChanged.current = true;
    setColumns(next);
  }
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
          {view === 'table' && (
            <ColumnPicker
              columns={columns}
              onChange={changeColumns}
              onReset={() => changeColumns(DEFAULT_COLUMNS)}
              status={columnsStatus}
            />
          )}
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
        <ErrorBox error={command.error || columnsError} />
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
            columns={columns
              .map((key) => REQUEST_COLUMNS.find((column) => column.key === key))
              .filter((column): column is RequestColumn => Boolean(column))}
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
