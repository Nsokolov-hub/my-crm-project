import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StockCatalog } from '../pages/StockCatalog';
import { useAuth } from '../app/Auth';
import { download } from '../lib/api';
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
