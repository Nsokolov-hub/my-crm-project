import { useState } from 'react';
import { DirectorySelect } from './Form';
import { Button, ErrorBox, Loading, Modal } from './ui';
import { ApiError } from '../lib/api';
import { today } from '../lib/format';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';
import './FinancialProfileEditor.scss';

type CustomsRule = { product_group_slug: string; type: 'PERCENTAGE' | 'FIXED_GROUP' | 'NONE'; value: string };
type FeeBracket = { from_amount: string; to_amount: string | null; fee: string; valid_from?: string | null; valid_to?: string | null };
type ProfileExpense = {
  name: string; amount: string; currency: string; method: string; basis: string;
  stage: string; calculation_type: string; percent_base: string | null; brackets: Record<string, unknown>[];
  include_in_cost: boolean; include_in_cash: boolean;
};
type ItemizedDefinition = {
  methodology: 'itemized_v2';
  management_currency: 'RUB';
  sale_currency: 'RUB';
  currency_precision: Record<string, number>;
  rounding: 'half_up' | 'half_even' | 'down';
  import_country_id: string | null;
  vat_rate: string;
  vat_deduction_mode: boolean;
  financing_annual_rate: string;
  day_basis: number;
  financing_start_event: 'delivery' | 'shipment' | 'invoice';
  default_markup_coefficient: string;
  default_expenses: ProfileExpense[];
  customs_rules: CustomsRule[];
  customs_fee_brackets: FeeBracket[];
  tax_category: string;
  template: { title: string; show_cas: boolean; show_manufacturer: boolean };
  [key: string]: unknown;
};
type ProductGroup = Entity & { name: string; slug: string };
const countryField: Field = {
  name: 'import_country_id', label: 'Страна ввоза', type: 'select', source: '/countries', required: true,
};
const groupNames: Record<string, string> = {
  reference_standards: 'Стандартные образцы', reagents: 'Реактивы', columns: 'Колонки',
  lab_glassware: 'Лабораторная посуда', other: 'Другое',
};
const decimal = /^\d+(?:[.,]\d+)?$/;

