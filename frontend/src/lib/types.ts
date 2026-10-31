import type { components } from './api-schema';
export type Entity = { id: string; version?: number; created_at?: string; [key: string]: unknown };
export type Page<T = Entity> = {
  items: T[];
  total?: number;
  page?: number;
  page_size?: number;
  unread?: number;
};
export type User = Entity & { name: string; email: string; permissions?: Record<string, string> };
export type Grant = { code: string; scope: string; allow: boolean };
/** The signed-in user, as typed by the API contract (docs/openapi.json → api-schema.d.ts). */
export type Session = components['schemas']['SessionView'];
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
    | 'json'
    | 'tel'
    | 'week'
    | 'counterparty-details';
  required?: boolean;
  placeholder?: string;
  help?: string;
  options?: Option[];
  source?: string;
  create?: { title: string; endpoint: string; fields: Field[] };
  labelKey?: string;
  value?: unknown;
  minLength?: number;
  min?: string | number;
  max?: string | number;
  wide?: boolean;
  /** For a 'week' field: the field of its ISO year, set together with the week from the calendar. */
  yearField?: string;
};
export type Column<T = Entity> = {
  key: string;
  label: React.ReactNode;
  render?: (row: T) => React.ReactNode;
  sortable?: boolean;
};
