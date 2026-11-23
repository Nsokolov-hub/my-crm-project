import { Download, ExternalLink, PackageCheck, RefreshCw, Search } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { Badge, Button, DataTable, Empty, ErrorBox, Loading, PageHeading } from '../components/ui';
import { download } from '../lib/api';
import { date } from '../lib/format';
import { useApi, useDebounced } from '../lib/hooks';
import type { Entity } from '../lib/types';
import '../styles/stock-catalog.scss';

type Offer = {
  source: string;
  source_name: string;
  offer_id: string;
  catalog_number?: string | null;
  pack?: string | null;
  quantity?: number | string | null;
  seen_at?: string | null;
  valid_until?: string | null;
  url?: string | null;
  match: string;
};
type StockRow = Entity & {
  name: string;
  article?: string | null;
  cas?: string | null;
  manufacturer?: string | null;
  purity?: string | null;
  packaging: string;
  availability: string;
  checked_at?: string | null;
  product_url?: string | null;
  origin?: string | null;
  offers: Offer[];
  fresh?: boolean;
  reason?: string | null;
};
type Source = {
  id: string;
  name: string;
  status: string;
  reason: string;
  last_check?: {
    checked_at?: string | null;
    running?: boolean;
    started_at?: string;
    pending_pages?: number;
    stored_offers?: number;
    fresh_offers?: number;
    exact_packagings?: number;
    pending_review?: number;
    error?: string;
    errors?: number;
    refused?: string | null;
    incomplete?: string | null;
    complete?: boolean;
    offers?: number;
    matched?: number;
    refresh?: {
      running?: boolean;
      started_at?: string | null;
      checked_at?: string | null;
      pending_pages?: number;
      pages?: number;
      offers?: number;
      withdrawn?: number;
      errors?: number;
      refused?: string | null;
      incomplete?: string | null;
      complete?: boolean;
      interval_hours?: number;
      fresh_hours?: number;
      scope?: string;
    };
  } | null;
};
type StockPage = {
  as_of: string;
  items: StockRow[];
  next_cursor?: string | null;
  has_more: boolean;
  sources: Source[];
  can_review: boolean;
};

const SOURCE_STATUS: Record<string, string> = {
  supported: 'Подключён',
  enabled: 'Подключён',
  configured: 'Настроен',
  needs_verification: 'Требует проверки',
  refused: 'Доступ ограничен',
  unsupported: 'Не подключён',
  review_only: 'Проверка менеджером',
  temporarily_unavailable: 'Временно недоступен',
};

const SOURCE_REASON: Record<string, string> = {
  no_stock_evidence: 'Нет подтверждения наличия на открытых страницах',
  no_verified_public_stock_evidence: 'Нет подтверждения наличия на открытых страницах',
  manufacturer_and_article_require_manager_review:
    'Производитель и артикул требуют проверки менеджером',
  robots: 'Автоматическая проверка запрещена источником',
  robots_disallow: 'Автоматическая проверка запрещена источником',
  robots_unavailable: 'Не удалось проверить правила доступа источника',
  captcha: 'Источник запросил проверку CAPTCHA',
  redirect_requires_review: 'Адрес страницы изменился. Требуется проверка менеджера',
  origin_not_allowed: 'Источник ведёт на неподтверждённый адрес',
  path_not_allowed: 'Источник ведёт на неподтверждённую страницу',
  missing_manufacturer_article: 'Не указан производитель или артикул',
  manufacturer_brand_missing: 'Не указан производитель',
  manufacturer_identity_missing: 'Не удалось подтвердить производителя',
  requested_category_empty_using_public_navigation:
    'Выбранный раздел пуст. Проверяется открытый каталог',
};

function sourceReason(value: string) {
  if (SOURCE_REASON[value]) return SOURCE_REASON[value];
  if (/^(401|403|429|451)$/.test(value)) return `Источник ограничил доступ (HTTP ${value})`;
  if (/^[a-z][a-z_]+_(changed|missing|invalid)$/.test(value))
    return 'Структура страницы изменилась. Требуется проверка';
  return value;
}

