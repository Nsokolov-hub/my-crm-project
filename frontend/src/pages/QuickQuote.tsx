import { Search } from 'lucide-react';
import { useState } from 'react';
import { DirectorySelect } from '../components/Form';
import { Button, ErrorBox, Modal } from '../components/ui';
import { date, decimal } from '../lib/format';
import { useApi, useCommand, useDebounced, useDirtyProtection } from '../lib/hooks';
import type { Page } from '../lib/types';

type LastQuote = {
  quote_item_id: string;
  packing_id: string;
  supplier_id: string;
  supplier_name: string;
  unit_price: string;
  currency_id: string;
  currency_code: string;
  delivery_days: number | null;
  quoted_at: string;
  quote_number: string;
  request_number: string;
};
type Candidate = {
  nomenclature_id: string;
  name: string;
  article: string | null;
  manufacturer: string | null;
  cas: string | null;
  product_group_name: string | null;
  packings: { id: string; name: string; last: LastQuote | null }[];
  last: LastQuote | null;
  request_item_id: string | null;
  request_quantity: string | null;
};
type Draft = {
  supplier_id: string;
  packing_id: string;
  quantity: string;
  unit_price: string;
  currency_id: string;
  delivery_days: string;
  source_request_item_id: string;
};

const plain = (value: unknown) =>
  value === null || value === undefined ? '' : String(Number(value)).replace('.', ',');
const number = (value: string) => value.replace(/\s/g, '').replace(',', '.');

function fromLast(last: LastQuote | null): Partial<Draft> {
  return last
    ? {
        supplier_id: last.supplier_id,
        unit_price: plain(last.unit_price),
        currency_id: last.currency_id,
        delivery_days: last.delivery_days === null ? '' : String(last.delivery_days),
      }
    : {};
}

/**
 * Business remark of 22 October 2026: a quote typed by hand in a few clicks, e.g. for a position moved from
 * another invoice. The product is found by name or article; supplier, price, currency and term come from its
 * latest quote and may be corrected; the saved position is at once available to a calculation.
 */
