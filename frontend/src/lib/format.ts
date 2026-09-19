import type { Entity } from './types';
export const stageLabels: Record<string, string> = {
  new: 'Новая',
  clarification: 'Уточнение',
  collecting_quotes: 'Сбор квот',
  quotes: 'Сбор квот',
  calculation: 'Расчёт',
  proposal_sent: 'КП отправлено',
  composition_agreed: 'Состав согласован',
  accepted: 'Состав согласован',
  awaiting_payment: 'Ожидается оплата',
  sale_confirmed: 'Продажа подтверждена',
  won: 'Продажа подтверждена',
  closed_lost: 'Закрыта без продажи',
  lost: 'Закрыта без продажи',
  assigned: 'Назначена',
  in_progress: 'В работе',
  completed: 'Завершена',
  cancelled: 'Отменена',
  draft: 'Черновик',
  issued: 'Выпущено',
  sent: 'Отправлено',
  approved: 'Согласовано',
  pending: 'Ожидает решения',
  returned: 'На доработке',
  rejected: 'Отклонено',
  confirmed: 'Подтверждено',
  declared: 'Заявлено',
  planned: 'Планируется',
  assembling: 'Комплектуется',
  closed: 'Закрыта',
  shipped: 'Отправлена',
  arrived: 'Прибыла',
  delivered: 'Доставлено',
  verified: 'Проверен',
  needs_review: 'Требует проверки',
  requires_review: 'Требует проверки',
  client: 'Клиент',
  supplier: 'Поставщик',
  both: 'Клиент и поставщик',
  normal: 'Обычный',
  high: 'Высокий',
  low: 'Низкий',
  no_answer: 'Не дозвонились',
  callback: 'Перезвонить',
  interested: 'Есть интерес',
  request_received: 'Получен запрос',
  invalid_contact: 'Неверный контакт',
  refused: 'Отказ',
  wrong_contact: 'Неверный контакт',
  own: 'Свои',
  shared: 'Предоставленные',
  all: 'Все',
  group: 'Групповой',
  direct: 'Личный',
  request: 'Заявка',
  wave: 'Волна',
  unpaid: 'Не оплачен',
  partially_paid: 'Частично оплачен',
  paid: 'Оплачен',
  ordered: 'Заказ подтверждён',
  ready: 'Готов к отправке',
  claim_opened: 'Претензия открыта',
  claim_resolved: 'Претензия закрыта',
  correction: 'Корректировка',
};
export const stages = [
  'new',
  'clarification',
  'collecting_quotes',
  'calculation',
  'proposal_sent',
  'composition_agreed',
  'awaiting_payment',
  'sale_confirmed',
  'closed_lost',
];
export const units = [
  { value: 'g', label: 'г' },
  { value: 'kg', label: 'кг' },
  { value: 'mg', label: 'мг' },
  { value: 'l', label: 'л' },
  { value: 'ml', label: 'мл' },
  { value: 'pcs', label: 'шт.' },
];
export function date(value: unknown, withTime = false) {
  if (!value) return '—';
  const parsed = new Date(String(value));
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : new Intl.DateTimeFormat('ru-RU', {
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
        timeZone: 'Europe/Moscow',
      }).format(parsed);
}
export function decimal(value: unknown) {
  if (value === null || value === undefined || value === '') return '—';
  const [whole, fraction] = String(value).split('.');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ')}${fraction ? ',' + fraction.replace(/0+$/, '') : ''}`.replace(
    /,$/,
    '',
  );
}
export function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет';
  if (Array.isArray(value)) return value.map(display).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return stageLabels[String(value)] || String(value);
}
export function label(row: Entity): string {
  return String(
    row.name || row.title || row.number || row.description || row.email || row.id.slice(0, 8),
  );
}
export function nowLocal() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
export function today() {
  return nowLocal().slice(0, 10);
}