function SourceCheck({ source }: { source: Source }) {
  const check = source.last_check;
  const refresh = check?.refresh;
  const reason = sourceReason(source.reason);
  return (
    <li>
      <strong>{source.name}</strong>
      <span>{SOURCE_STATUS[source.status] || 'Статус уточняется'}</span>
      {reason && <span>{reason}</span>}
      {check?.refused && (
        <span className="stock-source-warning">
          Доступ ограничен: {sourceReason(check.refused)}
        </span>
      )}
      {check?.incomplete && (
        <span className="stock-source-warning">
          Проверка не завершена: {sourceReason(check.incomplete)}
        </span>
      )}
      {Boolean(check?.errors) && (
        <span className="stock-source-warning">Ошибок: {check!.errors}</span>
      )}
      {check?.error && <span className="stock-source-warning">{sourceReason(check.error)}</span>}
      {check?.running && (
        <span>Идёт обход источников. В очереди страниц: {check.pending_pages ?? 0}</span>
      )}
      {check?.complete === false &&
        !check.running &&
        !check.refused &&
        !check.incomplete &&
        !check.errors && <span className="stock-source-warning">Проверка не завершена</span>}
      {check?.complete === true && <span>Проверка завершена</span>}
      {check?.offers !== undefined && (
        <span>
          Предложений: {check.offers}
          {check.matched !== undefined ? `. Сопоставлено: ${check.matched}` : ''}
        </span>
      )}
      {check?.stored_offers !== undefined && (
        <span>
          Сохранено предложений: {check.stored_offers}
          {check.fresh_offers !== undefined ? `. Свежих: ${check.fresh_offers}` : ''}
          {check.exact_packagings !== undefined
            ? `. Точных фасовок: ${check.exact_packagings}`
            : ''}
          {check.pending_review !== undefined ? `. На проверке: ${check.pending_review}` : ''}
        </span>
      )}
      {refresh && (
        <>
          <span>
            Перепроверка известного наличия — каждые {refresh.interval_hours ?? 2} ч. Подтверждение
            действует не более {refresh.fresh_hours ?? 6} ч.
          </span>
          <span>
            {refresh.running
              ? `Идёт перепроверка остатков. В очереди страниц: ${refresh.pending_pages ?? 0}`
              : refresh.checked_at
                ? `Последняя перепроверка остатков: ${date(refresh.checked_at, true)}`
                : 'Перепроверка остатков запланирована'}
          </span>
          {refresh.pages !== undefined && (
            <span>
              Перепроверено страниц: {refresh.pages}. Подтверждено предложений:{' '}
              {refresh.offers ?? 0}. Снято подтверждений: {refresh.withdrawn ?? 0}
            </span>
          )}
          {(refresh.refused ||
            refresh.incomplete ||
            refresh.errors ||
            (refresh.complete === false && !refresh.running)) && (
            <span className="stock-source-warning">
              Перепроверка остатков ограничена
              {refresh.refused
                ? `: ${sourceReason(refresh.refused)}`
                : refresh.incomplete
                  ? `: ${sourceReason(refresh.incomplete)}`
                  : refresh.errors
                    ? `. Ошибок: ${refresh.errors}`
                    : ''}
            </span>
          )}
        </>
      )}
      <small>
        {check?.running && check.started_at
          ? `Текущий обход начат: ${date(check.started_at, true)}`
          : check?.checked_at
            ? `Последняя проверка: ${date(check.checked_at, true)}`
            : 'Проверка ещё не выполнялась'}
      </small>
    </li>
  );
}

function safeUrl(value?: string | null) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function Evidence({ row, asOf }: { row: StockRow; asOf: string }) {
  const [openedAt] = useState(() => Date.now());
  return (
    <details className="stock-evidence">
      <summary>
        {row.offers.length ? `Источников: ${row.offers.length}` : 'Данные каталога'}
      </summary>
      {row.offers.length === 0 ? (
        <p className="muted">Наличие указано в каталоге. Количество уточните перед заказом.</p>
      ) : (
        row.offers.map((offer) => {
          const fresh =
            row.fresh !== false &&
            offer.valid_until &&
            new Date(offer.valid_until).getTime() > Math.max(new Date(asOf).getTime(), openedAt);
          const href = safeUrl(offer.url);
          return (
            <div className="stock-offer" key={offer.offer_id}>
              <strong>{offer.source_name}</strong>
              <span>
                {offer.catalog_number || 'Артикул не указан'} · {offer.pack || 'Фасовка не указана'}
              </span>
              <span>Остаток источника: {offer.quantity ?? 'не указан'}</span>
              <span>Получено: {date(offer.seen_at, true)}</span>
              <span>
                {fresh ? 'Актуально до' : 'Срок актуальности истёк'}:{' '}
                {date(offer.valid_until, true)}
              </span>
              <span>
                {offer.match === 'exact_pack' ? 'Фасовка сопоставлена' : 'Фасовку нужно проверить'}
              </span>
              {href && (
                <a href={href} target="_blank" rel="noopener noreferrer">
                  Открыть источник <ExternalLink size={13} />
                </a>
              )}
            </div>
          );
        })
      )}
    </details>
  );
}

