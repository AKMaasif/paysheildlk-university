import { chipFor, statusLabel } from "@/lib/format";

export function Chip({ value, label }: { value?: string | null; label?: string }) {
  const text = label ?? statusLabel(value);
  return <span className={`chip ${chipFor(value)}`}>{text}</span>;
}

export function Note({ children, tone = "info" }: { children: React.ReactNode; tone?: "info" | "warn" }) {
  return (
    <div className={`note ${tone === "warn" ? "warn" : ""}`}>
      <span className="mark">i</span>
      <div>{children}</div>
    </div>
  );
}

export function Spark({ values, color = "#1f8a84" }: { values: number[]; color?: string }) {
  const w = 76;
  const h = 22;
  const max = Math.max(1, ...values);
  const line = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * w;
      const y = h - 2 - (value / max) * (h - 4);
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} aria-hidden>
      <path d={line || "M0,11 L76,11"} fill="none" stroke={color} strokeWidth="1.4" />
    </svg>
  );
}

export function PageHeader({
  crumb,
  title,
  subtitle,
  chip,
  extra,
}: {
  crumb: string;
  title: string;
  subtitle?: string;
  chip?: string;
  extra?: React.ReactNode;
}) {
  const tone = (chip || "").toLowerCase();
  return (
    <header className="page-head">
      <div>
        <div className="crumb">{crumb}</div>
        <h1>{title}</h1>
        {subtitle && <p className="subtitle">{subtitle}</p>}
      </div>
      <div className="page-head-side">
        {extra}
        {chip && <span className={`status-chip ${tone}`}>● {chip}</span>}
      </div>
    </header>
  );
}

export function Kpi({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      <div className="sub">{sub}</div>
    </div>
  );
}

export function Trend({
  points,
}: {
  points: { label: string; cases: number; blocked_lkr: number }[];
}) {
  const w = 640;
  const h = 190;
  const pad = 28;
  const maxCases = Math.max(1, ...points.map((p) => p.cases));
  const maxBlocked = Math.max(1, ...points.map((p) => p.blocked_lkr));
  const x = (i: number) => pad + (i * (w - pad * 2)) / Math.max(1, points.length - 1);
  const line = (pick: (p: { cases: number; blocked_lkr: number }) => number, max: number) =>
    points
      .map((p, i) => {
        const y = h - pad - (pick(p) / max) * (h - pad * 2);
        return `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height="190" role="img" aria-label="Weekly cases">
        {[0, 1, 2, 3].map((n) => (
          <line key={n} x1={pad} x2={w - pad} y1={pad + n * ((h - pad * 2) / 3)} y2={pad + n * ((h - pad * 2) / 3)} stroke="#eee" />
        ))}
        <path d={line((p) => p.blocked_lkr, maxBlocked)} fill="none" stroke="#2a9d8f" strokeWidth="1.6" strokeDasharray="4 3" />
        <path d={line((p) => p.cases, maxCases)} fill="none" stroke="#0f6cbd" strokeWidth="1.8" />
        {points.map((p, i) =>
          i % 2 === 0 ? (
            <text key={p.label} x={x(i)} y={h - 8} textAnchor="middle" fontSize="10" fill="#616161">
              {p.label}
            </text>
          ) : null,
        )}
      </svg>
      <div className="legend">
        <span><i className="swatch" style={{ background: "#0f6cbd" }} /> Cases opened</span>
        <span><i className="swatch" style={{ background: "#2a9d8f" }} /> Blocked amount, scaled</span>
      </div>
    </div>
  );
}

export type Column<T> = {
  key: string;
  header: string;
  render?: (row: T) => React.ReactNode;
  width?: string;
};

export function DataTable<T>({
  columns,
  rows,
  selected,
  onSelect,
  rowId,
}: {
  columns: Column<T>[];
  rows: T[];
  selected?: string | null;
  onSelect?: (row: T) => void;
  rowId?: (row: T) => string;
}) {
  const idOf = rowId || ((row: T) => String((row as { id?: string }).id || ""));
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} style={{ width: col.width }}>{col.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length}>No rows in this scope.</td>
            </tr>
          ) : (
            rows.map((row) => {
              const id = idOf(row);
              return (
                <tr key={id} className={selected === id ? "selected" : ""} onClick={() => onSelect?.(row)}>
                  {columns.map((col) => (
                    <td key={col.key}>{col.render ? col.render(row) : String((row as Record<string, unknown>)[col.key] ?? "")}</td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
