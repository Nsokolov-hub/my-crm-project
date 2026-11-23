import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StockCatalog } from '../pages/StockCatalog';
import { useAuth } from '../app/Auth';
import { ApiError, download } from '../lib/api';
import { useApi } from '../lib/hooks';

vi.mock('../app/Auth', () => ({ useAuth: vi.fn() }));
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  download: vi.fn(),
}));
vi.mock('../lib/hooks', () => ({ useApi: vi.fn(), useDebounced: (value: string) => value }));
const AT = '2026-11-20T10:00:00+00:00';
const refresh = vi.fn();
const publicRow = {
  id: 'product:5g',
  name: 'Вымышленный реактив',
  article: '000123',
  cas: '50-00-0',
  manufacturer: 'Demo Brand',
  purity: '99%',
  packaging: '5 g',
  availability: 'in_stock',
  checked_at: AT,
  offers: [
    {
      source: 'demo',
      source_name: 'Тестовый поставщик',
      offer_id: 'offer',
      catalog_number: 'SRC-1',
      pack: '5 g',
      quantity: 3,
      match: 'exact_pack',
      seen_at: AT,
      valid_until: '2099-01-01T00:00:00+00:00',
      url: 'https://supplier.test/stock',
    },
  ],
};

function signIn(permissions: string[]) {
  vi.mocked(useAuth).mockReturnValue({
    can: (code: string) => permissions.includes(code),
  } as ReturnType<typeof useAuth>);
}

beforeEach(() => {
  vi.clearAllMocks();
  signIn(['catalog.read', 'clients.read', 'exports.download']);
  vi.mocked(download).mockResolvedValue(undefined);
  vi.mocked(useApi).mockImplementation((path) => {
    const params = new URLSearchParams(path?.split('?')[1]);
    const review = params.get('mode') === 'review';
    return {
      loading: false,
      error: undefined,
      refresh,
      data: {
        as_of: AT,
        items: [{ ...publicRow, ...(review ? { availability: 'unknown', fresh: false } : {}) }],
        next_cursor: params.has('cursor') ? null : 'cursor-next',
        has_more: !params.has('cursor'),
        can_review: true,
        sources: [
          {
            id: 'demo',
            name: 'Тестовый поставщик',
            status: 'supported',
            reason: '',
            last_check: {
              checked_at: AT,
              refused: 'robots',
              errors: 2,
              complete: false,
              offers: 0,
              matched: 0,
            },
          },
          {
            id: 'manual',
            name: 'Ручная проверка',
            status: 'review_only',
            reason: 'manufacturer_and_article_require_manager_review',
          },
          {
            id: 'offline',
            name: 'Недоступный источник',
            status: 'temporarily_unavailable',
            reason: '',
            last_check: { incomplete: 'no_verified_public_stock_evidence', complete: false },
          },
        ],
      },
    } as ReturnType<typeof useApi>;
  });
});

function open() {
  render(
    <MemoryRouter>
      <StockCatalog />
    </MemoryRouter>,
  );
}

