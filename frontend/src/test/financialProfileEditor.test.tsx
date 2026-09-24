import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  FinancialProfileEditor,
  normalizedDefinition,
  shiftDecimal,
  starterDefinition,
} from '../components/FinancialProfileEditor';
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

describe('financial profile editor', () => {
  it('starts without assumed tax rates and moves decimal percentages exactly', () => {
    const definition = starterDefinition();
    expect(definition.country_of_import).toBe('');
    expect(definition.tax_regime).toBe('');
    expect(definition.tax_category).toBe('');
    expect(Object.values(definition.constants)).toEqual(['0', '0', '0', '0', '0']);
    expect(shiftDecimal('33,333333', -2)).toBe('0.33333333');
    expect(shiftDecimal('0.33333333', 2)).toBe('33.333333');
  });

  it('keeps custom formulas and coefficients when starting a new version', () => {
    const previous = {
      ...starterDefinition(),
      constants: { ...starterDefinition().constants, insurance_factor: '1.05' },
      formulas: [
        { name: 'insured_purchase', expression: 'purchase * insurance_factor' },
        ...starterDefinition().formulas,
      ],
    };
    const definition = normalizedDefinition(previous);
    expect(definition.constants.insurance_factor).toBe('1.05');
    expect(definition.formulas[0]).toEqual({
      name: 'insured_purchase',
      expression: 'purchase * insurance_factor',
    });
  });

  it('requires explicit review of zero rates and sends a regular profile command', async () => {
    vi.mocked(api).mockResolvedValue({ id: 'profile-1' });
    const onSuccess = vi.fn();
    render(<FinancialProfileEditor onClose={vi.fn()} onSuccess={onSuccess} />);
    fireEvent.change(screen.getByLabelText('Название профиля *'), {
      target: { value: 'Правила компании' },
    });
    fireEvent.change(screen.getByLabelText('Страна ввоза *'), {
      target: { value: 'Россия' },
    });
    fireEvent.change(screen.getByLabelText('Налоговый режим *'), {
      target: { value: 'Режим компании' },
    });
    fireEvent.change(screen.getByLabelText('Категория налога в документах *'), {
      target: { value: 'Ставка компании' },
    });
    const dutyRate = screen.getByLabelText(/^Пошлина, %/);
    fireEvent.change(dutyRate, { target: { value: '12.0' } });
    expect(dutyRate).toHaveValue('12.0');
    fireEvent.change(dutyRate, { target: { value: '12,5' } });
    fireEvent.change(screen.getByLabelText('Основание создания или изменения *'), {
      target: { value: 'Приказ руководителя' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    expect(
      await screen.findByText(/Подтвердите, что ставки и правила расчёта проверены/),
    ).toBeVisible();
    expect(api).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText(/Я проверил ставки и правила расчёта/));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledOnce());
    expect(api).toHaveBeenCalledWith(
      '/profiles',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          name: 'Правила компании',
          reason: 'Приказ руководителя',
          definition: expect.objectContaining({
            country_of_import: 'Россия',
            constants: expect.objectContaining({ duty_rate: '0.125' }),
          }),
        }),
      }),
    );
  });

  it('creates a new version without losing the previous formula order', async () => {
    vi.mocked(api).mockResolvedValue({ id: 'profile-2' });
    const definition = starterDefinition();
    definition.country_of_import = 'Россия';
    definition.tax_regime = 'Режим компании';
    definition.tax_category = 'Категория компании';
    definition.formulas.unshift({ name: 'insured_purchase', expression: 'purchase * 1.05' });
    render(
      <FinancialProfileEditor
        initial={{ id: 'profile-1', name: 'Текущий профиль', definition }}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('Основание создания или изменения *'), {
      target: { value: 'Новая редакция правил' },
    });
    fireEvent.click(screen.getByLabelText(/Я проверил ставки и правила расчёта/));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/profiles',
        expect.objectContaining({
          body: expect.objectContaining({
            previous_id: 'profile-1',
            definition: expect.objectContaining({
              formulas: expect.arrayContaining([
                { name: 'insured_purchase', expression: 'purchase * 1.05' },
              ]),
            }),
          }),
        }),
      ),
    );
  });
});
