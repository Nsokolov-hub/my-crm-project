import { Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { DirectorySelect } from './Form';
import { Button, ErrorBox, Modal } from './ui';
import { ApiError } from '../lib/api';
import { useCommand, useDirtyProtection } from '../lib/hooks';
import type { Field } from '../lib/types';

type Bracket = { from_amount: string; to_amount: string; fee: string; valid_from: string; valid_to: string };
type Values = {
  name: string;
  calculation_type: string;
  default_value: string;
  currency_id: string;
  distribution_method: string;
  stage: string;
  percent_base: string;
  include_in_cost: boolean;
  include_in_cash: boolean;
};
type SelectKey = 'calculation_type' | 'distribution_method' | 'stage' | 'percent_base';

const currencyField: Field = {
  name: 'currency_id', label: 'Валюта', type: 'select', source: '/currencies', labelKey: 'code', required: true,
  create: { title: 'Новая валюта', endpoint: '/currencies', fields: [
    { name: 'code', label: 'Код ISO 3', required: true, help: 'Три заглавные латинские буквы, например INR.' },
    { name: 'name', label: 'Название', required: true },
  ] },
};
const calculationTypes = [
  ['FIXED', 'Фиксированная сумма'], ['PERCENTAGE', 'Процент'],
  ['BRACKET', 'По диапазону'], ['MANUAL', 'Ручной ввод'],
];
const distributions = [
  ['BY_QUANTITY', 'По количеству'], ['BY_PURCHASE_VALUE', 'По закупочной стоимости'],
  ['EQUALLY_BY_POSITION', 'Поровну по строкам'], ['BY_WEIGHT', 'По весу'], ['MANUAL', 'Вручную'],
];
const bases = [
  ['PURCHASE', 'Закупка'], ['CUSTOMS_BASE', 'Таможенная база'],
  ['DUTY', 'Пошлина'], ['COST', 'Себестоимость'],
];
const newBracket = (): Bracket => ({ from_amount: '', to_amount: '', fee: '', valid_from: '', valid_to: '' });
const decimal = (value: string) => /^\d+(?:\.\d{1,8})?$/.test(value);

export function ExpenseTypeEditor({ onClose, onSuccess }: { onClose: () => void; onSuccess: () => void }) {
  const [values, setValues] = useState<Values>({
    name: '', calculation_type: 'FIXED', default_value: '0', currency_id: '',
    distribution_method: 'BY_QUANTITY', stage: 'GENERAL', percent_base: '',
    include_in_cost: true, include_in_cash: true,
  });
  const [brackets, setBrackets] = useState<Bracket[]>([newBracket()]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const command = useCommand();
  useDirtyProtection(dirty);

  function change<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: '' }));
    setDirty(true);
  }
  function changeBracket(index: number, key: keyof Bracket, value: string) {
    setBrackets((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, [key]: value } : row));
    setErrors((current) => ({ ...current, brackets: '' }));
    setDirty(true);
  }
  function close() {
    if (!command.busy && (!dirty || window.confirm('Есть несохранённые изменения. Закрыть форму?'))) onClose();
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const next: Record<string, string> = {};
    if (!values.name.trim()) next.name = 'Укажите название расхода';
    if (!values.currency_id) next.currency_id = 'Выберите валюту';
    if (!decimal(values.default_value)) next.default_value = 'Укажите неотрицательное число';
    if (['PERCENTAGE', 'BRACKET'].includes(values.calculation_type) && !values.percent_base)
      next.percent_base = 'Выберите базу начисления';
    if (values.calculation_type === 'BRACKET') {
      if (!brackets.length) next.brackets = 'Добавьте хотя бы один диапазон';
      for (const row of brackets) {
        if (!decimal(row.from_amount) || !decimal(row.fee) || (row.to_amount && !decimal(row.to_amount))) {
          next.brackets = 'Заполните начало, конец и сумму диапазона числами';
          break;
        }
        if (row.to_amount && Number(row.to_amount) < Number(row.from_amount)) {
          next.brackets = 'Конец диапазона меньше начала';
          break;
        }
        if (row.valid_from && row.valid_to && row.valid_to < row.valid_from) {
          next.brackets = 'Дата окончания раньше даты начала';
          break;
        }
      }
    }
    setErrors(next);
    if (Object.keys(next).length) return;
    try {
      const result = await command.run('/expense-types', {
        ...values,
        name: values.name.trim(),
        percent_base: ['PERCENTAGE', 'BRACKET'].includes(values.calculation_type) ? values.percent_base : null,
        brackets: values.calculation_type === 'BRACKET' ? brackets.map((row) => ({
          from_amount: row.from_amount, to_amount: row.to_amount || null, fee: row.fee,
          valid_from: row.valid_from || null, valid_to: row.valid_to || null,
        })) : [],
      }, 'POST', true);
      if (result) { setDirty(false); onSuccess(); }
    } catch (error) {
      if (error instanceof ApiError && error.field) setErrors((current) => ({ ...current, [error.field!]: error.message }));
    }
  }
  const select = (key: SelectKey, label: string, options: string[][]) => (
    <div className="field" key={key}>
      <label htmlFor={`expense-${key}`}>{label} <span>*</span></label>
      <select id={`expense-${key}`} value={values[key]} onChange={(event) => change(key, event.target.value)}>
        {key === 'percent_base' && <option value="">Выберите…</option>}
        {options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
      </select>
      {errors[key] && <p className="field-error">{errors[key]}</p>}
    </div>
  );
  return <Modal title="Добавить вид расхода" wide onClose={close}>
    <form onSubmit={(event) => void submit(event)} noValidate>
      <div className="form-body">
        <ErrorBox error={command.error} />
        <div className="form-grid">
          <div className="field"><label htmlFor="expense-name">Название расхода <span>*</span></label>
            <input id="expense-name" maxLength={120} value={values.name} onChange={(event) => change('name', event.target.value)} />
            {errors.name && <p className="field-error">{errors.name}</p>}
          </div>
          {select('calculation_type', 'Тип расчёта', calculationTypes)}
          <div className="field"><label htmlFor="expense-value">Сумма или ставка по умолчанию <span>*</span></label>
            <input id="expense-value" inputMode="decimal" value={values.default_value} onChange={(event) => change('default_value', event.target.value.replace(',', '.'))} />
            {errors.default_value && <p className="field-error">{errors.default_value}</p>}
          </div>
          <div className="field"><label htmlFor="field-currency_id">Валюта <span>*</span></label>
            <DirectorySelect field={currencyField} value={values.currency_id} error={errors.currency_id} onChange={(value) => change('currency_id', String(value))} />
            {errors.currency_id && <p className="field-error">{errors.currency_id}</p>}
          </div>
          {select('distribution_method', 'Распределение', distributions)}
          {select('stage', 'Этап', [['GENERAL', 'Общий расход'], ['INTERNATIONAL_LOGISTICS', 'Международная логистика']])}
          {['PERCENTAGE', 'BRACKET'].includes(values.calculation_type) && select('percent_base', 'База начисления', bases)}
          {values.calculation_type === 'BRACKET' && <div className="field wide">
            <label>Диапазоны расходов <span>*</span></label>
            <div className="expense-brackets">
              {brackets.map((row, index) => <div className="expense-bracket" key={index}>
                <label>От <input inputMode="decimal" aria-label={`Диапазон ${index + 1}: от`} value={row.from_amount} onChange={(event) => changeBracket(index, 'from_amount', event.target.value.replace(',', '.'))} /></label>
                <label>До <input inputMode="decimal" aria-label={`Диапазон ${index + 1}: до`} value={row.to_amount} onChange={(event) => changeBracket(index, 'to_amount', event.target.value.replace(',', '.'))} placeholder="Без верхней границы" /></label>
                <label>Сумма <input inputMode="decimal" aria-label={`Диапазон ${index + 1}: сумма`} value={row.fee} onChange={(event) => changeBracket(index, 'fee', event.target.value.replace(',', '.'))} /></label>
                <label>Действует с <input type="date" value={row.valid_from} onChange={(event) => changeBracket(index, 'valid_from', event.target.value)} /></label>
                <label>Действует до <input type="date" value={row.valid_to} onChange={(event) => changeBracket(index, 'valid_to', event.target.value)} /></label>
                <button type="button" className="icon-button" aria-label={`Удалить диапазон ${index + 1}`} onClick={() => { setBrackets((current) => current.filter((_, rowIndex) => rowIndex !== index)); setDirty(true); }}><Trash2 size={17} /></button>
              </div>)}
            </div>
            {errors.brackets && <p className="field-error">{errors.brackets}</p>}
            <Button type="button" variant="secondary" onClick={() => { setBrackets((current) => [...current, newBracket()]); setDirty(true); }}><Plus size={16} /> Добавить диапазон</Button>
          </div>}
          <label className="field checkbox-field"><input type="checkbox" checked={values.include_in_cost} onChange={(event) => change('include_in_cost', event.target.checked)} /> Включать в себестоимость</label>
          <label className="field checkbox-field"><input type="checkbox" checked={values.include_in_cash} onChange={(event) => change('include_in_cash', event.target.checked)} /> Включать в денежную потребность</label>
        </div>
      </div>
      <div className="modal-footer"><Button type="button" variant="secondary" onClick={close}>Отмена</Button><Button type="submit" busy={command.busy}>Сохранить</Button></div>
    </form>
  </Modal>;
}
