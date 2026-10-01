import { Bookmark, Plus, Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
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
  action?: ReactNode;
  extra?: Record<string, unknown>;
  command?: boolean;
  onSelect?: (row: Entity) => void;
  description?: string;
  refreshKey?: number;
  query?: string;
  canCreate?: boolean;
  onChanged?: () => void;
  transform?: (values: Record<string, unknown>) => Record<string, unknown>;
  deriveValues?: (values: Record<string, unknown>, changedField: string) => Record<string, unknown>;
  pageSize?: number;
  filterKeys?: string[];
  selection?: {
    selectedIds: Set<string>;
    onToggle: (id: string) => void;
    onSelectPage: (ids: string[], checked: boolean) => void;
  };
};
export function Collection({
  title,
  endpoint,
  columns,
  fields,
  createLabel = 'Добавить',
  action,
  extra,
  command,
  onSelect,
  description,
  query = '',
  refreshKey = 0,
  canCreate = true,
  transform,
  deriveValues,
  onChanged,
  pageSize = 25,
  filterKeys = [],
  selection,
}: CollectionProps) {
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('created_at');
  const [direction, setDirection] = useState('desc');
  const [creating, setCreating] = useState(false);
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({});
  const searchInput = useMemo(() => ({ q, columnFilters }), [q, columnFilters]);
  const appliedSearch = useDebounced(searchInput, filterKeys.length > 0 ? 1000 : 300);
  const searchPending = searchInput !== appliedSearch;
  const filterQuery = filterKeys
    .map((key) => `&filter_${key}=${encodeURIComponent(appliedSearch.columnFilters[key] || '')}`)
    .join('');
  const data = useApi<Page>(
    searchPending
      ? null
      : `${endpoint}${endpoint.includes('?') ? '&' : '?'}page=${page}&page_size=${pageSize}&q=${encodeURIComponent(appliedSearch.q)}&sort=${sort}&direction=${direction}&ui_revision=${refreshKey}${query}${filterQuery}`,
  );
  const rows = data.data?.items || [];
  const visibleIds = rows.map((row) => row.id);
  const selectedOnPage = visibleIds.filter((id) => selection?.selectedIds.has(id)).length;
  const visibleColumns: Column[] = selection
    ? [
        {
          key: '__selection',
          label: (
            <input
              type="checkbox"
              aria-label="Выбрать все строки на странице"
              title="Выбрать все строки на странице"
              checked={visibleIds.length > 0 && selectedOnPage === visibleIds.length}
              ref={(element) => {
                if (element)
                  element.indeterminate = selectedOnPage > 0 && selectedOnPage < visibleIds.length;
              }}
              onChange={(event) => selection.onSelectPage(visibleIds, event.target.checked)}
            />
          ),
          render: (row) => (
            <input
              type="checkbox"
              aria-label={`Выбрать позицию ${String(row.name || row.nomenclature_name || row.description || row.id)}`}
              checked={selection.selectedIds.has(row.id)}
              onChange={() => selection.onToggle(row.id)}
            />
          ),
          sortable: false,
        },
        ...columns,
      ]
    : columns;
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
        action || (fields && canCreate) ? (
          <div className="inline-actions">
            {action}
            {fields && canCreate && (
              <Button onClick={() => setCreating(true)}>
                <Plus size={16} /> {createLabel}
              </Button>
            )}
          </div>
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
          rows={rows}
          columns={visibleColumns}
          onRow={onSelect}
          rowClassName={(row) => (selection?.selectedIds.has(row.id) ? 'selected-table-row' : '')}
          sort={sort}
          columnFilters={columnFilters}
          filterKeys={filterKeys}
          onFilter={(key, value) => {
            setColumnFilters((current) => ({ ...current, [key]: value }));
            setPage(1);
          }}
          onSort={(key) => {
            setSort(key);
            setDirection((v) => (v === 'asc' ? 'desc' : 'asc'));
          }}
        />
      )}
      <Pagination
        page={page}
        total={data.data?.total ?? data.data?.items.length ?? 0}
        pageSize={pageSize}
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
          deriveValues={deriveValues}
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
