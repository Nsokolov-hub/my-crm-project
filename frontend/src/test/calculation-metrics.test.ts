import { describe, expect, it } from 'vitest';
import { displayCalculationMetrics } from '../lib/calculations';

describe('calculation profitability presentation', () => {
  it('keeps sales and cost returns separate for new calculations', () => {
    expect(
      displayCalculationMetrics({
        profitability_percent: '28.96',
        margin_percent: '28.96',
        cost_profitability_percent: '40.77',
        profit: '7896.00',
      }),
    ).toEqual({
      profitability_percent: '28.96',
      cost_profitability_percent: '40.77',
      profit: '7896.00',
    });
  });

  it('reads the recorded margin of historical snapshots without mutating them', () => {
    const historical = Object.freeze({ profitability_percent: '40.77', margin_percent: '28.96' });
    expect(displayCalculationMetrics(historical)).toEqual({
      cost_profitability_percent: '40.77',
      profitability_percent: '28.96',
    });
    expect(historical).toEqual({ profitability_percent: '40.77', margin_percent: '28.96' });
  });

  it('does not mislabel a historical cost-only ratio or discard recorded zero values', () => {
    expect(displayCalculationMetrics({ profitability_percent: '0' })).toEqual({
      cost_profitability_percent: '0',
    });
    expect(displayCalculationMetrics({ profitability_percent: '0', margin_percent: '0' })).toEqual({
      cost_profitability_percent: '0',
      profitability_percent: '0',
    });
  });
});
