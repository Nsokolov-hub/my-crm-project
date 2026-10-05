/** Pickup belongs to the wave; last-mile delivery belongs to the selected calculation. */
export function normalizeLogisticsExpense<T extends Record<string, unknown>>(row: T) {
  let stage = String(row.stage || 'GENERAL');
  if (stage === 'GENERAL') {
    const name = String(row.name || '')
      .toLocaleLowerCase('ru-RU')
      .trim()
      .replace(/\s+/g, ' ');
    if (
      [
        'логистика рф',
        'логистика внутри рф',
        'вывоз из аэропорта',
        'логистика рф (вывоз из аэропорта)',
      ].includes(name)
    )
      stage = 'DOMESTIC_LOGISTICS';
    else if (
      ['доставка клиенту', 'доставка клиенту в москве', 'ндс доставки сдэк'].includes(name) ||
      name.startsWith('доставка сдэк ')
    )
      stage = 'CLIENT_DELIVERY';
  }
  if (stage === 'DOMESTIC_LOGISTICS' || stage === 'CLIENT_DELIVERY')
    return {
      ...row,
      stage,
      method: 'BY_QUANTITY',
      scope: stage === 'DOMESTIC_LOGISTICS' ? 'WAVE' : 'REQUEST',
    };
  return row;
}
