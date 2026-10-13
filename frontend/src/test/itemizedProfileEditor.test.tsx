import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { customsWithFixedFees, ItemizedProfileEditor } from '../components/ItemizedProfileEditor';
import { api } from '../lib/api';

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

const oldColumnsRule = {
  product_group_slug: 'columns',
  type: 'FIXED_GROUP',
  value: '73800',
} as const;

describe('itemized profile editor', () => {
  it('moves the old fixed columns duty to the fixed customs fee', () => {
    expect(
      customsWithFixedFees(
        [{ product_group_slug: 'reagents', type: 'PERCENTAGE', value: '5' }, oldColumnsRule],
        [],
      ),
    ).toEqual({
      customs_rules: [
        { product_group_slug: 'reagents', type: 'PERCENTAGE', value: '5' },
        { product_group_slug: 'columns', type: 'NONE', value: '0' },
      ],
      customs_fee_overrides: [{ product_group_slug: 'columns', amount: '73800' }],
    });
  });

  it('keeps the fixed fee a profile already sets for the group', () => {
    const fees = [{ product_group_slug: 'columns', amount: '70000' }];
    expect(customsWithFixedFees([oldColumnsRule], fees).customs_fee_overrides).toEqual(fees);
  });

  it('opens a new version of an old profile with duty-free columns and their fee', async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path.startsWith('/product-groups'))
        return { items: [{ id: 'group-columns', slug: 'columns', name: 'Колонки' }], total: 1 };
      if (path === '/profiles/example-v2') return { definition: {} };
      return { items: [], total: 0 };
    });
    render(
      <ItemizedProfileEditor
        initial={{
          id: 'profile-old',
          name: 'Профиль до 5 октября',
          definition: {
            methodology: 'itemized_v2',
            template: { title: 'Коммерческое предложение' },
            customs_rules: [oldColumnsRule],
            customs_fee_brackets: [{ from_amount: '0', to_amount: null, fee: '1231' }],
          },
        }}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Правило')).toHaveValue('NONE');
    expect(screen.getByLabelText('Ставка, %')).toHaveValue('0');
    expect(screen.queryByRole('option', { name: 'Фиксировано на группу' })).toBeNull();
    expect(screen.getByLabelText('Сбор на группу, ₽')).toHaveValue('73800');
    await waitFor(() => expect(screen.getByLabelText('Группа')).toHaveValue('columns'));
  });
});
