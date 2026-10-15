import { Plus, X } from 'lucide-react';
import { useState } from 'react';
import { useAuth } from '../app/Auth';
import { RecordForm } from '../components/Form';
import {
  Badge,
  Button,
  DataTable,
  ErrorBox,
  Loading,
  PageHeading,
  Pagination,
  Section,
} from '../components/ui';
import { taskFields } from '../lib/fields';
import { date } from '../lib/format';
import { useApi, useDebounced } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';

type TaskPage = Page & { groups?: { key: string; label: string; count: number }[] };
type View = {
  q: string;
  due: string;
  assignee: string;
  status: string;
  priority: string;
  group: string;
};

const PAGE_SIZE = 50;
const VIEW_KEY = 'tasks:view';
const DEFAULT_VIEW: View = { q: '', due: '', assignee: '', status: '', priority: '', group: 'due' };
const statusLabels: Record<string, string> = {
  assigned: 'Назначена',
  in_progress: 'В работе',
  completed: 'Завершена',
  cancelled: 'Отменена',
};
const editFields: Field[] = [
  { name: 'title', label: 'Название', required: true },
  {
    name: 'status',
    label: 'Статус',
    required: true,
    type: 'select',
    options: Object.entries(statusLabels).map(([value, label]) => ({ value, label })),
  },
  { name: 'due_at', label: 'Срок', type: 'datetime-local', required: true },
  {
    name: 'result',
    label: 'Результат / причина отмены',
    type: 'textarea',
    help: 'Обязательно при завершении и отмене задачи.',
  },
];

function initialView(): View {
  let saved: Partial<View> = {};
  try {
    saved = JSON.parse(localStorage.getItem(VIEW_KEY) || '{}') as Partial<View>;
  } catch {
    saved = {};
  }
  const view = { ...DEFAULT_VIEW, ...saved, q: '' };
  // A link such as «Просроченные задачи» on the dashboard opens the matching filter.
  if (new URLSearchParams(window.location.search).get('overdue') === 'true') view.due = 'overdue';
  return view;
}

