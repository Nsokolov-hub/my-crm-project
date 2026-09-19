import {
  AlertCircle,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Inbox,
  LoaderCircle,
  X,
} from 'lucide-react';
import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, errorMessage } from '../lib/api';
import { display } from '../lib/format';
import type { ReactNode } from 'react';
import type { Column, Entity } from '../lib/types';
export function Badge({ value, tone }: { value: unknown; tone?: string }) {
  const v = String(value || '');
  const color =
    tone ||
    ([
      'approved',
      'confirmed',
      'verified',
      'sale_confirmed',
      'completed',
      'paid',
      'delivered',
    ].includes(v)
      ? 'green'
      : ['rejected', 'closed_lost', 'cancelled', 'overdue'].includes(v)
        ? 'red'
        : ['pending', 'declared', 'awaiting_payment', 'requires_review', 'needs_review'].includes(v)
          ? 'amber'
          : ['new', 'draft', 'planned'].includes(v)
            ? 'gray'
            : 'purple');
  return (
    <span className={`badge ${color}`}>
      <i />
      {display(value)}
    </span>
  );
}
export function Button({
  children,
  variant = 'primary',
  busy,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  busy?: boolean;
}) {
  return (
    <button
      {...props}
      className={`button ${variant} ${props.className || ''}`}
      disabled={props.disabled || busy}
    >
      {busy && <LoaderCircle size={16} className="spin" />}
      {children}
    </button>
  );
}
export function PageHeading({
  eyebrow = 'Рабочее пространство',
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="heading-actions">{actions}</div>
    </div>
  );
}
export function Empty({
  title = 'Здесь пока нет записей',
  description = 'Добавьте первую запись, чтобы начать работу.',
  action,
  compact = false,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={`empty ${compact ? 'compact' : ''}`}>
      <div className="empty-icon">
        <Inbox size={26} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={24} />
      <span>Загружаем данные…</span>
    </div>
  );
}
export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  if (!error) return null;
  return (
    <div role="alert" className="error-box">
      <AlertCircle size={19} />
      <div>
        <strong>
          {error instanceof ApiError && error.status === 409
            ? 'Данные изменились'
            : 'Не удалось выполнить действие'}
        </strong>
        <p>{errorMessage(error)}</p>
        {error instanceof ApiError && error.requestId && (
          <small>Код обращения: {error.requestId}</small>
        )}
        {retry && (
          <Button variant="secondary" onClick={retry}>
            Повторить
          </Button>
        )}
      </div>
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal ${wide ? 'wide' : ''}`}
      aria-labelledby="modal-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <div className="modal-header">
        <h2 id="modal-title">{title}</h2>
        <button type="button" className="icon-button" aria-label="Закрыть" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function DataTable<T extends Entity>({
  rows,
  columns,
  onRow,
  empty,
  sort,
  onSort,
}: {
  rows: T[];
  columns: Column<NoInfer<T>>[];
  onRow?: (row: T) => void;
  empty?: ReactNode;
  sort?: string;
  onSort?: (key: string) => void;
}) {
  if (rows.length === 0) return empty || <Empty />;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key}>
                {onSort && col.sortable !== false ? (
                  <button onClick={() => onSort(col.key)}>
                    {col.label}
                    <span className={sort === col.key ? 'sorted' : ''}>↕</span>
                  </button>
                ) : (
                  col.label
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              {columns.map((col, index) => (
                <td key={col.key}>
                  {index === 0 && onRow ? (
                    <button className="row-link" onClick={() => onRow(row)}>
                      {col.render ? col.render(row) : display(row[col.key])}
                      <ArrowUpRight size={14} />
                    </button>
                  ) : col.render ? (
                    col.render(row)
                  ) : (
                    display(row[col.key])
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Pagination({
  page,
  total,
  pageSize = 25,
  onChange,
}: {
  page: number;
  total: number;
  pageSize?: number;
  onChange: (page: number) => void;
}) {
  return (
    <div className="pagination">
      <span>
        {total
          ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} из ${total}`
          : 'Нет записей'}
      </span>
      <div>
        <button
          className="icon-button"
          aria-label="Предыдущая страница"
          disabled={page === 1}
          onClick={() => onChange(page - 1)}
        >
          <ChevronLeft size={18} />
        </button>
        <span>Страница {page}</span>
        <button
          className="icon-button"
          aria-label="Следующая страница"
          disabled={page * pageSize >= total}
          onClick={() => onChange(page + 1)}
        >
          <ChevronRight size={18} />
        </button>
      </div>
    </div>
  );
}
export function Section({
  title,
  description,
  action,
  children,
  className = '',
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="section-heading">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
export function DetailPairs({ values }: { values: Record<string, unknown> }) {
  return (
    <dl className="detail-pairs">
      {Object.entries(values).map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{display(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
export function TextLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link className="text-link" to={to}>
      {children}
      <ArrowUpRight size={14} />
    </Link>
  );
}
