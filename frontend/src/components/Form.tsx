import { useState } from 'react';
import { z } from 'zod';
import { ApiError } from '../lib/api';
import { label } from '../lib/format';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';
import { Button, ErrorBox, Modal } from './ui';
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
    if (field.type === 'email' && !z.email().safeParse(value).success)
      errors[field.name] = 'Укажите корректную электронную почту';
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
function FieldControl({
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
  const source = useApi<Page>(
    field.source ? `${field.source}${field.source.includes('?') ? '&' : '?'}page_size=100` : null,
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
      className={`field ${field.wide || field.type === 'textarea' || field.type === 'json' ? 'wide' : ''} ${field.type === 'checkbox' ? 'checkbox-field' : ''}`}
    >
      <label htmlFor={common.id}>
        {field.label}
        {field.required && <span> *</span>}
      </label>
      {field.type === 'checkbox' ? (
        <input
          {...common}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
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
          inputMode={field.type === 'decimal' ? 'decimal' : undefined}
          autoComplete={field.type === 'password' ? 'new-password' : undefined}
          value={String(value ?? '')}
          placeholder={field.placeholder}
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
          Справочник недоступен. Проверьте права или повторно откройте форму.
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
  submitLabel?: string;
  note?: string;
}) {
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(
      fields.map((field) => [
        field.name,
        initial[field.name] ??
          field.value ??
          (field.type === 'checkbox' ? false : field.type === 'multiselect' ? [] : ''),
      ]),
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
    const validation = validateFields(fields, values);
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
          : field.type === 'number'
            ? Number(value)
            : field.type === 'datetime-local' && value
              ? new Date(String(value)).toISOString()
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
                field={field}
                value={values[field.name]}
                error={errors[field.name]}
                onChange={(value) => {
                  setValues((v) => ({ ...v, [field.name]: value }));
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
