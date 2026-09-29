import { useEffect, useState } from 'react';
import { useAuth } from '../app/Auth';
import { ApiError } from '../lib/api';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
import { Button, ErrorBox, Loading, Section } from './ui';

type Setting = Entity & {
  key: string;
  value: Record<string, unknown>;
  version: number;
};
type SettingMetadata = Record<string, { is_supported: boolean }>;
type Rule = {
  key: string;
  field: string;
  title: string;
  description: string;
  detail: string;
  choices: { value: string; label: string }[];
};

// Loss reasons remain configurable; call results are fixed for the call base.
const rules: Rule[] = [
  {
    key: 'loss_reasons',
    field: 'reasons',
    title: 'Причины отказа',
    description: 'Причины, которые сотрудник указывает при закрытии заявки без продажи.',
    detail: 'При закрытии заявки сотрудник обязан выбрать одну из этих причин.',
    choices: [
      { value: 'price_too_high', label: 'Высокая цена' },
      { value: 'went_to_competitor', label: 'Выбран конкурент' },
      { value: 'no_budget', label: 'Нет бюджета' },
      { value: 'timing', label: 'Не подходят сроки' },
      { value: 'other', label: 'Другая причина' },
    ],
  },
];

function valuesOf(row: Setting | undefined, field: string): string[] {
  const values = row?.value?.[field];
  return Array.isArray(values)
    ? values.filter((value): value is string => typeof value === 'string')
    : [];
}

function RuleCard({
  rule,
  row,
  editable,
  onUpdated,
}: {
  rule: Rule;
  row?: Setting;
  editable: boolean;
  onUpdated: () => void;
}) {
  const initial = valuesOf(row, rule.field);
  const [items, setItems] = useState(initial);
  const [editing, setEditing] = useState(false);
  const [standard, setStandard] = useState('');
  const [custom, setCustom] = useState('');
  const [validation, setValidation] = useState('');
  const [conflict, setConflict] = useState(false);
  const operation = useCommand();
  const labelOf = (value: string) =>
    rule.choices.find((choice) => choice.value === value)?.label || value;
  const dirty = JSON.stringify(items) !== JSON.stringify(initial);
  useDirtyProtection(editing && dirty);

  function startEdit() {
    setItems(initial);
    setStandard('');
    setCustom('');
    setValidation('');
    setConflict(false);
    operation.setError(undefined);
    setEditing(true);
  }

  function addItem() {
    const entered = custom.trim();
    const matched = rule.choices.find(
      (choice) => choice.label.toLocaleLowerCase('ru') === entered.toLocaleLowerCase('ru'),
    );
    const value = entered ? matched?.value || entered : standard;
    if (!value) {
      setValidation('Выберите готовый вариант или напишите свой.');
      return;
    }
    if (
      items.some(
        (item) => labelOf(item).toLocaleLowerCase('ru') === labelOf(value).toLocaleLowerCase('ru'),
      )
    ) {
      setValidation('Такой вариант уже есть в списке.');
      return;
    }
    setItems((current) => [...current, value]);
    setStandard('');
    setCustom('');
    setValidation('');
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!items.length) {
      setValidation('Оставьте хотя бы один вариант.');
      return;
    }
    try {
      await operation.run('/settings', {
        key: rule.key,
        value: { [rule.field]: items },
        version: row?.version || 0,
      });
      setEditing(false);
      onUpdated();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
    }
  }

  return (
    <Section
      title={rule.title}
      description={rule.description}
      action={
        editable && !editing ? (
          <Button variant="secondary" onClick={startEdit}>
            Изменить список
          </Button>
        ) : undefined
      }
    >
      <div className="form-body">
        <p>{rule.detail}</p>
        {!row && <p>Список ещё не настроен.</p>}
        {!editing ? (
          <ul className="guide-list">
            {initial.map((item) => (
              <li key={item}>{labelOf(item)}</li>
            ))}
          </ul>
        ) : (
          <form onSubmit={(event) => void save(event)}>
            <ul className="guide-list">
              {items.map((item) => (
                <li key={item}>
                  <span>{labelOf(item)} </span>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setItems((current) => current.filter((value) => value !== item))}
                    aria-label={`Убрать ${labelOf(item)}`}
                  >
                    Убрать
                  </Button>
                </li>
              ))}
            </ul>
            <div className="form-grid">
              <label className="field">
                Готовый вариант
                <select value={standard} onChange={(event) => setStandard(event.target.value)}>
                  <option value="">Выберите из списка</option>
                  {rule.choices
                    .filter((choice) => !items.includes(choice.value))
                    .map((choice) => (
                      <option key={choice.value} value={choice.value}>
                        {choice.label}
                      </option>
                    ))}
                </select>
              </label>
              <label className="field">
                Или свой вариант
                <input
                  value={custom}
                  onChange={(event) => setCustom(event.target.value)}
                  maxLength={100}
                  placeholder="Например, клиент ждёт образец"
                />
              </label>
            </div>
            <div className="inline-actions">
              <Button type="button" variant="secondary" onClick={addItem}>
                Добавить вариант
              </Button>
            </div>
            {validation && (
              <p role="alert" className="field-error">
                {validation}
              </p>
            )}
            <ErrorBox error={operation.error} />
            {conflict && (
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setEditing(false);
                  onUpdated();
                }}
              >
                Загрузить актуальный список
              </Button>
            )}
            <div className="inline-actions">
              <Button type="submit" busy={operation.busy} disabled={conflict || !dirty}>
                Сохранить список
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setEditing(false);
                  operation.setError(undefined);
                }}
              >
                Отмена
              </Button>
            </div>
          </form>
        )}
      </div>
    </Section>
  );
}

export function WorkingRulesEditor({ refreshKey = 0 }: { refreshKey?: number }) {
  const auth = useAuth();
  const metadata = useApi<SettingMetadata>('/settings/meta');
  const settings = useApi<Page<Setting>>('/settings');
  const { refresh } = settings;

  useEffect(() => {
    if (refreshKey) refresh();
  }, [refreshKey, refresh]);

  if (metadata.loading || settings.loading) return <Loading />;
  if (metadata.error || settings.error)
    return (
      <ErrorBox
        error={metadata.error || settings.error}
        retry={() => {
          metadata.refresh();
          settings.refresh();
        }}
      />
    );

  const available = rules.filter((rule) => metadata.data?.[rule.key]?.is_supported);
  return (
    <>
      <div className="info-note">
        Здесь показаны правила, которые применяются в работе CRM. Причины отказа можно изменить.
      </div>
      <Section title="Результаты звонка" description="Доступные сотрудникам результаты обзвона">
        <ul className="guide-list">
          {[
            'Не интересны',
            'Отправлена презентация',
            'Ждём запрос',
            'Получен запрос',
            'Неактуальный контакт',
          ].map((label) => (
            <li key={label}>{label}</li>
          ))}
        </ul>
      </Section>
      {available.map((rule) => {
        const row = settings.data?.items.find((item) => item.key === rule.key);
        return (
          <RuleCard
            key={`${rule.key}:${row?.version || 0}`}
            rule={rule}
            row={row}
            editable={auth.can('settings.dictionaries.write')}
            onUpdated={settings.refresh}
          />
        );
      })}
    </>
  );
}
