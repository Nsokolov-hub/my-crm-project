import { History } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, ErrorBox } from '../../components/ui';
import { date, decimal, units } from '../../lib/format';
import { useApi } from '../../lib/hooks';

type Sale = {
  execution_id: string;
  request_id: string;
  request_number: string;
  article: string | null;
  description: string;
  packing: string | null;
  manufacturer: string | null;
  unit_price: string;
  currency: string;
  unit: string;
  sold_at: string;
  price_includes_vat: boolean;
};

/** Previous confirmed sales to this buyer for the positions selected in the calculation. */
export function SalePriceHistory({
  requestId,
  quoteItemIds,
}: {
  requestId: string;
  quoteItemIds: string[];
}) {
  const [collapsed, setCollapsed] = useState(false);
  const query =
    quoteItemIds.length <= 100
      ? `?quote_item_ids=${encodeURIComponent([...quoteItemIds].sort().join(','))}`
      : '';
  const history = useApi<{ items: Sale[] }>(
    quoteItemIds.length ? `/requests/${requestId}/sale-price-history${query}` : null,
  );
  const items = history.data?.items || [];
  return (
    <aside
      className={`sale-price-history${collapsed ? ' collapsed' : ''}`}
      aria-label="История продаж клиенту"
    >
      <button
        type="button"
        className="side-note-toggle"
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((value) => !value)}
      >
        <History size={15} /> История продаж клиенту
        <span aria-hidden>{collapsed ? '‹' : '›'}</span>
      </button>
      {!collapsed && (
        <div className="sale-price-history-body">
          <p className="muted">Прежние продажи этому клиенту по выбранным артикулам.</p>
          {history.loading && <p role="status">Загрузка истории продаж…</p>}
          <ErrorBox error={history.error} />
          {history.error ? (
            <Button type="button" variant="secondary" onClick={history.refresh}>
              Повторить загрузку
            </Button>
          ) : !history.loading && !items.length ? (
            <p className="muted">Предыдущих продаж выбранных товаров не найдено.</p>
          ) : items.length > 0 ? (
            <div className="sale-price-history-scroll">
              <table className="sale-price-history-table">
                <thead>
                  <tr>
                    <th>Товар / дата</th>
                    <th>Цена за ед.</th>
                    <th>Заявка</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((sale, index) => (
                    <tr key={`${sale.execution_id}-${index}`}>
                      <td>
                        <strong>{sale.article || sale.description}</strong>
                        <small>
                          {[sale.manufacturer, sale.packing].filter(Boolean).join(' · ')}
                        </small>
                        <small>{date(sale.sold_at)}</small>
                      </td>
                      <td>
                        <strong>
                          {decimal(sale.unit_price)} {sale.currency}
                        </strong>
                        <small>
                          за{' '}
                          {units.find((unit) => unit.value === sale.unit)?.label ||
                            sale.unit ||
                            'шт.'}
                        </small>
                        <small>{sale.price_includes_vat ? 'с НДС' : 'без НДС'}</small>
                      </td>
                      <td>
                        <Link to={`/requests/${sale.request_id}`} target="_blank" rel="noreferrer">
                          №{sale.request_number}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      )}
    </aside>
  );
}
