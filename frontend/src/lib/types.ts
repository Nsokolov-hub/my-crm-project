export type Entity = { id: string; version?: number; created_at?: string; [key: string]: unknown };
export type Page<T = Entity> = { items: T[]; total?: number; page?: number; page_size?: number; unread?: number };
export type User = Entity & { name: string; email: string; permissions?: Record<string, string> };
export type Grant = { code: string; scope: string; allow: boolean };
export type Session = { user: User; csrf_token: string; permissions?: Record<string, string> };
export type RequestEntity = Entity & {
  title: string;
  number?: string;
  client_id: string;
  commercial_stage: string;
  owner_id?: string;
  due_at?: string;
};
export type Option = { value: string; label: string };
export type Field = {
  name: string;
  label: string;
  type?:
    | 'text'
    | 'email'
    | 'password'
    | 'textarea'
    | 'date'
    | 'datetime-local'
    | 'select'
    | 'multiselect'
    | 'decimal'
    | 'number'
    | 'checkbox'
    | 'json';
  required?: boolean;
  placeholder?: string;
  help?: string;
  options?: Option[];
  source?: string;
  labelKey?: string;
  value?: unknown;
  minLength?: number;
  wide?: boolean;
};
export type Column<T = Entity> = {
  key: string;
  label: string;
  render?: (row: T) => React.ReactNode;
  sortable?: boolean;
};
