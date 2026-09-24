import type { Field } from './types';
import { today, units, stages, stageLabels } from './format';
export const clientFields: Field[] = [
  { name: 'name', label: 'Название организации', required: true, wide: true },
  {
    name: 'kind',
    label: 'Тип контрагента',
    type: 'select',
    required: true,
    value: 'client',
    options: [
      { value: 'client', label: 'Клиент' },
      { value: 'supplier', label: 'Поставщик' },
      { value: 'both', label: 'Клиент и поставщик' },
    ],
  },
  { name: 'country', label: 'Страна' },
  { name: 'tax_id', label: 'ИНН / налоговый номер' },
  { name: 'phone', label: 'Телефон' },
  { name: 'email', label: 'Электронная почта', type: 'email' },
  { name: 'owner_id', label: 'Ответственный', type: 'select', source: '/users' },
  {
    name: 'details',
    label: 'Реквизиты и дополнительные сведения',
    type: 'json',
    value: {},
    help: 'Юридический адрес, банковские реквизиты и другие согласованные поля.',
  },
];
export const contactFields: Field[] = [
  { name: 'name', label: 'Имя контакта', required: true },
  { name: 'position', label: 'Должность' },
  { name: 'phone', label: 'Телефон' },
  { name: 'email', label: 'Электронная почта', type: 'email' },
];
export const requestFields: Field[] = [
  { name: 'title', label: 'Тема заявки', required: true, wide: true },
  {
    name: 'client_id',
    label: 'Клиент',
    required: true,
    type: 'select',
    source: '/counterparties?kind=client',
  },
  { name: 'seller_id', label: 'Организация продавца', type: 'select', source: '/sellers' },
  { name: 'owner_id', label: 'Ответственный', type: 'select', source: '/users' },
  { name: 'due_at', label: 'Желаемый срок', type: 'datetime-local' },
  {
    name: 'is_test',
    label: 'Тестовая заявка',
    type: 'checkbox',
    help: 'Тестовые заявки исключаются из рабочих отчётов.',
  },
];
export const requestEditFields: Field[] = [
  { name: 'seller_id', label: 'Организация продавца', type: 'select', source: '/sellers' },
  { name: 'title', label: 'Тема заявки', required: true },
  {
    name: 'commercial_stage',
    label: 'Коммерческий этап',
    type: 'select',
    options: stages.map((value) => ({ value, label: stageLabels[value] })),
  },
  { name: 'owner_id', label: 'Ответственный', type: 'select', source: '/users' },
  { name: 'due_at', label: 'Срок', type: 'datetime-local' },
  {
    name: 'loss_reason',
    label: 'Причина закрытия без продажи',
    type: 'select',
    source: '/dictionaries/loss_reasons',
    help: 'Выберите причину, если закрываете заявку без продажи.',
  },
  { name: 'reason', label: 'Причина изменения', type: 'textarea', required: true },
];
export const itemFields: Field[] = [
  { name: 'description', label: 'Исходное наименование клиента', required: true, wide: true },
  { name: 'cas', label: 'CAS-номер', placeholder: 'Например, 64-17-5' },
  { name: 'quantity', label: 'Количество', type: 'decimal' },
  { name: 'unit', label: 'Единица', type: 'select', options: units },
  { name: 'purity', label: 'Чистота / марка' },
  { name: 'packaging', label: 'Желаемая фасовка' },
  { name: 'allow_analogue', label: 'Допустим аналог', type: 'checkbox' },
];
export const taskFields: Field[] = [
  { name: 'title', label: 'Название задачи', required: true, wide: true },
  { name: 'assignee_id', label: 'Исполнитель', type: 'select', source: '/users' },
  { name: 'due_at', label: 'Срок', required: true, type: 'datetime-local' },
  {
    name: 'priority',
    label: 'Приоритет',
    type: 'select',
    value: 'normal',
    options: [
      { value: 'normal', label: 'Обычный' },
      { value: 'high', label: 'Высокий' },
      { value: 'low', label: 'Низкий' },
    ],
  },
  {
    name: 'entity_type',
    label: 'Связанный объект',
    type: 'select',
    options: [
      { value: 'request', label: 'Заявка' },
      { value: 'counterparty', label: 'Контрагент' },
      { value: 'wave', label: 'Волна' },
    ],
  },
  { name: 'entity_id', label: 'Идентификатор объекта' },
];
export const callFields: Field[] = [
  {
    name: 'client_id',
    label: 'Клиент',
    type: 'select',
    source: '/counterparties?kind=client',
    required: true,
  },
  {
    name: 'result',
    label: 'Результат звонка',
    type: 'select',
    required: true,
    source: '/dictionaries/call_results',
  },
  { name: 'comment', label: 'Комментарий', type: 'textarea' },
  {
    name: 'next_at',
    label: 'Следующее действие',
    type: 'datetime-local',
    help: 'Для результата «Перезвонить» срок обязателен.',
  },
  {
    name: 'next_assignee_id',
    label: 'Исполнитель следующего действия',
    type: 'select',
    source: '/users',
  },
  {
    name: 'reason',
    label: 'Причина отказа',
    type: 'textarea',
    help: 'Заполните при отказе клиента.',
  },
];
export const productFields: Field[] = [
  { name: 'name', label: 'Название вещества / товара', required: true, wide: true },
  { name: 'cas', label: 'CAS-номер' },
  { name: 'no_cas_reason', label: 'Причина отсутствия CAS' },
  { name: 'manufacturer', label: 'Производитель', required: true },
  { name: 'article', label: 'Артикул' },
  { name: 'purity', label: 'Чистота / марка' },
  { name: 'packaging', label: 'Фасовка' },
  { name: 'unit', label: 'Базовая единица', type: 'select', options: units, required: true },
  { name: 'package_quantity', label: 'Содержимое упаковки', type: 'decimal' },
  { name: 'specification', label: 'Спецификация', type: 'json', value: {} },
];
export const paymentFields: Field[] = [
  { name: 'amount', label: 'Сумма поступления', type: 'decimal', required: true },
  { name: 'currency', label: 'Валюта (ISO)', required: true, placeholder: 'RUB' },
  { name: 'payment_date', label: 'Дата платежа', type: 'date', required: true, value: today() },
  { name: 'number', label: 'Номер платёжного документа', required: true },
  { name: 'external_id', label: 'Банковский / внешний идентификатор' },
  { name: 'comment', label: 'Комментарий', type: 'textarea' },
];
