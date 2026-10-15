export type BarRow = { id: string; label: string; value: number; display: string };

/** One measure across named rows: horizontal bars from a zero baseline, values in text ink. */
export function BarChart({ title, rows }: { title: string; rows: BarRow[] }) {
  const max = Math.max(...rows.map((row) => Math.abs(row.value)), 0);
  return (
    <figure className="bar-chart" aria-label={title}>
      <figcaption>{title}</figcaption>
      <ul>
        {rows.map((row) => (
          <li key={row.id} title={`${row.label}: ${row.display}`}>
            <span className="bar-chart-label">{row.label}</span>
            <span className="bar-chart-track">
              <i
                style={{ width: `${max ? Math.max(1, (Math.abs(row.value) / max) * 100) : 0}%` }}
              />
            </span>
            <strong>{row.display}</strong>
          </li>
        ))}
      </ul>
    </figure>
  );
}