export function StockCatalog() {
  const auth = useAuth();
  const allowed = auth.can('catalog.read');
  const internal = auth.can('clients.read');
  const exportAllowed = auth.can('exports.download');
  const [mode, setMode] = useState<'catalog' | 'review'>('catalog');
  const [q, setQ] = useState('');
  const [brand, setBrand] = useState('');
  const [source, setSource] = useState('');
  const [cursors, setCursors] = useState<string[]>([]);
  const [asOf, setAsOf] = useState<string>();
  const [exporting, setExporting] = useState<'client' | 'internal'>();
  const [exportError, setExportError] = useState<unknown>();
  const debounced = useDebounced(q);
  const debouncedBrand = useDebounced(brand);
  const filtersPending = q !== debounced || brand !== debouncedBrand;
  const params = new URLSearchParams({
    mode,
    audience: internal ? 'internal' : 'client',
    q: debounced,
    brand: debouncedBrand,
    source: internal ? source : '',
    limit: '50',
  });
  if (cursors.length) params.set('cursor', cursors[cursors.length - 1]);
  if (asOf) params.set('as_of', asOf);
  const list = useApi<StockPage>(allowed ? `/stock-catalog?${params}` : null);

  function firstPage() {
    setCursors([]);
    setAsOf(undefined);
    setExportError(undefined);
  }

  async function exportFile(audience: 'client' | 'internal') {
    setExporting(audience);
    setExportError(undefined);
    const exportParams = new URLSearchParams({
      audience,
      mode,
      q: debounced,
      brand: debouncedBrand,
      source: internal ? source : '',
    });
    if (list.data?.as_of) exportParams.set('as_of', list.data.as_of);
    try {
      await download(
        `/stock-catalog/export.xlsx?${exportParams}`,
        audience === 'client' ? 'ОГК-ХИМ Каталог наличия.xlsx' : 'ОГК-ХИМ Внутренний каталог.xlsx',
      );
    } catch (error) {
      setExportError(error);
    } finally {
      setExporting(undefined);
    }
  }

  if (!allowed)
    return (
      <Empty
        title="Каталог недоступен"
        description="Для этого раздела требуется право просмотра каталога."
      />
    );

  return (
    <>
      <PageHeading
        title="Каталог наличия"
        description="Товары в наличии с точной фасовкой. Количество и срок подтвердите перед заказом."
        actions={
          <Button
            variant="secondary"
            onClick={() => {
              firstPage();
              if (!cursors.length && !asOf) list.refresh();
            }}
          >
            <RefreshCw size={16} /> Обновить список
          </Button>
        }
      />
      <div className="panel stock-catalog">
        {internal && (
          <div className="tabs">
            {(
              [
                ['catalog', 'В наличии'],
                ['review', 'На проверке менеджера'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                className={mode === key ? 'active' : ''}
                onClick={() => {
                  setMode(key);
                  firstPage();
                }}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        {mode === 'review' && (
          <div className="stock-review-note">
            <PackageCheck size={21} />
            <div>
              <strong>Сначала проверьте товар и фасовку</strong>
              <p>
                Здесь предложения без точного соответствия карточке. Если производитель и артикул
                известны, проверьте CAS и фасовку, затем создайте или исправьте карточку через{' '}
                <Link to="/catalog">Номенклатуру</Link>. Повторная проверка источника сопоставит
                позиции с подтверждёнными идентификаторами.
              </p>
              <p>
                В предложениях «Химические решения (ЦСДБ)» и других строках без производителя или
                артикула создание карточки само не свяжет предложение и не подтвердит наличие. Они
                остаются в очереди ручной проверки. Автоматическая публикация отключена.
              </p>
            </div>
          </div>
        )}
        <div className="stock-filters">
          <label className="search-input">
            <Search size={17} />
            <input
              aria-label="Поиск по наличию"
              placeholder="Название, артикул или CAS…"
              value={q}
              maxLength={200}
              onChange={(event) => {
                setQ(event.target.value);
                firstPage();
              }}
            />
          </label>
          <input
            aria-label="Производитель"
            placeholder="Производитель"
            value={brand}
            maxLength={200}
            onChange={(event) => {
              setBrand(event.target.value);
              firstPage();
            }}
          />
          {internal && (
            <select
              aria-label="Источник наличия"
              value={source}
              onChange={(event) => {
                setSource(event.target.value);
                firstPage();
              }}
            >
              <option value="">Все источники</option>
              {(list.data?.sources || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="stock-export-bar">
          <span className="muted">
            {list.error
              ? 'Не удалось загрузить наличие'
              : list.loading
                ? 'Загрузка наличия'
                : list.data?.as_of
                  ? `Срез: ${date(list.data.as_of, true)} · Москва`
                  : 'Наличие не загружено'}
          </span>
          <div className="inline-actions">
            {exportAllowed && mode === 'catalog' && (
              <Button
                variant="secondary"
                disabled={
                  Boolean(exporting) ||
                  list.loading ||
                  filtersPending ||
                  !list.data ||
                  Boolean(list.error)
                }
                busy={exporting === 'client'}
                onClick={() => void exportFile('client')}
              >
                <Download size={15} /> Excel для клиента
              </Button>
            )}
            {exportAllowed && internal && (
              <Button
                variant="secondary"
                disabled={
                  Boolean(exporting) ||
                  list.loading ||
                  filtersPending ||
                  !list.data ||
                  Boolean(list.error)
                }
                busy={exporting === 'internal'}
                onClick={() => void exportFile('internal')}
              >
                <Download size={15} /> Внутренний Excel
              </Button>
            )}
          </div>
        </div>
        {exportAllowed && (
          <p className="stock-export-hint muted">
            Excel включает все позиции по текущим фильтрам.
            {mode === 'catalog' && ' В клиентском файле нет поставщиков и закупочных данных.'}
          </p>
        )}
        <ErrorBox error={list.error || exportError} retry={list.refresh} />
        {list.error ? null : list.loading ? (
          <Loading />
        ) : list.data ? (
          <>
            <DataTable
              rows={list.data?.items || []}
              className="stock-table"
              empty={
                <Empty
                  compact
                  title={
                    mode === 'catalog' ? 'Позиций в наличии нет' : 'Предложений на проверке нет'
                  }
                  description="Уточните фильтры или обновите список после проверки источников."
                />
              }
              columns={[
                { key: 'name', label: 'Наименование' },
                { key: 'article', label: 'Артикул' },
                { key: 'cas', label: 'CAS' },
                { key: 'manufacturer', label: 'Производитель' },
                { key: 'purity', label: 'Чистота' },
                { key: 'packaging', label: 'Фасовка' },
                {
                  key: 'availability',
                  label: 'Наличие',
                  render: (row) => (
                    <Badge
                      value={row.availability === 'in_stock' ? 'В наличии' : 'Требует проверки'}
                      tone={row.availability === 'in_stock' ? 'green' : 'amber'}
                    />
                  ),
                },
                {
                  key: 'checked_at',
                  label: 'Обновлено',
                  render: (row) => date(row.checked_at, true),
                },
                ...(internal
                  ? [
                      {
                        key: 'offers',
                        label: 'Проверка наличия',
                        render: (row: StockRow) => (
                          <Evidence row={row} asOf={list.data?.as_of || ''} />
                        ),
                      },
                    ]
                  : []),
              ]}
            />
            <div className="stock-pagination">
              <span className="muted">Страница {cursors.length + 1}</span>
              <div className="inline-actions">
                <Button
                  variant="ghost"
                  disabled={!cursors.length}
                  onClick={() => setCursors(cursors.slice(0, -1))}
                >
                  Назад
                </Button>
                <Button
                  variant="ghost"
                  disabled={
                    filtersPending ||
                    !list.data?.has_more ||
                    !list.data.next_cursor ||
                    Boolean(list.error)
                  }
                  onClick={() => {
                    if (!list.data?.next_cursor) return;
                    setAsOf(list.data.as_of);
                    setCursors([...cursors, list.data.next_cursor]);
                  }}
                >
                  Далее
                </Button>
              </div>
            </div>
          </>
        ) : null}
        {!list.error && !list.loading && internal && Boolean(list.data?.sources.length) && (
          <details className="stock-source-status">
            <summary>Состояние источников</summary>
            <ul>
              {list.data!.sources.map((item) => (
                <SourceCheck key={item.id} source={item} />
              ))}
            </ul>
          </details>
        )}
      </div>
    </>
  );
}
