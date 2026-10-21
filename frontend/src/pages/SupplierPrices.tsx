import { Download, Search } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { Button, DataTable, ErrorBox, Loading, Pagination, Section } from '../components/ui';
import { download } from '../lib/api';
import { date, decimal } from '../lib/format';
import { useApi } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';

type PriceRow = Entity & {
  quoted_at: string;
  quote_number: string;
  request_id: string;
  request_number: string;
  client_name: string;
  supplier_name: string;
  nomenclature_name: string;
  article: string | null;
  manufacturer: string | null;
  packing: string;
  unit_price: string;
  currency: string;
  unit_price_rub: string | null;
  delivery_days: number | null;
};
const PAGE_SIZE = 100;

/**
 * Business remark of 21 October 2026: the base of supplier prices from quotes, to look through and to take
 * away as a spreadsheet with all prices and the best price of each product.
 */
export function SupplierPricesReport({ from, to }: { from: string; to: string }) {
  const auth = useAuth();
  const [draft, setDraft] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [error, setError] = useState<unknown>();
  const query = `date_from=${from}&date_to=${to}&q=${encodeURIComponent(q)}`;
  const prices = useApi<Page<PriceRow> & { rates_profile: string | null }>(
    `/analytics/supplier-prices?${query}&page=${page}&page_size=${PAGE_SIZE}`,
  );
  return (
    <Section
      title="Цены из квот поставщиков"
      description={
        prices.data?.rates_profile
          ? `Цены в рублях — по курсам профиля расчёта «${prices.data.rates_profile}».`
          : 'Все позиции квот за период; удалённые квоты и тестовые заявки не входят.'
      }
      action={
        auth.can('exports.download') ? (
          <Button
            variant="secondary"
            onClick={() =>
              void download(
                `/analytics/supplier-prices.xlsx?${query}`,
                `База цен поставщиков ${from} — ${to}.xlsx`,
              ).catch(setError)
            }
          >
            <Download size={17} />
            Выгрузить в Excel
          </Button>
        ) : undefined
      }
    >
      <form
        className="report-search"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setPage(1);
          setQ(draft.trim());
        }}
      >
        <label>
          <Search size={16} />
          <input
            aria-label="Поиск по товару, артикулу, CAS, производителю или поставщику"
            placeholder="Товар, артикул, CAS, производитель или поставщик"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
        <Button type="submit" variant="secondary">
          Найти
        </Button>
      </form>
      <p className="muted">
        В Excel — лист со всеми ценами и лист «Лучшие цены»: самая низкая цена в рублях по каждому
        товару и фасовке у разных поставщиков.
      </p>
      <ErrorBox error={error || prices.error} retry={prices.refresh} />
      {prices.loading ? (
        <Loading />
      ) : (
        <DataTable<PriceRow>
          rows={prices.data?.items || []}
          columns={[
            { key: 'quoted_at', label: 'Дата', render: (row) => date(row.quoted_at) },
            {
              key: 'nomenclature_name',
              label: 'Товар',
              render: (row) => (
                <span className="stacked">
                  <strong>{row.nomenclature_name}</strong>
                  <small>
                    {[row.article, row.manufacturer, row.packing].filter(Boolean).join(' · ')}
                  </small>
                </span>
              ),
            },
            { key: 'supplier_name', label: 'Поставщик' },
            {
              key: 'unit_price',
              label: 'Цена',
              render: (row) => `${decimal(row.unit_price)} ${row.currency}`,
            },
            {
              key: 'unit_price_rub',
              label: 'Цена, ₽',
              render: (row) => (row.unit_price_rub === null ? '—' : decimal(row.unit_price_rub)),
            },
            {
              key: 'delivery_days',
              label: 'Срок, дней',
              render: (row) => (row.delivery_days === null ? '—' : String(row.delivery_days)),
            },
            {
              key: 'request_number',
              label: 'Заявка',
              render: (row) => (
                <span className="stacked">
                  <Link to={`/requests/${row.request_id}`}>№{row.request_number}</Link>
                  <small>{row.client_name}</small>
                </span>
              ),
            },
          ]}
        />
      )}
      <Pagination
        page={page}
        total={prices.data?.total || 0}
        pageSize={PAGE_SIZE}
        onChange={setPage}
      />
    </Section>
  );
}
