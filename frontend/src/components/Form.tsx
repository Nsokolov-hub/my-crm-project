import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { z } from 'zod';
import { ApiError, api } from '../lib/api';
import { label } from '../lib/format';
import { useApi, useCommand, useDebounced, useDirtyProtection } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';
import { Button, ErrorBox, Modal } from './ui';
import { CounterpartyDetailsEditor } from './CounterpartyDetailsEditor';
import { WeekPicker } from './WeekPicker';
import '../styles/forms-v2.scss';
/**
 * Business remark of 31 October 2026: phone numbers with 10–15 digits and their city or country code; several
 * numbers separated by «,» or «;», an extension as «доб. 12». The same rule as the API's.
 */
// Digits of a whole international number where a country has one length (as in the API, longest codes first).
const PHONE_LENGTHS: Record<string, number> = {
  '375': 12,
  '374': 11,
  '373': 11,
  '380': 12,
  '992': 12,
  '993': 11,
  '994': 12,
  '995': 12,
  '996': 12,
  '998': 12,
  '7': 11,
  '1': 11,
};
export function phoneError(value: string): string | undefined {
  const message =
    'Укажите телефон с кодом города или страны: 10–15 цифр, например +7 495 123-45-67. Добавочный — «доб. 123», несколько номеров — через запятую';
  for (const part of value.split(/[,;]/)) {
    const number = part.trim().replace(/\s*(?:доб\.?|вн\.?|ext\.?|x|#)\s*\d{1,6}\s*$/i, '');
    if (!number || !/^\+?[\d\s().-]+$/.test(number)) return message;
    const digits = number.replace(/\D/g, '');
    if (digits.length < 10 || digits.length > 15 || new Set(digits).size === 1) return message;
    const code = number.startsWith('+')
      ? Object.keys(PHONE_LENGTHS).find((prefix) => digits.startsWith(prefix))
      : undefined;
    if (code && digits.length !== PHONE_LENGTHS[code]) return message;
  }
  return undefined;
}
export function validateFields(fields: Field[], values: Record<string, unknown>) {
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const value = values[field.name];
    if (
      field.required &&
      (value === undefined ||
        value === null ||
        value === '' ||
        (Array.isArray(value) && !value.length))
    ) {
      errors[field.name] = 'Заполните это поле';
      continue;
    }
    if (value === undefined || value === '') continue;
    if (
      field.type === 'date' &&
      ((field.min !== undefined && String(value) < String(field.min)) ||
        (field.max !== undefined && String(value) > String(field.max)))
    )
      errors[field.name] = 'Выберите дату в допустимом диапазоне';
    if (field.type === 'email' && !z.email().safeParse(String(value).trim()).success)
      errors[field.name] = 'Укажите корректную электронную почту, например name@company.ru';
    if (field.type === 'tel' && typeof value === 'string' && value.trim()) {
      const message = phoneError(value);
      if (message) errors[field.name] = message;
    }
    if (
      field.type === 'week' &&
      (!Number.isInteger(Number(value)) || Number(value) < 1 || Number(value) > 53)
    )
      errors[field.name] = 'Номер недели ISO: от 1 до 53';
    if (field.type === 'decimal' && !/^\d+(?:\.\d{1,8})?$/.test(String(value)))
      errors[field.name] = 'Введите неотрицательное число, до 8 знаков после точки';
    if (field.minLength && String(value).length < field.minLength)
      errors[field.name] = `Минимум ${field.minLength} символов`;
    if (field.type === 'json' && typeof value === 'string')
      try {
        JSON.parse(value);
      } catch {
        errors[field.name] = 'Проверьте формат JSON: кавычки, запятые и скобки';
      }
  }
  return errors;
}
export function DirectorySelect({
  field,
  value,
  error,
  onChange,
}: {
  field: Field;
  value: unknown;
  error?: string;
  onChange: (value: unknown) => void;
}) {
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<Entity[]>([]);
  const [selected, setSelected] = useState<{ id: string; label: string }>();
  const debounced = useDebounced(search, 250);
  const sourcePath = field.source
    ? `${field.source}${field.source.includes('?') ? '&' : '?'}page_size=100${debounced ? `&q=${encodeURIComponent(debounced)}` : ''}`
    : null;
  const source = useApi<Page>(sourcePath);
  const selectedId = String(value ?? '');
  const rows = useMemo(
    () => [...created, ...(source.data?.items || [])],
    [created, source.data?.items],
  );
  const options = rows
    .filter((row, index) => rows.findIndex((candidate) => candidate.id === row.id) === index)
    .map((row) => ({
      value: row.id,
      label: field.labelKey ? String(row[field.labelKey] || label(row)) : label(row),
    }))
    .filter((option) => option.label.toLocaleLowerCase().includes(search.toLocaleLowerCase()));

  useEffect(() => {
    if (!selectedId || selected?.id === selectedId) return;
    const found = rows.find((row) => row.id === selectedId);
    if (found) {
      setSelected({
        id: found.id,
        label: field.labelKey ? String(found[field.labelKey] || label(found)) : label(found),
      });
      return;
    }
    if (source.loading || !field.source) return;
    const controller = new AbortController();
    const base = field.source.split('?')[0];
    void api<Entity>(`${base}/${encodeURIComponent(selectedId)}`, { signal: controller.signal })
      .then((row) => {
        setSelected({
          id: row.id,
          label: field.labelKey ? String(row[field.labelKey] || label(row)) : label(row),
        });
      })
      .catch(() => {
        if (!controller.signal.aborted) setSelected({ id: selectedId, label: 'Выбрано' });
      });
    return () => controller.abort();
  }, [selectedId, selected?.id, source.loading, field.source, field.labelKey, rows]);

  function choose(id: string, optionLabel: string) {
    setSelected({ id, label: optionLabel });
    setSearch('');
    setOpen(false);
    onChange(id);
  }

  return (
    <div
      className="directory-select"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <input
        id={`field-${field.name}`}
        name={`${field.name}-search`}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={`options-${field.name}`}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `error-${field.name}` : undefined}
        placeholder="Найти в справочнике…"
        value={open ? search : selected?.id === selectedId ? selected.label : ''}
        onFocus={() => {
          setSearch('');
          setOpen(true);
        }}
        onChange={(event) => {
          if (selectedId) onChange('');
          setSearch(event.target.value);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false);
          if (event.key === 'Enter' && open && options.length === 1) {
            event.preventDefault();
            choose(options[0].value, options[0].label);
          }
        }}
      />
      {open && (
        <div className="directory-options" id={`options-${field.name}`} role="listbox">
          {source.loading && <p>Загрузка…</p>}
          {options.map((option) => (
            <button
              type="button"
              key={option.value}
              role="option"
              aria-selected={option.value === selectedId}
              onClick={() => choose(option.value, option.label)}
            >
              {option.label}
            </button>
          ))}
          {!source.loading && !options.length && <p>Совпадений нет</p>}
          {field.create && (
            <button
              type="button"
              className="directory-create"
              onClick={() => {
                setOpen(false);
                setCreating(true);
              }}
            >
              + Создать {field.label.toLocaleLowerCase()}
            </button>
          )}
        </div>
      )}
      {Boolean(source.error) && (
        <p className="field-error">
          {source.error instanceof ApiError
            ? source.error.message
            : 'Не удалось загрузить справочник.'}
        </p>
      )}
      {creating &&
        field.create &&
        createPortal(
          <RecordForm
            title={field.create.title}
            endpoint={field.create.endpoint}
            fields={field.create.fields}
            onClose={() => setCreating(false)}
            onSuccess={(row) => {
              setCreated((current) => [row, ...current]);
              choose(
                row.id,
                field.labelKey ? String(row[field.labelKey] || label(row)) : label(row),
              );
              setCreating(false);
            }}
          />,
          document.body,
        )}
    </div>
  );
}
function FieldControl({
  field,
  value,
  error,
  onChange,
  values = {},
  onChangeMany,
}: {
  field: Field;
  value: unknown;
  error?: string;
  onChange: (value: unknown) => void;
  values?: Record<string, unknown>;
  onChangeMany?: (patch: Record<string, unknown>) => void;
}) {
  const source = useApi<Page>(
    field.source && field.type !== 'select'
      ? `${field.source}${field.source.includes('?') ? '&' : '?'}page_size=100`
      : null,
  );
  const options =
    field.options ||
    source.data?.items.map((row) => ({
      value: row.id,
      label: field.labelKey ? String(row[field.labelKey] || label(row)) : label(row),
    })) ||
    [];
  const common = {
    id: `field-${field.name}`,
    name: field.name,
    'aria-invalid': Boolean(error),
    'aria-describedby': error ? `error-${field.name}` : undefined,
    required: field.required,
  };
  return (
    <div
      className={`field ${field.wide || field.type === 'textarea' || field.type === 'json' || field.type === 'counterparty-details' ? 'wide' : ''} ${field.type === 'checkbox' ? 'checkbox-field' : ''}`}
    >
      {field.type === 'counterparty-details' ? (
        <div className="field-group-title">{field.label}</div>
      ) : (
        <label htmlFor={common.id}>
          {field.label}
          {field.required && <span> *</span>}
        </label>
      )}
      {field.type === 'checkbox' ? (
        <input
          {...common}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
      ) : field.type === 'counterparty-details' ? (
        <CounterpartyDetailsEditor kind={field.placeholder} value={value} onChange={onChange} />
      ) : field.type === 'week' ? (
        <WeekPicker
          id={common.id}
          week={value}
          year={field.yearField ? values[field.yearField] : undefined}
          invalid={Boolean(error)}
          describedBy={common['aria-describedby']}
          onWeek={(next) => onChange(next)}
          onPick={(week, year) =>
            onChangeMany
              ? onChangeMany({
                  [field.name]: week,
                  ...(field.yearField ? { [field.yearField]: year } : {}),
                })
              : onChange(week)
          }
        />
      ) : field.type === 'select' && field.source ? (
        <DirectorySelect field={field} value={value} error={error} onChange={onChange} />
      ) : field.type === 'select' || field.type === 'multiselect' ? (
        <select
          {...common}
          multiple={field.type === 'multiselect'}
          value={
            field.type === 'multiselect'
              ? Array.isArray(value)
                ? value.map(String)
                : []
              : String(value ?? '')
          }
          onChange={(e) =>
            onChange(
              field.type === 'multiselect'
                ? Array.from(e.target.selectedOptions, (v) => v.value)
                : e.target.value,
            )
          }
        >
          <option value="" disabled={field.type === 'multiselect'}>
            {source.loading ? 'Загрузка…' : 'Выберите…'}
          </option>
          {options.map((option) => (
            <option value={option.value} key={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : field.type === 'textarea' || field.type === 'json' ? (
        <textarea
          {...common}
          rows={field.type === 'json' ? 9 : 3}
          spellCheck={field.type !== 'json'}
          className={field.type === 'json' ? 'code-input' : ''}
          value={typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value ?? '')}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          {...common}
          type={field.type === 'decimal' ? 'text' : field.type || 'text'}
          min={field.min}
          max={field.max}
          inputMode={
            field.type === 'decimal' ? 'decimal' : field.type === 'tel' ? 'tel' : undefined
          }
          autoComplete={field.type === 'password' ? 'new-password' : undefined}
          placeholder={field.placeholder ?? (field.type === 'tel' ? '+7 495 123-45-67' : undefined)}
          value={String(value ?? '')}
          onChange={(e) =>
            onChange(field.type === 'decimal' ? e.target.value.replace(',', '.') : e.target.value)
          }
        />
      )}{' '}
      {field.help && <small>{field.help}</small>}
      {error && (
        <p className="field-error" id={`error-${field.name}`}>
          {error}
        </p>
      )}
      {Boolean(source.error) && (
        <p className="field-error">
          {source.error instanceof ApiError
            ? source.error.message
            : 'Справочник недоступен. Проверьте права или повторно откройте форму.'}
        </p>
      )}
    </div>
  );
}
export function RecordForm({
  title,
  fields,
  endpoint,
  method = 'POST',
  initial = {},
  extra = {},
  command = false,
  onClose,
  onSuccess,
  transform,
  validate,
  deriveValues,
  submitLabel = 'Сохранить',
  note,
}: {
  title: string;
  fields: Field[];
  endpoint: string;
  method?: string;
  initial?: Record<string, unknown>;
  extra?: Record<string, unknown>;
  command?: boolean;
  onClose: () => void;
  onSuccess: (result: Entity) => void;
  transform?: (values: Record<string, unknown>) => Record<string, unknown>;
  /** Checks that depend on several fields; returns messages by field name. */
  validate?: (values: Record<string, unknown>) => Record<string, string>;
  deriveValues?: (values: Record<string, unknown>, changedField: string) => Record<string, unknown>;
  submitLabel?: string;
  note?: React.ReactNode;
}) {
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(
      fields.map((field) => {
        let val =
          initial[field.name] ??
          field.value ??
          (field.type === 'checkbox' ? false : field.type === 'multiselect' ? [] : '');
        // Convert ISO UTC strings to local datetime-local format for editing
        if (field.type === 'datetime-local' && typeof val === 'string' && val) {
          const d = new Date(val);
          if (!Number.isNaN(d.getTime())) {
            val = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
          }
        }
        return [field.name, val];
      }),
    ),
  );
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const operation = useCommand();
  useDirtyProtection(dirty);
  function close() {
    if (operation.busy) return;
    if (!dirty || window.confirm('Есть несохранённые изменения. Закрыть форму без сохранения?'))
      onClose();
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    const validation = { ...(validate ? validate(values) : {}), ...validateFields(fields, values) };
    setErrors(validation);
    if (Object.keys(validation).length) {
      document.getElementById(`field-${Object.keys(validation)[0]}`)?.focus();
      return;
    }
    const body: Record<string, unknown> = { ...extra };
    for (const field of fields) {
      const value = values[field.name];
      if (value === '' && !field.required) continue;
      body[field.name] =
        field.type === 'json' && typeof value === 'string'
          ? JSON.parse(value)
          : field.type === 'number' || field.type === 'week'
            ? Number(value)
            : field.type === 'datetime-local' && value
              ? (() => {
                  // datetime-local inputs produce 'YYYY-MM-DDTHH:MM' (no timezone).
                  // We must interpret this as user's local time, not UTC.
                  const local = new Date(String(value));
                  // Guard: if the Date is invalid, send the raw string
                  if (Number.isNaN(local.getTime())) return value;
                  return local.toISOString();
                })()
              : value;
    }
    try {
      const result = await operation.run<Entity>(
        endpoint,
        transform ? transform(body) : body,
        method,
        command,
      );
      if (result) {
        setDirty(false);
        onSuccess(result);
      }
    } catch (error) {
      if (error instanceof ApiError && error.field) setErrors({ [error.field]: error.message });
    }
  }
  return (
    <Modal title={title} onClose={close} wide={fields.length > 5}>
      <form noValidate onSubmit={submit}>
        <div className="form-body">
          {note && <div className="info-note">{note}</div>}
          <ErrorBox error={operation.error} />
          <div className="form-grid">
            {fields.map((field) => (
              <FieldControl
                key={field.name}
                field={
                  field.name === 'entity_id'
                    ? {
                        ...field,
                        label: 'Выберите связанный объект',
                        type: 'select',
                        required: Boolean(values.entity_type),
                        source: (
                          {
                            request: '/requests',
                            counterparty: '/counterparties',
                            wave: '/waves',
                          } as Record<string, string>
                        )[String(values.entity_type || '')],
                      }
                    : field.type === 'counterparty-details'
                      ? { ...field, placeholder: String(values.kind || 'client') }
                      : field
                }
                value={values[field.name]}
                error={errors[field.name]}
                values={values}
                onChangeMany={(patch) => {
                  setValues((v) => {
                    let next = { ...v, ...patch };
                    for (const name of Object.keys(patch))
                      next = deriveValues ? deriveValues(next, name) : next;
                    return next;
                  });
                  setDirty(true);
                  setErrors((e) => ({
                    ...e,
                    ...Object.fromEntries(Object.keys(patch).map((k) => [k, ''])),
                  }));
                }}
                onChange={(value) => {
                  setValues((v) => {
                    const next = { ...v, [field.name]: value };
                    if (field.name === 'entity_type') next.entity_id = null;
                    return deriveValues ? deriveValues(next, field.name) : next;
                  });
                  setDirty(true);
                  setErrors((e) => ({ ...e, [field.name]: '' }));
                }}
              />
            ))}
          </div>
        </div>
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={close}>
            Отмена
          </Button>
          <Button type="submit" busy={operation.busy}>
            {submitLabel}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
