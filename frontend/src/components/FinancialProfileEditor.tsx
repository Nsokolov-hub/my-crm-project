import { useState } from 'react';
import { ErrorBox, Button, Modal } from './ui';
import { ApiError } from '../lib/api';
import { today } from '../lib/format';
import { useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity } from '../lib/types';
import './FinancialProfileEditor.scss';

type Formula = { name: string; expression: string };
type Constant = { name: string; value: string };
type Definition = {
  management_currency: string;
  sale_currency: string;
  currency_precision: Record<string, number>;
  rounding: 'half_up' | 'half_even' | 'down';
  country_of_import: string;
  tax_regime: string;
  constants: Record<string, string>;
  formulas: Formula[];
  tax_category: string;
  funding_ratio: string;
  require_same_sale_currency: boolean;
  allow_partial_acceptance: boolean;
  allow_multiple_suppliers: boolean;
  allow_samples: boolean;
  reward_enabled: boolean;
  reward_label: string;
  reward_basis: string;
  template: { title: string; show_cas: boolean; show_manufacturer: boolean };
};
type Draft = {
  name: string;
  effective_from: string;
  effective_until: string;
  reason: string;
  definition: Definition;
};

const rateFields = [
  { name: 'duty_rate', label: 'Пошлина', help: 'Доля от базы для пошлины.' },
  { name: 'import_tax_rate', label: 'Налог при ввозе', help: 'Доля от базы ввоза с пошлиной.' },
  { name: 'markup', label: 'Наценка', help: 'Доля от себестоимости.' },
  { name: 'sale_tax_rate', label: 'Налог при продаже', help: 'Доля от цены без налога.' },
  {
    name: 'reward_rate',
    label: 'Вознаграждение',
    help: 'Дополнительно к цене, доля от себестоимости.',
  },
] as const;
const rateNames = new Set<string>(rateFields.map((field) => field.name));
const formulaLabels: Record<string, string> = {
  customs_base: 'База для пошлины',
  duty: 'Пошлина',
  import_tax: 'Налог при ввозе',
  cost: 'Себестоимость',
  cash_need: 'Потребность в деньгах',
  reward: 'Вознаграждение',
  sale_net: 'Цена без налога',
  sale_tax: 'Налог продажи',
};
const requiredFormulas = new Set(Object.keys(formulaLabels));