export function QuickQuoteDialog({
  requestId,
  onClose,
  onSaved,
}: {
  requestId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState('');
  const [article, setArticle] = useState('');
  const query = useDebounced(
    `name=${encodeURIComponent(name.trim())}&article=${encodeURIComponent(article.trim())}`,
  );
  const searching = name.trim().length >= 2 || article.trim().length >= 2;
  const candidates = useApi<{ items: Candidate[]; prices: boolean }>(
    searching ? `/requests/${requestId}/quote-candidates?${query}` : null,
  );
  const currencies = useApi<Page>('/currencies?page_size=100');
  const positions = useApi<Page>(`/requests/${requestId}/items?page_size=100`);
  const operation = useCommand();
  const [chosen, setChosen] = useState<Candidate>();
  const [draft, setDraft] = useState<Draft>();
  const [problem, setProblem] = useState('');
  useDirtyProtection(Boolean(chosen));

  function choose(candidate: Candidate) {
    const packing = candidate.packings.find((row) => row.last) || candidate.packings[0];
    setChosen(candidate);
    setProblem('');
    setDraft({
      supplier_id: '',
      currency_id: '',
      unit_price: '',
      delivery_days: '',
      ...fromLast(packing?.last || candidate.last),
      packing_id: packing?.id || '',
      quantity: candidate.request_quantity ? String(Number(candidate.request_quantity)) : '1',
      source_request_item_id: candidate.request_item_id || '',
    });
  }
  function patch(values: Partial<Draft>) {
    setDraft((current) => (current ? { ...current, ...values } : current));
    setProblem('');
  }
  const currency = currencies.data?.items.find((row) => row.id === draft?.currency_id);
  const packing = chosen?.packings.find((row) => row.id === draft?.packing_id);
  async function save() {
    if (!chosen || !draft) return;
    const price = Number(number(draft.unit_price));
    if (
      !draft.supplier_id ||
      !draft.packing_id ||
      !draft.currency_id ||
      draft.unit_price === '' ||
      draft.delivery_days === ''
    )
      return setProblem('Заполните поставщика, фасовку, цену, валюту и срок поставки.');
    if (!Number.isInteger(Number(draft.quantity)) || Number(draft.quantity) <= 0)
      return setProblem('Количество — целое число фасовок больше нуля.');
    if (!Number.isFinite(price) || price < 0)
      return setProblem('Цена не может быть отрицательной.');
    if (!Number.isInteger(Number(draft.delivery_days)) || Number(draft.delivery_days) < 0)
      return setProblem('Срок поставки — целое число дней.');
    try {
      await operation.run(
        `/requests/${requestId}/quote-sheets`,
        {
          supplier_id: draft.supplier_id,
          items: [
            {
              source_request_item_id: draft.source_request_item_id || null,
              nomenclature_id: chosen.nomenclature_id,
              packing_id: draft.packing_id,
              quantity: draft.quantity,
              unit_price: number(draft.unit_price),
              currency_id: draft.currency_id,
              delivery_days: Number(draft.delivery_days),
            },
          ],
        },
        'POST',
      );
      onSaved();
    } catch {
      /* ErrorBox shows the failure. */
    }
  }
  return (
    <Modal title="Быстрая квота" wide onClose={onClose}>
      <div className="form-body quick-quote">
        <p className="muted">
          Найдите товар по наименованию или артикулу. Поставщик, цена, валюта и срок подставятся из
          последней квоты этого товара — проверьте их, дополните недостающее и сохраните. Позиция
          сразу будет доступна в расчёте.
        </p>
        <ErrorBox error={operation.error || candidates.error} />
        {!chosen && (
          <>
            <div className="quick-quote-search">
              <label className="field">
                Наименование
                <span className="search-input">
                  <Search size={15} />
                  <input
                    autoFocus
                    value={name}
                    placeholder="Например, Avilamycin"
                    onChange={(event) => setName(event.target.value)}
                  />
                </span>
              </label>
              <label className="field">
                Артикул
                <span className="search-input">
                  <Search size={15} />
                  <input
                    value={article}
                    placeholder="Например, TRC-A794655"
                    onChange={(event) => setArticle(event.target.value)}
                  />
                </span>
              </label>
            </div>
            {searching && candidates.data && !candidates.data.items.length && (
              <p className="muted">
                Такого товара нет в номенклатуре. Добавьте его через «Новая квота» или загрузку
                файла квот.
              </p>
            )}
            {Boolean(candidates.data?.items.length) && (
              <ul className="quick-quote-results" aria-label="Найденные товары">
                {candidates.data!.items.map((candidate) => (
                  <li key={candidate.nomenclature_id}>
                    <button type="button" onClick={() => choose(candidate)}>
                      <strong>{candidate.name}</strong>
                      <small>
                        {[
                          candidate.article,
                          candidate.manufacturer,
                          candidate.packings.map((row) => row.name).join(', '),
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </small>
                      <small>
                        {candidate.last
                          ? `Последняя квота: ${decimal(candidate.last.unit_price)} ${candidate.last.currency_code} · ${candidate.last.supplier_name} · ${date(candidate.last.quoted_at)}`
                          : candidates.data!.prices
                            ? 'Квот ещё не было'
                            : ''}
                        {candidate.request_item_id ? ' · есть в этой заявке' : ''}
                      </small>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {chosen && draft && (
          <>
            <div className="quick-quote-product">
              <div>
                <strong>{chosen.name}</strong>
                <small>
                  {[chosen.article, chosen.manufacturer, chosen.cas, chosen.product_group_name]
                    .filter(Boolean)
                    .join(' · ')}
                </small>
              </div>
              <Button type="button" variant="ghost" onClick={() => setChosen(undefined)}>
                Другой товар
              </Button>
            </div>
            <div className="form-grid">
              <div className="field">
                <label htmlFor="field-supplier_id">Поставщик</label>
                <DirectorySelect
                  field={{
                    name: 'supplier_id',
                    label: 'Поставщик',
                    type: 'select',
                    source: '/counterparties?kind=supplier',
                    required: true,
                  }}
                  value={draft.supplier_id}
                  onChange={(value) => patch({ supplier_id: String(value || '') })}
                />
              </div>
              <label className="field">
                Фасовка
                <select
                  value={draft.packing_id}
                  onChange={(event) => {
                    const next = chosen.packings.find((row) => row.id === event.target.value);
                    patch({ packing_id: event.target.value, ...fromLast(next?.last || null) });
                  }}
                >
                  <option value="">Выберите фасовку</option>
                  {chosen.packings.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Количество, фасовок
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={draft.quantity}
                  onChange={(event) => patch({ quantity: event.target.value })}
                />
              </label>
              <label className="field">
                Цена за фасовку
                <input
                  inputMode="decimal"
                  value={draft.unit_price}
                  onChange={(event) => patch({ unit_price: event.target.value })}
                />
              </label>
              <label className="field">
                Валюта
                <select
                  value={draft.currency_id}
                  onChange={(event) => patch({ currency_id: event.target.value })}
                >
                  <option value="">Выберите валюту</option>
                  {currencies.data?.items.map((row) => (
                    <option key={row.id} value={row.id}>
                      {String(row.code)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Срок поставки, дней
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={draft.delivery_days}
                  onChange={(event) => patch({ delivery_days: event.target.value })}
                />
              </label>
              <label className="field wide">
                Позиция заявки
                <select
                  value={draft.source_request_item_id}
                  onChange={(event) => patch({ source_request_item_id: event.target.value })}
                >
                  <option value="">Новая позиция по квоте</option>
                  {positions.data?.items
                    .filter((row) => !row.archived)
                    .map((row, index) => (
                      <option key={row.id} value={row.id}>
                        {index + 1}. {String(row.nomenclature_name || row.description)}
                      </option>
                    ))}
                </select>
              </label>
            </div>
            {(packing?.last || chosen.last) && (
              <p className="muted">
                Подставлено из квоты {(packing?.last || chosen.last)!.quote_number} по заявке №
                {(packing?.last || chosen.last)!.request_number} от{' '}
                {date((packing?.last || chosen.last)!.quoted_at)}.
              </p>
            )}
            <p className="info-note" role="status">
              Будет записана квота: {chosen.name} {packing?.name || ''} × {draft.quantity || '—'}
              {' · '}
              {draft.unit_price || '—'} {String(currency?.code || '')} за фасовку · срок{' '}
              {draft.delivery_days || '—'} дн.
            </p>
            {problem && <p className="field-error">{problem}</p>}
          </>
        )}
      </div>
      <div className="modal-footer">
        <Button type="button" variant="ghost" onClick={onClose}>
          Отмена
        </Button>
        <Button type="button" disabled={!chosen} busy={operation.busy} onClick={() => void save()}>
          Сохранить квоту
        </Button>
      </div>
    </Modal>
  );
}
