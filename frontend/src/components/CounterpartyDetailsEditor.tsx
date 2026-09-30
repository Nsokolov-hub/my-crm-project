const detailFields: { key: string; label: string; multiline?: boolean }[] = [
  { key: 'Юридический адрес', label: 'Юридический адрес' },
  { key: 'Почтовый адрес', label: 'Почтовый адрес' },
  { key: 'КПП', label: 'КПП' },
  { key: 'Банк', label: 'Банк' },
  { key: 'БИК', label: 'БИК' },
  { key: 'Расчётный счёт', label: 'Расчётный счёт' },
  { key: 'Корреспондентский счёт', label: 'Корреспондентский счёт' },
  { key: 'Дополнительные сведения', label: 'Дополнительные сведения', multiline: true },
  { key: 'Логистика по умолчанию', label: 'Международная логистика поставщика, сумма' },
  { key: 'Валюта логистики', label: 'Валюта логистики поставщика (например, INR)' },
];

const knownKeys = new Set<string>(detailFields.map((field) => field.key));
const legacyLabels: Record<string, string> = {
  city: 'Город',
  comment: 'Комментарий из импорта',
};

function textValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

export function CounterpartyDetailsEditor({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (value: Record<string, unknown>) => void;
}) {
  const details = value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};

  function update(key: string, text: string) {
    const next = { ...details };
    if (text === '') delete next[key];
    else next[key] = text;
    onChange(next);
  }

  const otherFields = Object.entries(details).filter(([key]) => !knownKeys.has(key));

  return (
    <div className="counterparty-details-grid">
      {detailFields.map((field) => (
        <label key={field.key} className={field.multiline ? 'wide' : ''}>
          <span>{field.label}</span>
          {field.multiline ? (
            <textarea
              rows={3}
              value={textValue(details[field.key])}
              onChange={(event) => update(field.key, event.target.value)}
            />
          ) : (
            <input
              type="text"
              value={textValue(details[field.key])}
              onChange={(event) => update(field.key, event.target.value)}
            />
          )}
        </label>
      ))}
      {otherFields.map(([key, saved]) => (
        <label key={key}>
          <span>{legacyLabels[key] || key}</span>
          <input
            type="text"
            value={textValue(saved)}
            onChange={(event) => update(key, event.target.value)}
          />
        </label>
      ))}
    </div>
  );
}
