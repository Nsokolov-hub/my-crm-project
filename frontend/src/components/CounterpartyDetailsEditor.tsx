const legalFields = [
  'Юридический адрес',
  'Почтовый адрес',
  'КПП',
  'Банк',
  'БИК',
  'Расчётный счёт',
  'Корреспондентский счёт',
];
const supplierFields: Record<string, string> = {
  rfq_email: 'Почта для запросов поставщику',
  contract: 'Контракт по умолчанию',
  payment_terms: 'Условия оплаты',
  delivery_terms: 'Условия доставки',
  'Логистика по умолчанию': 'Международная логистика, сумма',
  'Валюта логистики': 'Валюта международной логистики',
};
const bankFields = [
  'Банк',
  'БИК',
  'Корреспондентский счёт',
  'Расчётный счёт',
  'ИНН',
  'КПП',
  'Получатель',
];
const knownKeys = new Set([
  ...legalFields,
  ...Object.keys(supplierFields),
  'calculation_type',
  'seller_bank_details',
  'city',
  'Дополнительные сведения',
]);
const textValue = (value: unknown) =>
  value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
export function CounterpartyDetailsEditor({
  value,
  onChange,
  kind = 'client',
}: {
  value: unknown;
  onChange: (value: Record<string, unknown>) => void;
  kind?: string;
}) {
  const details =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  const isSupplier = ['supplier', 'both'].includes(kind);
  const isClient = ['client', 'both'].includes(kind);
  function update(key: string, text: string) {
    const next = { ...details };
    if (text === '') delete next[key];
    else next[key] = text;
    onChange(next);
  }
  function field(key: string, label = key) {
    return (
      <label key={key}>
        <span>{label}</span>
        <input value={textValue(details[key])} onChange={(e) => update(key, e.target.value)} />
      </label>
    );
  }
  const bank = (details.seller_bank_details || {}) as Record<string, unknown>;
  return (
    <div className="counterparty-details-grid">
      <strong className="wide">Юридические и банковские реквизиты контрагента</strong>
      {legalFields.map((key) => field(key))}
      {field('city', 'Город доставки')}
      {isSupplier && (
        <>
          <strong className="wide">Закупки у поставщика</strong>
          <label>
            <span>Тип расчёта</span>
            <select
              value={String(details.calculation_type || 'IMPORT')}
              onChange={(e) => update('calculation_type', e.target.value)}
            >
              <option value="IMPORT">Импорт — таможня и ввозной НДС</option>
              <option value="DAP">DAP — пошлина 5% и НДС в закупке</option>
              <option value="RUSSIA">Перепродажа внутри РФ — закупка с НДС в RUB</option>
            </select>
          </label>
          {Object.entries(supplierFields).map(([key, label]) => field(key, label))}
        </>
      )}
      {isClient && (
        <>
          <strong className="wide">Наш банковский счёт для этого клиента</strong>
          <p className="wide muted">
            Заполненные поля заменяют банковские реквизиты нашей организации в новых счетах этому
            клиенту. Пустые поля берутся из карточки нашей организации.
          </p>
          {bankFields.map((key) => (
            <label key={'seller-' + key}>
              <span>Наши реквизиты: {key}</span>
              <input
                value={textValue(bank[key])}
                onChange={(e) => {
                  const next = { ...bank };
                  if (e.target.value) next[key] = e.target.value;
                  else delete next[key];
                  onChange({ ...details, seller_bank_details: next });
                }}
              />
            </label>
          ))}
        </>
      )}
      <label className="wide">
        <span>Дополнительные сведения</span>
        <textarea
          rows={3}
          value={textValue(details['Дополнительные сведения'])}
          onChange={(e) => update('Дополнительные сведения', e.target.value)}
        />
      </label>
      {Object.entries(details)
        .filter(([key]) => !knownKeys.has(key))
        .map(([key]) => field(key))}
    </div>
  );
}
