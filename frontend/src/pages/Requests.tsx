import { Columns3, List, Plus, Search } from 'lucide-react';
import { useState } from 'react';
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
import { requestFields } from '../lib/fields';
import { date, stages, stageLabels } from '../lib/format';
import { useApi, useDebounced } from '../lib/hooks';
import type { Page, RequestEntity } from '../lib/types';
export function Requests() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<'table' | 'board'>('table');
  const [creating, setCreating] = useState(params.get('new') === '1');
  const [q, setQ] = useState(params.get('q') || '');
  const [stage, setStage] = useState(params.get('stage') || '');
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('created_at');
  const [direction, setDirection] = useState('desc');
  const debounced = useDebounced(q);
  const list = useApi<Page<RequestEntity>>(
    `/requests?page=${page}&page_size=${view === 'board' ? 100 : 25}&q=${encodeURIComponent(debounced)}&stage=${stage}&sort=${sort}&direction=${direction}${params.get('client_id') ? '&client_id=' + params.get('client_id') : ''}`,
  );
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
              onChange={(e) => {
                setQ(e.target.value);
                setPage(1);
              }}
            />
          </div>
          <select
            aria-label="Коммерческий этап"
            value={stage}
            onChange={(e) => {
              setStage(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Все этапы</option>
            {stages.map((s) => (
              <option key={s} value={s}>
                {stageLabels[s]}
              </option>
            ))}
          </select>
          <div className="segmented">
            <button
              className={view === 'table' ? 'active' : ''}
              onClick={() => {
                setView('table');
                setPage(1);
              }}
              aria-label="Таблица"
            >
              <List size={18} />
            </button>
            <button
              className={view === 'board' ? 'active' : ''}
              onClick={() => {
                setView('board');
                setPage(1);
              }}
              aria-label="Доска этапов"
            >
              <Columns3 size={18} />
            </button>
          </div>
        </div>
        {list.loading ? (
          <Loading />
        ) : list.error ? (
          <ErrorBox error={list.error} retry={list.refresh} />
        ) : view === 'table' ? (
          <DataTable
            rows={list.data?.items || []}
            onRow={(r) => navigate(`/requests/${r.id}`)}
            sort={sort}
            onSort={(key) => {
              setSort(key);
              setDirection((v) => (v === 'asc' ? 'desc' : 'asc'));
            }}
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
                key: 'commercial_stage',
                label: 'Коммерческий этап',
                render: (r) => <Badge value={r.commercial_stage} />,
              },
              { key: 'owner_name', label: 'Ответственный' },
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