export function Tasks() {
  const auth = useAuth();
  const [view, setView] = useState<View>(initialView);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Entity>();
  const [creating, setCreating] = useState(false);
  const search = useDebounced(view.q, 300);
  const users = useApi<Page>('/users');
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(PAGE_SIZE),
    q: search,
    ...(view.due ? { due: view.due } : {}),
    ...(view.assignee ? { assignee_id: view.assignee } : {}),
    ...(view.status ? { status: view.status } : {}),
    ...(view.priority ? { priority: view.priority } : {}),
    ...(view.group ? { group: view.group } : {}),
  });
  const list = useApi<TaskPage>(`/tasks?${params.toString()}`);
  const groups = Object.fromEntries((list.data?.groups || []).map((group) => [group.key, group]));
  const filtered = Boolean(view.q || view.due || view.assignee || view.status || view.priority);
  const me = auth.session?.user.id || '';

  function change(next: Partial<View>) {
    setView((current) => {
      const value = { ...current, ...next };
      try {
        localStorage.setItem(VIEW_KEY, JSON.stringify({ ...value, q: '' }));
      } catch {
        /* Фильтры просто не запомнятся. */
      }
      return value;
    });
    setPage(1);
  }
  function refresh() {
    list.refresh();
  }

  return (
    <>
      <PageHeading
        title="Задачи"
        description="Договорённости, следующие контакты и сроки команды."
      />
      <Section
        title="Все задачи"
        action={
          <Button onClick={() => setCreating(true)}>
            <Plus size={16} /> Создать задачу
          </Button>
        }
      >
        <div className="task-filters" role="search">
          <div className="task-filter task-filter-wide">
            <span>Задача</span>
            <input
              aria-label="Поиск по названию задачи"
              placeholder="Название задачи…"
              value={view.q}
              onChange={(event) => change({ q: event.target.value })}
            />
          </div>
          <div className="task-filter">
            <span>Срок</span>
            <select
              aria-label="Период выполнения"
              value={view.due}
              onChange={(event) => change({ due: event.target.value })}
            >
              <option value="">Любой</option>
              <option value="overdue">Просрочено</option>
              <option value="today">Сегодня</option>
              <option value="tomorrow">Завтра</option>
              <option value="week">На этой неделе</option>
              <option value="later">Позже</option>
              <option value="closed">Завершены и отменены</option>
            </select>
          </div>
          <div className="task-filter">
            <span>Исполнитель</span>
            <select
              aria-label="Фильтр по исполнителю"
              value={view.assignee}
              onChange={(event) => change({ assignee: event.target.value })}
            >
              <option value="">Все</option>
              {me && <option value={me}>Мои задачи</option>}
              {(users.data?.items || [])
                .filter((user) => user.id !== me)
                .map((user) => (
                  <option key={user.id} value={user.id}>
                    {String(user.name)}
                  </option>
                ))}
            </select>
          </div>
          <div className="task-filter">
            <span>Состояние</span>
            <select
              aria-label="Фильтр по состоянию"
              value={view.status}
              onChange={(event) => change({ status: event.target.value })}
            >
              <option value="">Все</option>
              <option value="active">Открытые</option>
              {Object.entries(statusLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="task-filter">
            <span>Приоритет</span>
            <select
              aria-label="Фильтр по приоритету"
              value={view.priority}
              onChange={(event) => change({ priority: event.target.value })}
            >
              <option value="">Любой</option>
              <option value="high">Высокий</option>
              <option value="normal">Обычный</option>
              <option value="low">Низкий</option>
            </select>
          </div>
          <div className="task-filter">
            <span>Группировка</span>
            <select
              aria-label="Группировка задач"
              value={view.group}
              onChange={(event) => change({ group: event.target.value })}
            >
              <option value="due">По сроку</option>
              <option value="assignee">По исполнителю</option>
              <option value="client">По клиенту</option>
              <option value="status">По состоянию</option>
              <option value="priority">По приоритету</option>
              <option value="">Без группировки</option>
            </select>
          </div>
          {filtered && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => change({ ...DEFAULT_VIEW, group: view.group })}
            >
              <X size={15} /> Сбросить
            </Button>
          )}
        </div>
        {list.loading ? (
          <Loading />
        ) : list.error ? (
          <ErrorBox error={list.error} retry={list.refresh} />
        ) : (
          <DataTable
            className="task-table"
            rows={list.data?.items || []}
            onRow={setSelected}
            group={
              view.group
                ? (row) => {
                    const group = groups[String(row.group)];
                    return {
                      key: String(row.group),
                      label: (
                        <span className="task-group-heading">
                          {group?.label || '—'}
                          <small>{group?.count ?? ''}</small>
                        </span>
                      ),
                    };
                  }
                : undefined
            }
            columns={[
              { key: 'title', label: 'Задача', sortable: false },
              {
                key: 'client_name',
                label: 'Клиент',
                render: (r) =>
                  r.client_name ? (
                    <span className="stacked">
                      <span>{String(r.client_name)}</span>
                      {Boolean(r.request_number) && (
                        <small>Заявка {String(r.request_number)}</small>
                      )}
                    </span>
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
                  <span className={r.due_group === 'overdue' ? 'overdue' : ''}>
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
        )}
        <Pagination
          page={page}
          total={list.data?.total ?? 0}
          pageSize={PAGE_SIZE}
          onChange={setPage}
        />
      </Section>
      {creating && (
        <RecordForm
          title="Создать задачу"
          fields={taskFields}
          endpoint="/tasks"
          onClose={() => setCreating(false)}
          onSuccess={() => {
            setCreating(false);
            refresh();
          }}
        />
      )}
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
            refresh();
          }}
        />
      )}
    </>
  );
}
