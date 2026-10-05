/** Present historical snapshots without relabelling their cost-based return as sales profitability. */
export function displayCalculationMetrics(values: Record<string, unknown>) {
  const result = { ...values };
  if (
    values.cost_profitability_percent === undefined &&
    values.profitability_percent !== undefined
  ) {
    result.cost_profitability_percent = values.profitability_percent;
    delete result.profitability_percent;
  }
  if (values.margin_percent !== undefined) result.profitability_percent = values.margin_percent;
  delete result.margin_percent;
  return result;
}