export function ItemizedProfileEditor({
  initial, onClose, onSuccess,
}: {
  initial?: Entity;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const example = useApi<{ definition: ItemizedDefinition }>(initial ? null : '/profiles/example-v2');
  if (!initial && !example.data) {
    return <Modal title="Новый профиль расчёта" wide onClose={onClose}>
      <div className="form-body">
        <ErrorBox error={example.error} retry={example.refresh} />
        {example.loading && <Loading />}
      </div>
    </Modal>;
  }
  const raw = (initial?.definition || example.data?.definition) as ItemizedDefinition;
  const hiddenRules = !Array.isArray(raw.customs_rules) || !Array.isArray(raw.customs_fee_brackets);
  const definition = {
    ...raw,
    customs_rules: Array.isArray(raw.customs_rules) ? raw.customs_rules : [],
    customs_fee_brackets: Array.isArray(raw.customs_fee_brackets) ? raw.customs_fee_brackets : [],
    default_expenses: Array.isArray(raw.default_expenses) ? raw.default_expenses : [],
  };
  return <ItemizedProfileForm initial={initial} startingDefinition={definition} hiddenRules={hiddenRules} onClose={onClose} onSuccess={onSuccess} />;
}

function ItemizedProfileForm({
  initial, startingDefinition, hiddenRules, onClose, onSuccess,
}: {
  initial?: Entity;
  startingDefinition: ItemizedDefinition;
  hiddenRules: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [name, setName] = useState(String(initial?.name || ''));
  const [effectiveUntil, setEffectiveUntil] = useState('');
  const [reason, setReason] = useState('');
  const [definition, setDefinition] = useState<ItemizedDefinition>(startingDefinition);
  const [confirmed, setConfirmed] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [validation, setValidation] = useState('');
  const groups = useApi<Page<ProductGroup>>('/product-groups?page_size=100');
  const expenseTypes = useApi<Page<Entity>>('/expense-types?page_size=100');
  const [expenseTypeId, setExpenseTypeId] = useState('');
  const operation = useCommand();
  useDirtyProtection(dirty);
  const readonlyProfile = hiddenRules;

  function update(patch: Partial<ItemizedDefinition>) {
    setDefinition((current) => ({ ...current, ...patch }));
    setDirty(true);
    setConfirmed(false);
    setValidation('');
  }
  function changeRule(index: number, patch: Partial<CustomsRule>) {
    update({ customs_rules: definition.customs_rules.map((row, n) => n === index ? { ...row, ...patch } : row) });
  }
  function changeBracket(index: number, patch: Partial<FeeBracket>) {
    update({ customs_fee_brackets: definition.customs_fee_brackets.map((row, n) => n === index ? { ...row, ...patch } : row) });
  }
  function addExpenseType() {
    const row = expenseTypes.data?.items.find((item) => item.id === expenseTypeId);
    if (!row) return;
    const name = String(row.name || '');
    if (definition.default_expenses.some((item) => item.name === name)) {
      setValidation('Этот расход уже добавлен в профиль.');
      return;
    }
    update({ default_expenses: [...definition.default_expenses, {
      name, amount: String(row.default_value || '0'), currency: String(row.currency_code || 'RUB'),
      method: String(row.distribution_method || 'BY_QUANTITY'), basis: 'Профиль расчёта',
      stage: String(row.stage || 'GENERAL'), calculation_type: String(row.calculation_type || 'FIXED'),
      percent_base: row.percent_base ? String(row.percent_base) : null,
      brackets: Array.isArray(row.brackets) ? row.brackets as Record<string, unknown>[] : [],
      include_in_cost: row.include_in_cost !== false, include_in_cash: row.include_in_cash !== false,
    }] });
    setExpenseTypeId('');
  }
  function close() {
    if (operation.busy) return;
    if (!dirty || window.confirm('Есть несохранённые изменения. Закрыть профиль?')) onClose();
  }
  function check(): string {
    if (readonlyProfile) return 'Нет доступа к таможенным правилам исходного профиля.';
    if (!name.trim()) return 'Укажите название профиля.';
    if (!definition.import_country_id) return 'Выберите страну ввоза из справочника.';
    if (!reason.trim() || reason.trim().length < 3) return 'Укажите основание изменения — не менее трёх символов.';
    if (effectiveUntil && effectiveUntil < today()) return 'Дата окончания не может быть раньше текущей даты.';
    if (!decimal.test(definition.vat_rate)) return 'Проверьте ставку НДС.';
    if (!decimal.test(definition.financing_annual_rate)) return 'Проверьте финансовую ставку.';
    if (!Number.isInteger(definition.day_basis) || definition.day_basis < 1 || definition.day_basis > 366)
      return 'База дней должна быть целым числом от 1 до 366.';
    if (!decimal.test(definition.default_markup_coefficient) || Number(definition.default_markup_coefficient.replace(',', '.')) <= 0)
      return 'Наценка должна быть положительным коэффициентом.';
    const slugs = definition.customs_rules.map((row) => row.product_group_slug);
    if (new Set(slugs).size !== slugs.length || slugs.some((slug) => !slug)) return 'Проверьте уникальность товарных групп.';
    if (definition.customs_rules.some((row) => !decimal.test(row.value))) return 'Проверьте значения таможенных правил.';
    if (!definition.customs_fee_brackets.length || definition.customs_fee_brackets.some((row) =>
      !decimal.test(row.from_amount) || !decimal.test(row.fee) ||
      (row.to_amount !== null && row.to_amount !== '' && (!decimal.test(row.to_amount) ||
        Number(row.from_amount.replace(',', '.')) > Number(row.to_amount.replace(',', '.')))) ||
      Boolean(row.valid_from && row.valid_to && row.valid_to < row.valid_from)
    )) return 'Укажите корректные диапазоны таможенного сбора.';
    if (!definition.template.title.trim()) return 'Укажите название коммерческого предложения.';
    if (definition.default_expenses.some((row) => !row.name.trim() || !decimal.test(row.amount)))
      return 'Проверьте начальные расходы профиля.';
    if (!confirmed) return 'Подтвердите проверку ставок и правил для вашей компании.';
    return '';
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const problem = check();
    setValidation(problem);
    if (problem) return;
    try {
      const body = {
        name: name.trim(), effective_from: today(), effective_until: effectiveUntil || null,
        reason: reason.trim(), ...(initial ? { previous_id: initial.id } : {}),
        definition: {
          ...definition,
          vat_rate: definition.vat_rate.replace(',', '.'),
          financing_annual_rate: definition.financing_annual_rate.replace(',', '.'),
          default_markup_coefficient: definition.default_markup_coefficient.replace(',', '.'),
          customs_rules: definition.customs_rules.map((row) => ({ ...row, value: row.value.replace(',', '.') })),
          customs_fee_brackets: definition.customs_fee_brackets.map((row) => ({
            from_amount: row.from_amount.replace(',', '.'), to_amount: row.to_amount ? row.to_amount.replace(',', '.') : null,
            fee: row.fee.replace(',', '.'), valid_from: row.valid_from || null, valid_to: row.valid_to || null,
          })),
          default_expenses: definition.default_expenses.map((row) => ({ ...row, amount: row.amount.replace(',', '.') })),
        },
      };
      const result = await operation.run<Entity>('/profiles', body, 'POST', true);
      if (result) { setDirty(false); onSuccess(); }
    } catch (error) {
      if (error instanceof ApiError && error.field) setValidation(error.message);
    }
  }

  return <Modal title={initial ? 'Новая версия профиля расчёта' : 'Новый профиль расчёта'} wide onClose={close}>
    <form noValidate onSubmit={(event) => void submit(event)}>
      <div className="form-body profile-editor itemized-profile-editor">
        <div className="info-note">Параметры 5%, 73 800 ₽, 4 997 ₽ и НДС 22% взяты из ТЗ. Проверьте их на контрольном примере перед публикацией профиля. Для базы свыше 500 000 ₽ добавьте согласованные диапазоны сбора.</div>
        <ErrorBox error={operation.error || groups.error || (validation ? new Error(validation) : undefined)} />
        <section className="profile-section">
          <h3>Профиль и страна ввоза</h3>
          <div className="form-grid">
            <label className="field wide">Название *<input value={name} onChange={(event) => { setName(event.target.value); setDirty(true); }} /></label>
            <div className="field"><label htmlFor="field-import_country_id">Страна ввоза *</label><DirectorySelect field={countryField} value={definition.import_country_id || ''} onChange={(value) => update({ import_country_id: String(value) || null })} /></div>
            <label className="field">Действует по<input type="date" value={effectiveUntil} onChange={(event) => { setEffectiveUntil(event.target.value); setDirty(true); }} /></label>
            <label className="field wide">Основание изменения *<textarea value={reason} onChange={(event) => { setReason(event.target.value); setDirty(true); }} /></label>
          </div>
        </section>
        <section className="profile-section">
          <h3>Налоги, финансирование и наценка</h3>
          <div className="form-grid">
            <label className="field">Ставка НДС, %<input inputMode="decimal" value={definition.vat_rate} onChange={(event) => update({ vat_rate: event.target.value })} /></label>
            <label className="field">Ставка финансирования, % годовых<input inputMode="decimal" value={definition.financing_annual_rate} onChange={(event) => update({ financing_annual_rate: event.target.value })} /></label>
            <label className="field">База дней<input type="number" min={1} max={366} value={definition.day_basis} onChange={(event) => update({ day_basis: Number(event.target.value) })} /></label>
            <label className="field">Событие начала отсрочки<select value={definition.financing_start_event} onChange={(event) => update({ financing_start_event: event.target.value as ItemizedDefinition['financing_start_event'] })}><option value="delivery">Поставка</option><option value="shipment">Отгрузка</option><option value="invoice">Счёт</option></select></label>
            <label className="field">Наценка по умолчанию, коэффициент<input inputMode="decimal" value={definition.default_markup_coefficient} onChange={(event) => update({ default_markup_coefficient: event.target.value })} /></label>
            <label className="field">Округление<select value={definition.rounding} onChange={(event) => update({ rounding: event.target.value as ItemizedDefinition['rounding'] })}><option value="half_up">0,5 в большую сторону</option><option value="half_even">К ближайшему чётному</option><option value="down">Вниз</option></select></label>
            <label className="profile-confirmation wide"><input type="checkbox" checked={definition.vat_deduction_mode} onChange={(event) => update({ vat_deduction_mode: event.target.checked })} />Учитывать вычет входного НДС</label>
          </div>
        </section>
        <section className="profile-section">
          <h3>Таможенные правила по группам</h3>
          <div className="itemized-rule-list">
            {definition.customs_rules.map((rule, index) => <div className="itemized-rule" key={index}>
              <label className="field">Товарная группа<select value={rule.product_group_slug} onChange={(event) => changeRule(index, { product_group_slug: event.target.value })}>{(groups.data?.items || Object.entries(groupNames).map(([slug, name]) => ({ id: slug, slug, name }))).map((group) => <option key={group.slug} value={group.slug}>{group.name}</option>)}</select></label>
              <label className="field">Правило<select value={rule.type} onChange={(event) => changeRule(index, { type: event.target.value as CustomsRule['type'] })}><option value="PERCENTAGE">Процент</option><option value="FIXED_GROUP">Фиксировано на группу</option><option value="NONE">Без пошлины</option></select></label>
              <label className="field">{rule.type === 'PERCENTAGE' ? 'Ставка, %' : 'Сумма, ₽'}<input inputMode="decimal" value={rule.value} onChange={(event) => changeRule(index, { value: event.target.value })} /></label>
              <Button type="button" variant="ghost" onClick={() => update({ customs_rules: definition.customs_rules.filter((_, n) => n !== index) })}>Удалить</Button>
            </div>)}
          </div>
          <Button type="button" variant="secondary" onClick={() => update({ customs_rules: [...definition.customs_rules, { product_group_slug: '', type: 'NONE', value: '0' }] })}>+ Добавить правило</Button>
        </section>
        <section className="profile-section">
          <h3>Таможенный сбор по диапазонам</h3>
          <div className="itemized-rule-list">
            {definition.customs_fee_brackets.map((bracket, index) => <div className="itemized-rule" key={index}>
              <label className="field">От, ₽<input inputMode="decimal" value={bracket.from_amount} onChange={(event) => changeBracket(index, { from_amount: event.target.value })} /></label>
              <label className="field">До, ₽ (пусто — без предела)<input inputMode="decimal" value={bracket.to_amount || ''} onChange={(event) => changeBracket(index, { to_amount: event.target.value })} /></label>
              <label className="field">Сбор, ₽<input inputMode="decimal" value={bracket.fee} onChange={(event) => changeBracket(index, { fee: event.target.value })} /></label>
              <label className="field">Действует с<input type="date" value={bracket.valid_from || ''} onChange={(event) => changeBracket(index, { valid_from: event.target.value || null })} /></label>
              <label className="field">Действует по<input type="date" value={bracket.valid_to || ''} onChange={(event) => changeBracket(index, { valid_to: event.target.value || null })} /></label>
              <Button type="button" variant="ghost" onClick={() => update({ customs_fee_brackets: definition.customs_fee_brackets.filter((_, n) => n !== index) })}>Удалить</Button>
            </div>)}
          </div>
          <Button type="button" variant="secondary" onClick={() => update({ customs_fee_brackets: [...definition.customs_fee_brackets, { from_amount: '', to_amount: '', fee: '' }] })}>+ Добавить диапазон</Button>
        </section>
        <section className="profile-section">
          <h3>Начальные расходы профиля</h3>
          <p>Эти статьи появятся в каждом новом расчёте и останутся доступными для изменения или удаления.</p>
          <div className="inline-actions">
            <select aria-label="Вид расхода" value={expenseTypeId} onChange={(event) => setExpenseTypeId(event.target.value)}>
              <option value="">Выберите вид расхода</option>
              {(expenseTypes.data?.items || []).map((row) => <option key={row.id} value={row.id}>{String(row.name)}</option>)}
            </select>
            <Button type="button" variant="secondary" disabled={!expenseTypeId} onClick={addExpenseType}>Добавить из справочника</Button>
          </div>
          {Boolean(expenseTypes.error) && <p className="field-error">Не удалось загрузить виды расходов.</p>}
          <div className="itemized-rule-list">
            {definition.default_expenses.map((expense, index) => <div className="itemized-rule" key={`${expense.name}-${index}`}>
              <strong>{expense.name}</strong>
              <label className="field">{expense.calculation_type === 'PERCENTAGE' ? 'Ставка, %' : 'Сумма'}
                <input inputMode="decimal" value={expense.amount} onChange={(event) => update({ default_expenses: definition.default_expenses.map((row, n) => n === index ? { ...row, amount: event.target.value } : row) })} />
              </label>
              <span>{expense.currency} · {expense.method}</span>
              <Button type="button" variant="ghost" onClick={() => update({ default_expenses: definition.default_expenses.filter((_, n) => n !== index) })}>Удалить</Button>
            </div>)}
          </div>
        </section>
        <section className="profile-section">
          <h3>Коммерческое предложение</h3>
          <label className="field wide">Название документа<input value={definition.template.title} onChange={(event) => update({ template: { ...definition.template, title: event.target.value } })} /></label>
          <label className="profile-confirmation"><input type="checkbox" checked={definition.template.show_cas} onChange={(event) => update({ template: { ...definition.template, show_cas: event.target.checked } })} />Показывать CAS</label>
        </section>
        <label className="profile-confirmation"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />Я проверил ставки и правила расчёта для своей компании</label>
      </div>
      <div className="modal-footer"><Button type="button" variant="secondary" onClick={close}>Отмена</Button><Button type="submit" busy={operation.busy}>Сохранить черновик</Button></div>
    </form>
  </Modal>;
}