describe('stock catalogue', () => {
  it('shows exact packaging and internal source evidence without prices', () => {
    open();
    expect(screen.getByRole('heading', { name: 'Каталог наличия' })).toBeInTheDocument();
    expect(screen.getByText('000123')).toBeInTheDocument();
    expect(screen.getAllByText('5 g')).toHaveLength(1);
    fireEvent.click(screen.getByText('Источников: 1'));
    expect(screen.getByText('Остаток источника: 3')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Открыть источник' })).toHaveAttribute(
      'href',
      'https://supplier.test/stock',
    );
    expect(screen.queryByText('Цена')).not.toBeInTheDocument();
  });

  it('exports all filtered results at the selected snapshot, never just the current cursor', async () => {
    open();
    fireEvent.change(screen.getByRole('textbox', { name: 'Поиск по наличию' }), {
      target: { value: '000123' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Производитель' }), {
      target: { value: 'Demo Brand' },
    });
    fireEvent.change(screen.getByRole('combobox', { name: 'Источник наличия' }), {
      target: { value: 'demo' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
    expect(screen.getByText('Страница 2')).toBeInTheDocument();
    const listPath = vi.mocked(useApi).mock.calls.at(-1)?.[0];
    const listParams = new URLSearchParams(listPath?.split('?')[1]);
    expect(listParams.get('cursor')).toBe('cursor-next');
    expect(listParams.get('as_of')).toBe(AT);
    fireEvent.click(screen.getByRole('button', { name: 'Excel для клиента' }));
    await waitFor(() => expect(download).toHaveBeenCalledOnce());
    const [exportPath] = vi.mocked(download).mock.calls[0];
    const params = new URLSearchParams(exportPath.split('?')[1]);
    expect(params.get('audience')).toBe('client');
    expect(params.get('q')).toBe('000123');
    expect(params.get('brand')).toBe('Demo Brand');
    expect(params.get('source')).toBe('demo');
    expect(params.get('as_of')).toBe(AT);
    expect(params.has('cursor')).toBe(false);
  });

  it('keeps review a manager workflow and offers only the internal file', () => {
    open();
    fireEvent.click(screen.getByRole('button', { name: 'На проверке менеджера' }));
    expect(screen.getByText('Сначала проверьте товар и фасовку')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Номенклатуру' })).toHaveAttribute('href', '/catalog');
    expect(screen.getByText(/Автоматическая публикация отключена/)).toBeInTheDocument();
    expect(screen.getByText(/создание карточки само не свяжет предложение/)).toBeInTheDocument();
    expect(screen.getByText(/Химические решения \(ЦСДБ\)/)).toBeInTheDocument();
    expect(screen.getByText('Требует проверки')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Excel для клиента' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Внутренний Excel' })).toBeInTheDocument();
  });

  it('shows refused and incomplete source checks without implying successful availability', () => {
    open();
    fireEvent.click(screen.getByText('Состояние источников'));
    expect(
      screen.getByText('Доступ ограничен: Автоматическая проверка запрещена источником'),
    ).toBeVisible();
    expect(screen.getByText('Ошибок: 2')).toBeVisible();
    expect(screen.getByText('Предложений: 0. Сопоставлено: 0')).toBeVisible();
    expect(screen.getByText('Проверка менеджером')).toBeVisible();
    expect(screen.getByText('Временно недоступен')).toBeVisible();
    expect(screen.getByText('Производитель и артикул требуют проверки менеджером')).toBeVisible();
    expect(
      screen.getByText('Проверка не завершена: Нет подтверждения наличия на открытых страницах'),
    ).toBeVisible();
    expect(screen.queryByText('Проверка завершена')).not.toBeInTheDocument();
  });

  it('shows current crawl coverage and review counts before the whole run finishes', () => {
    const sample = vi.mocked(useApi).getMockImplementation()!('/stock-catalog');
    vi.mocked(useApi).mockReturnValue({
      ...sample,
      data: {
        ...(sample.data as Record<string, unknown>),
        sources: [
          {
            id: 'skygen',
            name: 'Скайклад',
            status: 'supported',
            reason: '',
            last_check: {
              running: true,
              complete: false,
              checked_at: null,
              started_at: AT,
              pending_pages: 420,
              offers: 30,
              matched: 2,
              stored_offers: 28,
              fresh_offers: 27,
              exact_packagings: 2,
              pending_review: 26,
            },
          },
        ],
      },
    });
    open();
    fireEvent.click(screen.getByText('Состояние источников'));
    expect(screen.getByText('Идёт обход источников. В очереди страниц: 420')).toBeVisible();
    expect(
      screen.getByText('Сохранено предложений: 28. Свежих: 27. Точных фасовок: 2. На проверке: 26'),
    ).toBeVisible();
    expect(screen.getByText(/^Текущий обход начат:/)).toBeVisible();
    expect(screen.queryByText('Проверка ещё не выполнялась')).not.toBeInTheDocument();
    expect(screen.queryByText('Проверка не завершена')).not.toBeInTheDocument();
    expect(screen.queryByText('Проверка завершена')).not.toBeInTheDocument();
  });

  it('shows a frequent stock refresh separately from incomplete catalogue coverage', () => {
    const sample = vi.mocked(useApi).getMockImplementation()!('/stock-catalog');
    vi.mocked(useApi).mockReturnValue({
      ...sample,
      data: {
        ...(sample.data as Record<string, unknown>),
        sources: [
          {
            id: 'chimmed',
            name: 'Химмед',
            status: 'supported',
            reason: '',
            last_check: {
              checked_at: AT,
              complete: false,
              offers: 18800,
              matched: 5680,
              refresh: {
                running: true,
                checked_at: null,
                started_at: AT,
                pending_pages: 20,
                pages: 2,
                offers: 5,
                withdrawn: 15,
                interval_hours: 2,
                fresh_hours: 6,
                scope: 'known_stock',
                complete: false,
              },
            },
          },
        ],
      },
    });
    open();
    fireEvent.click(screen.getByText('Состояние источников'));
    expect(screen.getByText(/Перепроверка известного наличия — каждые 2 ч/)).toBeVisible();
    expect(screen.getByText(/Подтверждение действует не более 6 ч/)).toBeVisible();
    expect(screen.getByText('Идёт перепроверка остатков. В очереди страниц: 20')).toBeVisible();
    expect(
      screen.getByText(
        'Перепроверено страниц: 2. Подтверждено предложений: 5. Снято подтверждений: 15',
      ),
    ).toBeVisible();
    expect(screen.getByText('Предложений: 18800. Сопоставлено: 5680')).toBeVisible();
    expect(screen.getByText('Проверка не завершена')).toBeVisible();
    expect(screen.queryByText('Проверка завершена')).not.toBeInTheDocument();
  });

  it('hides source data, review and internal download without counterparty rights', () => {
    signIn(['catalog.read', 'exports.download']);
    open();
    expect(screen.queryByRole('button', { name: 'На проверке менеджера' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Внутренний Excel' })).not.toBeInTheDocument();
    expect(screen.queryByText('Источников: 1')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Источник наличия' })).not.toBeInTheDocument();
    expect(vi.mocked(useApi).mock.calls.at(-1)?.[0]).toContain('audience=client');
  });

  it('does not fetch the catalogue without catalog.read or offer exports without exports.download', () => {
    signIn([]);
    open();
    expect(screen.getByText('Каталог недоступен')).toBeInTheDocument();
    expect(vi.mocked(useApi).mock.calls.at(-1)?.[0]).toBeNull();
  });

  it.each([false, true])(
    'shows a failed load without empty or cached stock when cached data exists: %s',
    (hasCachedData) => {
      const cached = vi.mocked(useApi).getMockImplementation()!('/stock-catalog');
      vi.mocked(useApi).mockReturnValue({
        ...cached,
        data: hasCachedData ? cached.data : undefined,
        loading: false,
        error: new ApiError(503, 'SITE_UNAVAILABLE', 'Каталог сайта временно недоступен'),
      });
      open();

      expect(screen.getByRole('alert')).toHaveTextContent('Каталог сайта временно недоступен');
      expect(screen.getByText('Не удалось загрузить наличие')).toBeVisible();
      expect(screen.queryByText('Загрузка наличия')).not.toBeInTheDocument();
      expect(screen.queryByText('Позиций в наличии нет')).not.toBeInTheDocument();
      expect(screen.queryByText('000123')).not.toBeInTheDocument();
      expect(screen.queryByRole('table')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Назад' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Далее' })).not.toBeInTheDocument();
      expect(screen.queryByText('Состояние источников')).not.toBeInTheDocument();
      expect(screen.queryByText(/^Срез:/)).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Excel для клиента' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Внутренний Excel' })).toBeDisabled();
      fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
      expect(refresh).toHaveBeenCalledOnce();
      expect(download).not.toHaveBeenCalled();
    },
  );

  it('explains a failed export and keeps unsafe supplier links inert', async () => {
    vi.mocked(download).mockRejectedValueOnce(new Error('Сайт не отвечает'));
    publicRow.offers[0].url = 'javascript:alert(1)';
    try {
      open();
      fireEvent.click(screen.getByText('Источников: 1'));
      expect(screen.queryByRole('link', { name: 'Открыть источник' })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Excel для клиента' }));
      await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Сайт не отвечает'));
    } finally {
      publicRow.offers[0].url = 'https://supplier.test/stock';
    }
  });
});
