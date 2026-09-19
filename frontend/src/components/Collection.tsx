import { Bookmark, Plus, Search } from 'lucide-react';
import { useState } from 'react';
import { useApi, useDebounced } from '../lib/hooks';
import type { Column, Entity, Field, Page } from '../lib/types';
import { RecordForm } from './Form';
import { Button, DataTable, ErrorBox, Loading, Pagination, Section } from './ui';
export type CollectionProps = {
  title: string;
  endpoint: string;
  columns: Column[];
  fields?: Field[];
  createLabel?: string;
  extra?: Record<string, unknown>;
  command?: boolean;
  onSelect?: (row: Entity) => void;
  description?: string;
  refreshKey?: number;
  query?: string;
  canCreate?: boolean;
  onChanged?: () => void;
  transform?: (values: Record<string, unknown>) => Record<string, unknown>;
};
export function Collection({
  title,
  endpoint,
  columns,
  fields,
  createLabel = 'Добавить',
  extra,
  command,
  onSelect,
  description,
  query = '',
  refreshKey = 0,
  canCreate = true,
  transform,
  onChanged,
}: CollectionProps) {
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('created_at');
  const [direction, setDirection] = useState('desc');
  const [creating, setCreating] = useState(false);
  const debounced = useDebounced(q);
  const data = useApi<Page>(
    `${endpoint}${endpoint.includes('?') ? '&' : '?'}page=${page}&page_size=25&q=${encodeURIComponent(debounced)}&sort=${sort}&direction=${direction}&ui_revision=${refreshKey}${query}`,
  );
  const [saved, setSaved] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(`filters:${endpoint}`) || '[]');
    } catch {
      return [];
    }
  });
  function saveFilter() {
    if (!q) return;
    const next = [...new Set([...saved, q])].slice(-8);
    setSaved(next);
    localStorage.setItem(`filters:${endpoint}`, JSON.stringify(next));
  }
  return (
    <Section
      title={title}
      description={description}
      action={
        fields && canCreate ? (
          <Button onClick={() => setCreating(true)}>
            <Plus size={16} />
            {createLabel}
          </Button>
        ) : undefined
      }
    >
      <div className="collection-toolbar">
        <div className="search-input">
          <Search size={17} />
          <input
            aria-label={`Поиск: ${title}`}
            placeholder="Найти по названию или номеру…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <button
          className="icon-button"
          aria-label="Сохранить фильтр"
          title="Сохранить текущий поисковый запрос"
          onClick={saveFilter}
          disabled={!q}
        >
          <Bookmark size={18} />
        </button>
        {saved.length > 0 && (
          <select
            className="compact-select"
            aria-label="Сохранённые фильтры"
            value=""
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Сохранённые фильтры</option>
            {saved.map((filter) => (
              <option key={filter}>{filter}</option>
            ))}
          </select>
        )}
      </div>
      {data.loading ? (
        <Loading />
      ) : data.error ? (
        <ErrorBox error={data.error} retry={data.refresh} />
      ) : (
        <DataTable
          rows={data.data?.items || []}
          columns={columns}
          onRow={onSelect}
          sort={sort}
          onSort={(key) => {
            setSort(key);
            setDirection((v) => (v === 'asc' ? 'desc' : 'asc'));
          }}
        />
      )}
      <Pagination
        page={page}
        total={data.data?.total ?? data.data?.items.length ?? 0}
        onChange={setPage}
      />
      {creating && fields && (
        <RecordForm
          title={createLabel}
          fields={fields}
          endpoint={endpoint}
          extra={extra}
          command={command}
          transform={transform}
          onClose={() => setCreating(false)}
          onSuccess={() => {
            setCreating(false);
            data.refresh();
            onChanged?.();
          }}
        />
      )}
    </Section>
  );
}