// A blank starter is deliberately not a tax profile. The user supplies the applicable rates.
export function starterDefinition(): Definition {
  return {
    management_currency: 'RUB',
    sale_currency: 'RUB',
    currency_precision: { RUB: 2 },
    rounding: 'half_up',
    country_of_import: '',
    tax_regime: '',
    tax_category: '',
    constants: {
      duty_rate: '0',
      import_tax_rate: '0',
      markup: '0',
      sale_tax_rate: '0',
      reward_rate: '0',
    },
    formulas: [
      { name: 'customs_base', expression: 'purchase' },
      { name: 'duty', expression: 'customs_base * duty_rate' },
      { name: 'import_tax', expression: '(customs_base + duty) * import_tax_rate' },
      { name: 'cost', expression: 'purchase + expenses_cost + duty + import_tax' },
      { name: 'cash_need', expression: 'purchase + expenses_cash + duty + import_tax' },
      { name: 'reward', expression: 'cost * reward_rate' },
      { name: 'sale_net', expression: 'cost * (1 + markup) + reward' },
      { name: 'sale_tax', expression: 'sale_net * sale_tax_rate' },
    ],
    funding_ratio: '1',
    require_same_sale_currency: false,
    allow_partial_acceptance: true,
    allow_multiple_suppliers: true,
    allow_samples: false,
    reward_enabled: false,
    reward_label: '',
    reward_basis: '',
    template: {
      title: 'Коммерческое предложение',
      show_cas: true,
      show_manufacturer: true,
    },
  };
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function str(value: unknown, fallback = ''): string {
  return value === undefined || value === null ? fallback : String(value);
}
function boolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

export function normalizedDefinition(source?: Record<string, unknown>): Definition {
  const base = starterDefinition();
  if (!source) return base;
  const raw = object(source);
  const precision = object(raw.currency_precision);
  const constants = object(raw.constants);
  const template = object(raw.template);
  const rounding = raw.rounding;
  const management = str(raw.management_currency, base.management_currency);
  const sale = str(raw.sale_currency, base.sale_currency);
  return {
    ...base,
    management_currency: management,
    sale_currency: sale,
    currency_precision: {
      ...base.currency_precision,
      ...Object.fromEntries(Object.entries(precision).map(([key, value]) => [key, Number(value)])),
    },
    rounding:
      rounding === 'half_up' || rounding === 'half_even' || rounding === 'down'
        ? rounding
        : base.rounding,
    country_of_import: str(raw.country_of_import),
    tax_regime: str(raw.tax_regime),
    tax_category: str(raw.tax_category),
    constants: {
      ...base.constants,
      ...Object.fromEntries(Object.entries(constants).map(([key, value]) => [key, str(value)])),
    },
    formulas: Array.isArray(raw.formulas)
      ? raw.formulas.map((row) => ({
          name: str(object(row).name),
          expression: str(object(row).expression),
        }))
      : base.formulas,
    funding_ratio: str(raw.funding_ratio, base.funding_ratio),
    require_same_sale_currency: boolean(
      raw.require_same_sale_currency,
      base.require_same_sale_currency,
    ),
    allow_partial_acceptance: boolean(raw.allow_partial_acceptance, base.allow_partial_acceptance),
    allow_multiple_suppliers: boolean(raw.allow_multiple_suppliers, base.allow_multiple_suppliers),
    allow_samples: boolean(raw.allow_samples, base.allow_samples),
    reward_enabled: boolean(raw.reward_enabled, base.reward_enabled),
    reward_label: str(raw.reward_label),
    reward_basis: str(raw.reward_basis),
    template: {
      title: str(template.title, base.template.title),
      show_cas: boolean(template.show_cas, base.template.show_cas),
      show_manufacturer: boolean(template.show_manufacturer, base.template.show_manufacturer),
    },
  };
}

// Decimal place movement avoids binary-float artifacts in percentages sent to the API.
export function shiftDecimal(value: string, places: number): string {
  const normalized = value.trim().replace(',', '.');
  if (!/^\d+(?:\.\d+)?$/.test(normalized)) return normalized;
  const [integer, fraction = ''] = normalized.split('.');
  const digits = `${integer}${fraction}`;
  const decimalPosition = integer.length + places;
  const padded =
    decimalPosition <= 0
      ? `${'0'.repeat(1 - decimalPosition)}${digits}`
      : decimalPosition >= digits.length
        ? `${digits}${'0'.repeat(decimalPosition - digits.length)}`
        : digits;
  const point = decimalPosition <= 0 ? 1 : decimalPosition;
  const result = `${padded.slice(0, point)}.${padded.slice(point)}`;
  return (
    result
      .replace(/^0+(?=\d)/, '')
      .replace(/\.?0+$/, '')
      .replace(/\.$/, '') || '0'
  );
}

function initialDraft(initial?: Entity, template?: Record<string, unknown>): Draft {
  return {
    name: str(initial?.name),
    effective_from: today(),
    effective_until: '',
    reason: '',
    definition: normalizedDefinition((initial?.definition as Record<string, unknown>) || template),
  };
}

export function buildProfileBody(draft: Draft, previousId?: string): Record<string, unknown> {
  const definition = draft.definition;
  const currencyPrecision = {
    ...definition.currency_precision,
    [definition.management_currency]:
      definition.currency_precision[definition.management_currency] ?? 2,
    [definition.sale_currency]: definition.currency_precision[definition.sale_currency] ?? 2,
  };
  return {
    name: draft.name.trim(),
    effective_from: draft.effective_from,
    effective_until: draft.effective_until || null,
    reason: draft.reason.trim(),
    ...(previousId ? { previous_id: previousId } : {}),
    definition: { ...definition, currency_precision: currencyPrecision },
  };
}

export function FinancialProfileEditor({
  initial,
  template,
  onClose,
  onSuccess,
}: {
  initial?: Entity;
  template?: Record<string, unknown>;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => initialDraft(initial, template));
  const [rateInputs, setRateInputs] = useState<Record<string, string>>(() => {
    const constants = initialDraft(initial, template).definition.constants;
    return Object.fromEntries(
      rateFields.map((field) => [field.name, shiftDecimal(constants[field.name] ?? '0', 2)]),
    );
  });
  const [fundingInput, setFundingInput] = useState(() =>
    shiftDecimal(initialDraft(initial, template).definition.funding_ratio, 2),
  );
  const [dirty, setDirty] = useState(false);
  const [ratesConfirmed, setRatesConfirmed] = useState(false);
  const [validation, setValidation] = useState('');
  const operation = useCommand();
  useDirtyProtection(dirty);
  const definition = draft.definition;
  const unavailable = Boolean(
    initial &&
    (!Array.isArray(object(initial.definition).formulas) ||
      !('constants' in object(initial.definition))),
  );

  function updateDraft(patch: Partial<Draft>) {
    setDraft((current) => ({ ...current, ...patch }));
    setDirty(true);
    setValidation('');
  }
  function updateDefinition(patch: Partial<Definition>) {
    setDraft((current) => ({
      ...current,
      definition: { ...current.definition, ...patch },
    }));
    setDirty(true);
    setRatesConfirmed(false);
    setValidation('');
  }
  function updateConstant(name: string, value: string) {
    updateDefinition({ constants: { ...definition.constants, [name]: value } });
  }
  function close() {
    if (operation.busy) return;
    if (!dirty || window.confirm('Есть несохранённые изменения. Закрыть профиль?')) onClose();
  }
  function validate(): string {
    if (unavailable)
      return 'У вас нет доступа к формулам исходной версии. Обратитесь к администратору.';
    if (!draft.name.trim()) return 'Укажите название профиля.';
    if (!draft.effective_from) return 'Укажите дату начала действия.';
    if (draft.effective_until && draft.effective_until < draft.effective_from)
      return 'Дата окончания не может быть раньше даты начала.';
    if (!definition.country_of_import.trim()) return 'Укажите страну ввоза.';
    if (!definition.tax_regime.trim()) return 'Укажите режим расчёта налогов.';
    if (!definition.tax_category.trim()) return 'Укажите категорию налога для документов.';
    if (
      !/^[A-Z]{3}$/.test(definition.management_currency) ||
      !/^[A-Z]{3}$/.test(definition.sale_currency)
    )
      return 'Укажите коды валют из трёх латинских букв, например RUB.';
    for (const currency of [definition.management_currency, definition.sale_currency]) {
      const precision = definition.currency_precision[currency] ?? 2;
      if (!Number.isInteger(precision) || precision < 0 || precision > 6)
        return `Для ${currency} выберите точность от 0 до 6 знаков.`;
    }
    for (const field of rateFields) {
      const percent = shiftDecimal(definition.constants[field.name] || '0', 2);
      if (!/^\d+(?:\.\d{1,6})?$/.test(percent)) return `Проверьте ставку «${field.label}».`;
      if (
        field.name === 'reward_rate' &&
        !definition.reward_enabled &&
        Number(definition.constants.reward_rate) !== 0
      )
        return 'Отключённое вознаграждение должно иметь ставку 0%.';
    }
    if (
      !/^\d+(?:\.\d{1,6})?$/.test(shiftDecimal(definition.funding_ratio, 2)) ||
      Number(definition.funding_ratio) > 1
    )
      return 'Доля предоплаты должна быть от 0 до 100%.';
    for (const [name, value] of Object.entries(definition.constants)) {
      if (!/^[a-z][a-z0-9_]{0,59}$/.test(name)) return `Проверьте код коэффициента «${name}».`;
      if (!/^-?\d+(?:\.\d+)?$/.test(value)) return `Проверьте значение коэффициента «${name}».`;
    }
    const names = definition.formulas.map((row) => row.name);
    if (new Set(names).size !== names.length)
      return 'Названия шагов расчёта должны быть уникальными.';
    if (!Array.from(requiredFormulas).every((name) => names.includes(name)))
      return 'В расчёте должны присутствовать все обязательные шаги.';
    if (definition.formulas.length > 40) return 'В расчёте может быть не более 40 шагов.';
    for (const row of definition.formulas) {
      if (!/^[a-z][a-z0-9_]{0,59}$/.test(row.name) || !row.expression.trim())
        return 'Укажите допустимый код и выражение для каждого шага расчёта.';
    }
    if (
      definition.reward_enabled &&
      (!definition.reward_label.trim() || !definition.reward_basis.trim())
    )
      return 'Укажите название и основание вознаграждения.';
    if (!definition.template.title.trim()) return 'Укажите название коммерческого предложения.';
    if (!ratesConfirmed)
      return 'Подтвердите, что ставки и правила расчёта проверены для вашей компании.';
    if (draft.reason.trim().length < 3)
      return 'Укажите основание изменения (не менее трёх символов).';
    return '';
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const problem = validate();
    setValidation(problem);
    if (problem) return;
    try {
      const result = await operation.run<Entity>(
        '/profiles',
        buildProfileBody(draft, initial?.id),
        'POST',
        true,
      );
      if (result) {
        setDirty(false);
        onSuccess();
      }
    } catch (error) {
      if (error instanceof ApiError && error.field) setValidation(error.message);
    }
  }
  function moveFormula(index: number, direction: -1 | 1) {
    const next = [...definition.formulas];
    const other = index + direction;
    if (other < 0 || other >= next.length) return;
    [next[index], next[other]] = [next[other], next[index]];
    updateDefinition({ formulas: next });
  }
  function changeFormula(index: number, patch: Partial<Formula>) {
    const next = definition.formulas.map((row, rowIndex) =>
      rowIndex === index ? { ...row, ...patch } : row,
    );
    updateDefinition({ formulas: next });
  }
  const otherConstants: Constant[] = Object.entries(definition.constants)
    .filter(([name]) => !rateNames.has(name))
    .map(([name, value]) => ({ name, value }));
  const usesStarterFormulas =
    JSON.stringify(definition.formulas) === JSON.stringify(starterDefinition().formulas);

  return (
    <Modal
      title={initial ? 'Новая версия профиля' : 'Новый финансовый профиль'}
      wide
      onClose={close}
    >
      <form noValidate onSubmit={(event) => void submit(event)}>
        <div className="form-body profile-editor">
          <div className="info-note">
            {initial
              ? 'Проверьте ставки и условия предыдущей версии перед сохранением. '
              : template
                ? 'Открыт условный пример с тестовыми ставками. Замените их правилами вашей компании. '
                : 'Новый профиль начинается с нулевых ставок и простой схемы расчёта. Нули — заготовка, а не утверждённые ставки. '}
            Проверьте расчёт на контрольном примере. Сохранение создаёт черновик; применять его CRM
            начнёт после публикации.
          </div>
          {unavailable && (
            <div className="info-note">
              Формулы исходной версии скрыты вашими правами. Новую версию можно создать только с
              полным доступом к финансовому профилю.
            </div>
          )}
          <ErrorBox error={operation.error || (validation ? new Error(validation) : undefined)} />

          <section className="profile-section">
            <h3>Профиль и срок действия</h3>
            <div className="form-grid">
              <label className="field wide">
                Название профиля *
                <input
                  value={draft.name}
                  onChange={(event) => updateDraft({ name: event.target.value })}
                  required
                />
              </label>
              <p className="profile-effective-note wide">
                Профиль начнёт действовать в день публикации.
              </p>
              <label className="field">
                Действует по (необязательно)
                <input
                  type="date"
                  value={draft.effective_until}
                  onChange={(event) => updateDraft({ effective_until: event.target.value })}
                />
              </label>
              <label className="field">
                Страна ввоза *
                <input
                  value={definition.country_of_import}
                  onChange={(event) => updateDefinition({ country_of_import: event.target.value })}
                  placeholder="Например, Россия"
                  required
                />
              </label>
              <label className="field">
                Налоговый режим *
                <input
                  value={definition.tax_regime}
                  onChange={(event) => updateDefinition({ tax_regime: event.target.value })}
                  placeholder="Название применяемого режима"
                  required
                />
              </label>
              <label className="field wide">
                Категория налога в документах *
                <input
                  value={definition.tax_category}
                  onChange={(event) => updateDefinition({ tax_category: event.target.value })}
                  placeholder="Например, без НДС или НДС по применяемой ставке"
                  required
                />
              </label>
            </div>
          </section>

          <section className="profile-section">
            <h3>Валюты и округление</h3>
            <div className="form-grid">
              <label className="field">
                Валюта учёта *
                <input
                  maxLength={3}
                  value={definition.management_currency}
                  onChange={(event) =>
                    updateDefinition({ management_currency: event.target.value.toUpperCase() })
                  }
                  required
                />
                <small>Трёхбуквенный код, например RUB.</small>
              </label>
              <label className="field">
                Валюта продажи *
                <input
                  maxLength={3}
                  value={definition.sale_currency}
                  onChange={(event) =>
                    updateDefinition({ sale_currency: event.target.value.toUpperCase() })
                  }
                  required
                />
              </label>
              <label className="field">
                Знаков после запятой — валюта учёта
                <select
                  value={definition.currency_precision[definition.management_currency] ?? 2}
                  onChange={(event) =>
                    updateDefinition({
                      currency_precision: {
                        ...definition.currency_precision,
                        [definition.management_currency]: Number(event.target.value),
                      },
                    })
                  }
                >
                  {[0, 1, 2, 3, 4, 5, 6].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              {definition.sale_currency !== definition.management_currency && (
                <label className="field">
                  Знаков после запятой — валюта продажи
                  <select
                    value={definition.currency_precision[definition.sale_currency] ?? 2}
                    onChange={(event) =>
                      updateDefinition({
                        currency_precision: {
                          ...definition.currency_precision,
                          [definition.sale_currency]: Number(event.target.value),
                        },
                      })
                    }
                  >
                    {[0, 1, 2, 3, 4, 5, 6].map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="field">
                Правило округления
                <select
                  value={definition.rounding}
                  onChange={(event) =>
                    updateDefinition({ rounding: event.target.value as Definition['rounding'] })
                  }
                >
                  <option value="half_up">0,5 — в большую сторону</option>
                  <option value="half_even">0,5 — к ближайшему чётному</option>
                  <option value="down">Всегда вниз</option>
                </select>
              </label>
            </div>
          </section>

          <section className="profile-section">
            <h3>Ставки и наценка</h3>
            <p>
              Введите проценты. Нулевые значения в новом профиле — заготовка, а не действующие
              ставки.
            </p>
            <div className="form-grid">
              {rateFields
                .filter((field) => field.name !== 'reward_rate' || definition.reward_enabled)
                .map((field) => (
                  <label className="field" key={field.name}>
                    {field.label}, %
                    <input
                      type="text"
                      inputMode="decimal"
                      value={rateInputs[field.name]}
                      onChange={(event) => {
                        setRateInputs((current) => ({
                          ...current,
                          [field.name]: event.target.value,
                        }));
                        updateConstant(field.name, shiftDecimal(event.target.value, -2));
                      }}
                    />
                    <small>{field.help}</small>
                  </label>
                ))}
            </div>
          </section>

          <section className="profile-section">
            <h3>Правила продажи и оплаты</h3>
            <div className="form-grid">
              <label className="field">
                Минимальная доля оплаты для исполнения, %
                <input
                  type="text"
                  inputMode="decimal"
                  value={fundingInput}
                  onChange={(event) => {
                    setFundingInput(event.target.value);
                    updateDefinition({ funding_ratio: shiftDecimal(event.target.value, -2) });
                  }}
                />
                <small>100% означает полную оплату до исполнения.</small>
              </label>
              <div className="profile-switches wide">
                <label>
                  <input
                    type="checkbox"
                    checked={definition.require_same_sale_currency}
                    onChange={(event) =>
                      updateDefinition({ require_same_sale_currency: event.target.checked })
                    }
                  />{' '}
                  Валюта закупки должна совпадать с валютой продажи
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={definition.allow_partial_acceptance}
                    onChange={(event) =>
                      updateDefinition({ allow_partial_acceptance: event.target.checked })
                    }
                  />{' '}
                  Разрешить клиенту принять часть количества
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={definition.allow_multiple_suppliers}
                    onChange={(event) =>
                      updateDefinition({ allow_multiple_suppliers: event.target.checked })
                    }
                  />{' '}
                  Разрешить товары нескольких поставщиков в согласованном составе
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={definition.allow_samples}
                    onChange={(event) => updateDefinition({ allow_samples: event.target.checked })}
                  />{' '}
                  Разрешить образцы в расчёте
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={definition.reward_enabled}
                    onChange={(event) => {
                      if (!event.target.checked)
                        setRateInputs((current) => ({ ...current, reward_rate: '0' }));
                      updateDefinition({
                        reward_enabled: event.target.checked,
                        ...(event.target.checked
                          ? {}
                          : { constants: { ...definition.constants, reward_rate: '0' } }),
                      });
                    }}
                  />{' '}
                  Учитывать отдельное вознаграждение
                </label>
              </div>
              {definition.reward_enabled && (
                <>
                  <label className="field">
                    Название вознаграждения *
                    <input
                      value={definition.reward_label}
                      onChange={(event) => updateDefinition({ reward_label: event.target.value })}
                      required
                    />
                  </label>
                  <label className="field">
                    Основание вознаграждения *
                    <input
                      value={definition.reward_basis}
                      onChange={(event) => updateDefinition({ reward_basis: event.target.value })}
                      required
                    />
                  </label>
                </>
              )}
            </div>
          </section>

          <section className="profile-section">
            <h3>Коммерческое предложение</h3>
            <div className="form-grid">
              <label className="field wide">
                Заголовок документа *
                <input
                  value={definition.template.title}
                  onChange={(event) =>
                    updateDefinition({
                      template: { ...definition.template, title: event.target.value },
                    })
                  }
                  required
                />
              </label>
              <div className="profile-switches wide">
                <label>
                  <input
                    type="checkbox"
                    checked={definition.template.show_cas}
                    onChange={(event) =>
                      updateDefinition({
                        template: { ...definition.template, show_cas: event.target.checked },
                      })
                    }
                  />{' '}
                  Показывать CAS
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={definition.template.show_manufacturer}
                    onChange={(event) =>
                      updateDefinition({
                        template: {
                          ...definition.template,
                          show_manufacturer: event.target.checked,
                        },
                      })
                    }
                  />{' '}
                  Показывать производителя
                </label>
              </div>
            </div>
          </section>

          <section className="profile-section">
            <h3>Порядок расчёта</h3>
            {usesStarterFormulas ? (
              <ol className="profile-steps">
                <li>Пошлина считается от закупочной стоимости.</li>
                <li>Налог при ввозе считается от закупочной стоимости вместе с пошлиной.</li>
                <li>В себестоимость входят закупка, расходы, пошлина и налог при ввозе.</li>
                <li>Цена без налога равна себестоимости с наценкой и вознаграждением.</li>
                <li>Налог продажи считается от цены без налога.</li>
              </ol>
            ) : (
              <p>В этой версии изменён порядок расчёта. Проверьте формулы в разделе ниже.</p>
            )}
            <details className="profile-advanced">
              <summary>Изменить формулы и дополнительные коэффициенты</summary>
              <p>
                Для обычной работы достаточно указать ставки выше. Если ваш порядок расчёта
                отличается, меняйте отдельные шаги здесь. Выражения используют только числа,
                арифметику и параметры предыдущих шагов.
              </p>
              <div className="profile-legend">
                <span>
                  <b>purchase</b> — закупка
                </span>
                <span>
                  <b>expenses_cost</b> — расходы в себестоимости
                </span>
                <span>
                  <b>expenses_cash</b> — расходы к оплате
                </span>
                <span>
                  <b>quantity</b> — количество
                </span>
              </div>
              <h4>Дополнительные коэффициенты</h4>
              {otherConstants.map((constant) => (
                <div className="profile-constant" key={constant.name}>
                  <label className="field">
                    Код коэффициента
                    <input
                      defaultValue={constant.name}
                      onBlur={(event) => {
                        const newName = event.target.value.trim();
                        if (newName === constant.name) return;
                        if (!newName || newName in definition.constants) {
                          event.target.value = constant.name;
                          setValidation('Введите новый уникальный код коэффициента.');
                          return;
                        }
                        const next = { ...definition.constants };
                        delete next[constant.name];
                        next[newName] = constant.value;
                        updateDefinition({ constants: next });
                      }}
                    />
                  </label>
                  <label className="field">
                    Значение
                    <input
                      inputMode="decimal"
                      value={constant.value}
                      onChange={(event) =>
                        updateConstant(constant.name, event.target.value.replace(',', '.'))
                      }
                    />
                  </label>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      const next = { ...definition.constants };
                      delete next[constant.name];
                      updateDefinition({ constants: next });
                    }}
                  >
                    Удалить
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  let index = 1;
                  while (definition.constants[`coefficient_${index}`]) index += 1;
                  updateDefinition({
                    constants: { ...definition.constants, [`coefficient_${index}`]: '0' },
                  });
                }}
              >
                Добавить коэффициент
              </Button>
              <h4>Шаги расчёта</h4>
              {definition.formulas.map((formula, index) => (
                <div className="profile-formula" key={index}>
                  <label className="field">
                    {formulaLabels[formula.name] || 'Дополнительный шаг'}
                    <input
                      value={formula.name}
                      readOnly={requiredFormulas.has(formula.name)}
                      onChange={(event) => changeFormula(index, { name: event.target.value })}
                      aria-label={`Код шага ${index + 1}`}
                    />
                  </label>
                  <label className="field">
                    Как вычислить
                    <input
                      value={formula.expression}
                      onChange={(event) => changeFormula(index, { expression: event.target.value })}
                      aria-label={`Формула шага ${index + 1}`}
                    />
                  </label>
                  <div className="profile-formula-actions">
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={index === 0}
                      onClick={() => moveFormula(index, -1)}
                      aria-label={`Шаг ${index + 1} выше`}
                    >
                      ↑
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={index === definition.formulas.length - 1}
                      onClick={() => moveFormula(index, 1)}
                      aria-label={`Шаг ${index + 1} ниже`}
                    >
                      ↓
                    </Button>
                    {!requiredFormulas.has(formula.name) && (
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() =>
                          updateDefinition({
                            formulas: definition.formulas.filter(
                              (_, rowIndex) => rowIndex !== index,
                            ),
                          })
                        }
                      >
                        Удалить
                      </Button>
                    )}
                  </div>
                </div>
              ))}
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  updateDefinition({
                    formulas: [...definition.formulas, { name: '', expression: '' }],
                  })
                }
              >
                Добавить шаг
              </Button>
            </details>
            <label className="profile-confirmation">
              <input
                type="checkbox"
                checked={ratesConfirmed}
                onChange={(event) => setRatesConfirmed(event.target.checked)}
              />
              Я проверил ставки и правила расчёта для своей компании; значение 0% там, где оно
              осталось, указано осознанно.
            </label>
          </section>

          <section className="profile-section">
            <label className="field">
              Основание создания или изменения *
              <textarea
                rows={3}
                value={draft.reason}
                onChange={(event) => updateDraft({ reason: event.target.value })}
                placeholder="Кто и на каком основании утвердил эти правила"
                required
              />
            </label>
          </section>
        </div>
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={close}>
            Отмена
          </Button>
          <Button type="submit" busy={operation.busy} disabled={unavailable}>
            Сохранить черновик
          </Button>
        </div>
      </form>
    </Modal>
  );
}
