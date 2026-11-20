import { Badge } from './ui';
import { date } from '../lib/format';

type Offer = {
  source: string;
  source_name: string;
  offer_id: string;
  catalog_number: string;
  pack: string | null;
  match: 'exact_pack' | 'product_only';
  seen_at: string;
  valid_until: string;
  quantity: number | null;
  url?: string | null;
};
export type StockSnapshotData = {
  version: 1;
  captured_at: string;
  availability: 'in_stock' | 'on_order' | 'unknown';
  origin: 'own' | 'supplier' | 'none' | 'unknown';
  packaging_match: 'exact' | 'product_only' | 'custom' | 'unmatched';
  offers: Offer[];
};

/** Historical evidence for employees; kept separate from the line sent to a customer or supplier. */
export function StockSnapshot({ value }: { value: unknown }) {
  const snapshot = value as StockSnapshotData | null | undefined;
  if (!snapshot || snapshot.version !== 1)
    return <span className="muted">Наличие не зафиксировано</span>;
  const exact = snapshot.packaging_match === 'exact';
  const label =
    snapshot.availability === 'in_stock'
      ? snapshot.origin === 'own'
        ? 'Было на нашем складе'
        : 'Было у поставщика'
      : snapshot.availability === 'on_order'
        ? 'Под заказ'
        : 'Нужно уточнить';
  return (
    <div className="stock-snapshot">
      <Badge
        value={label}
        tone={snapshot.availability === 'in_stock' && exact ? 'green' : 'amber'}
      />
      <div className="muted">На момент заявки: {date(snapshot.captured_at, true)}</div>
      {!exact && <div>Фасовка не подтверждена — требуется проверка менеджером.</div>}
      {snapshot.offers.length > 0 && (
        <details>
          <summary>Источники наличия ({snapshot.offers.length})</summary>
          <ul>
            {snapshot.offers.map((offer, index) => (
              <li key={`${offer.source}:${offer.offer_id}:${index}`}>
                {offer.url && /^https?:\/\//i.test(offer.url) ? (
                  <a href={offer.url} target="_blank" rel="noreferrer">
                    {offer.source_name}
                  </a>
                ) : (
                  offer.source_name
                )}
                {' · '}
                {offer.catalog_number}
                {offer.pack ? ` · ${offer.pack}` : ''}
                <div>
                  {offer.match === 'exact_pack'
                    ? 'Совпала фасовка'
                    : 'Совпал товар; фасовку уточнить'}
                </div>
                <div>
                  Замечено: {date(offer.seen_at, true)}; срок актуальности:{' '}
                  {date(offer.valid_until, true)}
                </div>
                <div>
                  {offer.quantity == null
                    ? 'Количество не указано'
                    : `Указано упаковок: ${offer.quantity}`}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="muted">
        Снимок не резервирует товар. Перед заказом подтвердите наличие и количество.
      </div>
    </div>
  );
}
