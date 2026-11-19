"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useShell } from "@/components/Shell";
import { api } from "@/lib/api";
import { lkr } from "@/lib/format";

type Bucket = { payments: number; flagged: number; blocked: number; held: number; released: number; rate: number };
type Day = Bucket & { date: string; partial: boolean };
type Province = Bucket & { province: string; share: number };
type District = Bucket & { district: string; province: string };
type Analytics = {
  label: string;
  window_days: number;
  generated_at: string;
  sources: { simulated: number; recorded: number };
  totals: Omit<Bucket, "rate"> & { flag_rate: number; amount_stopped_lkr: number; amount_held_lkr: number };
  trend: { last7_flagged: number; prev7_flagged: number; change_pct: number | null };
  daily: Day[];
  by_province: Province[];
  by_district: District[];
  hotspots: { district: string; rate: number; multiple: number; flagged: number }[];
  rising: { district: string; last7_flagged: number; last7_payments: number; expected: number; last7_rate: number; usual_rate: number }[];
  findings: string[];
  summary: string;
  summary_source: string;
};

// Outcome colours match the rest of the console: red blocked, amber held, teal released after the customer's check.
const BLOCKED = "#b42318";
const HELD = "#c98a1b";
const RELEASED = "#1f8a84";
const RAMP = ["#eef3f2", "#f6dfae", "#eba55c", "#cc5a30", "#9f1d14"];
// Below this many payments a rate says more about luck than risk, so the tile is not coloured by it.
const MIN_FOR_RATE = 30;

function rampIndex(rate: number, top: number): number {
  if (rate <= 0) return 0;
  const ratio = rate / Math.max(top, 0.1);
  return ratio < 0.25 ? 1 : ratio < 0.5 ? 2 : ratio < 0.8 ? 3 : 4;
}

function shortDate(iso: string): string {
  const [, month, day] = iso.split("-");
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${Number(day)} ${names[Number(month) - 1]}`;
}

function money(amount: number): { num: string; unit: string } {
  if (amount >= 1_000_000) return { num: (amount / 1_000_000).toFixed(1), unit: "M Rs" };
  return { num: amount.toLocaleString("en-US"), unit: "Rs" };
}

export function FraudAnalytics({ full = false }: { full?: boolean }) {
  const { refreshKey } = useShell();
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState("");
  const [pick, setPick] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    api<Analytics>("/api/analytics")
      .then((body) => { if (!stop) { setData(body); setError(""); } })
      .catch((err) => { if (!stop) setError(err instanceof Error ? err.message : "Analytics are not available"); });
    return () => { stop = true; };
  }, [refreshKey]);

  const topRate = useMemo(() => Math.max(0.1, ...(data?.by_district.filter((row) => row.payments >= MIN_FOR_RATE).map((row) => row.rate) || [0])), [data]);
  if (error) return <section className="card" style={{ marginBottom: 14 }}><p className="fine">Fraud analytics are not available: {error}</p></section>;
  if (!data) return <section className="card" style={{ marginBottom: 14 }}><p className="fine">Reading the ledger…</p></section>;

  const t = data.totals;
  const stopped = money(t.amount_stopped_lkr);
  const chosen = data.by_district.find((row) => row.district === pick) || null;
  const byName = [...data.by_district].sort((a, b) => a.province.localeCompare(b.province) || b.flagged - a.flagged || a.district.localeCompare(b.district));

  return (
    <section style={{ marginBottom: 14 }}>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="panel-head">
          <h2>Fraud analytics · last {data.window_days} days</h2>
          <span>
            {data.label === "simulated" && <span className="chip neutral" title={`${data.sources.simulated.toLocaleString("en-US")} simulated payments and ${data.sources.recorded} recorded by the pilot`}>SIMULATED</span>}{" "}
            <span className="chip info">Analytics agent</span>
          </span>
        </div>
        {data.summary_source === "rules" ? (
          <ul className="brief">
            {data.findings.map((line) => <li key={line}>{line}</li>)}
          </ul>
        ) : (
          <p>{data.summary}</p>
        )}
        <p className="fine">
          {data.summary_source === "rules"
            ? "Every figure is counted from the ledger by fixed code. No model wrote these lines."
            : `Reworded by ${data.summary_source}. Each number in it was checked against the ledger counts.`}
          {" "}Simulated payments are labelled and are not a live bank feed.
        </p>
      </div>

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(5, minmax(0, 1fr))" }}>
        <Kpi label="PAYMENTS" num={t.payments.toLocaleString("en-US")} caption={`${data.window_days} days`} />
        <Kpi label="ABNORMAL" num={t.flagged.toLocaleString("en-US")} unit={`${t.flag_rate}%`} caption="held, blocked or checked" />
        <Kpi label="BLOCKED" num={t.blocked.toLocaleString("en-US")} caption="stopped as fraud" color={BLOCKED} />
        <Kpi label="HELD NOW" num={t.held.toLocaleString("en-US")} caption={`${lkr(t.amount_held_lkr)} not moved`} color={HELD} />
        <Kpi label="STOPPED" num={stopped.num} unit={stopped.unit} caption="value of blocked payments" />
      </div>

      <div className="analytics-grid">
        <div className="card">
          <div className="panel-head"><h2>Abnormal payments per day</h2><Legend /></div>
          <DailyChart days={data.daily} />
          <p className="fine">
            {data.trend.change_pct === null
              ? "Not enough earlier days to compare weeks."
              : `Last 7 full days: ${data.trend.last7_flagged} abnormal, ${data.trend.change_pct > 0 ? "up" : data.trend.change_pct < 0 ? "down" : "level"}${data.trend.change_pct === 0 ? "" : ` ${Math.abs(data.trend.change_pct)}%`} on the 7 before (${data.trend.prev7_flagged}). Today is partial.`}
          </p>
        </div>

        <div className="card">
          <div className="panel-head"><h2>By province</h2><span className="fine" style={{ margin: 0 }}>abnormal payments · rate</span></div>
          <ProvinceBars rows={data.by_province} />
        </div>
      </div>

      <div className="card" style={{ marginTop: 12 }}>
        <div className="panel-head">
          <h2>All 25 districts</h2>
          <span className="fine" style={{ margin: 0 }}>colour is the abnormal rate · number is the count</span>
        </div>
        <div className="district-grid" role="list">
          {byName.map((row) => {
            const thin = row.payments < MIN_FOR_RATE;
            const level = thin ? 0 : rampIndex(row.rate, topRate);
            return (
              <button
                key={row.district}
                role="listitem"
                type="button"
                className={`tile ${thin ? "thin" : ""} ${pick === row.district ? "picked" : ""}`}
                style={{ background: RAMP[level], color: level >= 3 ? "#fff" : "#1c2423" }}
                onClick={() => setPick(pick === row.district ? null : row.district)}
                title={`${row.district}, ${row.province}: ${row.flagged} abnormal of ${row.payments} payments (${row.rate}%)${thin ? `. Under ${MIN_FOR_RATE} payments, so the rate is not coloured.` : ""}`}
              >
                <span className="tile-name">{row.district}</span>
                <span className="tile-num">{row.flagged}</span>
                <span className="tile-rate">{thin ? `of ${row.payments}` : `${row.rate}%`}</span>
              </button>
            );
          })}
        </div>
        <div className="ramp" aria-hidden="true">
          <span>lower rate</span>
          {RAMP.map((colour) => <i key={colour} style={{ background: colour }} />)}
          <span>higher rate</span>
          <span className="thin-key">dashed: under {MIN_FOR_RATE} payments, too few to rate</span>
        </div>
        {chosen && (
          <p className="fine">
            <strong>{chosen.district}</strong> ({chosen.province}): {chosen.payments.toLocaleString("en-US")} payments, {chosen.flagged} abnormal ({chosen.rate}%) — {chosen.blocked} blocked, {chosen.held} held, {chosen.released} released after the customer's check.
          </p>
        )}
      </div>

      {(data.hotspots.length > 0 || data.rising.length > 0) && (
        <div className="analytics-grid" style={{ marginTop: 12 }}>
          <div className="card">
            <h2>Hot spots</h2>
            {data.hotspots.length === 0 && <p className="fine">No district is well above the national rate.</p>}
            {data.hotspots.map((spot) => (
              <p key={spot.district} className="spot"><strong>{spot.district}</strong> <span className="chip hold">{spot.multiple}× national</span> <span className="fine">{spot.rate}% abnormal · {spot.flagged} payments</span></p>
            ))}
          </div>
          <div className="card">
            <h2>Rising this week</h2>
            {data.rising.length === 0 && <p className="fine">No district is well above its own usual rate.</p>}
            {data.rising.map((row) => (
              <p key={row.district} className="spot"><strong>{row.district}</strong> <span className="chip block">{row.last7_flagged} vs {row.expected} expected</span> <span className="fine">{row.last7_rate}% this week, usually {row.usual_rate}%</span></p>
            ))}
          </div>
        </div>
      )}

      {full && (
        <div className="card" style={{ marginTop: 12 }}>
          <h2>District table</h2>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>District</th><th>Province</th><th>Payments</th><th>Abnormal</th><th>Rate</th><th>Blocked</th><th>Held</th><th>Released</th></tr></thead>
              <tbody>
                {data.by_district.map((row) => (
                  <tr key={row.district}>
                    <td>{row.district}</td><td>{row.province}</td>
                    <td>{row.payments.toLocaleString("en-US")}</td><td>{row.flagged}</td><td>{row.rate}%</td>
                    <td>{row.blocked}</td><td>{row.held}</td><td>{row.released}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {!full && <p className="fine"><Link href="/analytics">Open the full analytics page</Link></p>}
    </section>
  );
}

function Kpi({ label, num, unit, caption, color }: { label: string; num: string; unit?: string; caption: string; color?: string }) {
  return (
    <div className="kpi-cell">
      <div className="label">{label}</div>
      <div className="figure"><span className="num" style={color ? { color } : undefined}>{num}</span>{unit && <span className="unit">{unit}</span>}</div>
      <div className="caption">{caption}</div>
    </div>
  );
}

function Legend() {
  return (
    <span className="legend">
      <i style={{ background: BLOCKED }} /> blocked <i style={{ background: HELD }} /> held <i style={{ background: RELEASED }} /> released
    </span>
  );
}

function DailyChart({ days }: { days: Day[] }) {
  const width = 640;
  const height = 210;
  const pad = { left: 30, right: 8, top: 10, bottom: 26 };
  const max = Math.max(4, ...days.map((day) => day.flagged));
  const top = Math.ceil(max / 2) * 2;
  const inner = width - pad.left - pad.right;
  const step = inner / days.length;
  const barW = Math.max(4, step * 0.68);
  const y = (value: number) => pad.top + (height - pad.top - pad.bottom) * (1 - value / top);
  const label = `Abnormal payments per day for ${days.length} days. Highest day ${Math.max(...days.map((d) => d.flagged))}.`;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} style={{ width: "100%", height: "auto", display: "block" }}>
      {[0, top / 2, top].map((tick) => (
        <g key={tick}>
          <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} stroke="#e1e6e4" strokeWidth="1" />
          <text x={pad.left - 6} y={y(tick) + 3.5} textAnchor="end" fontSize="10" fill="#5e6b68" fontFamily="var(--mono)">{tick}</text>
        </g>
      ))}
      {days.map((day, index) => {
        const x = pad.left + index * step + (step - barW) / 2;
        let base = 0;
        const parts: [number, string][] = [[day.blocked, BLOCKED], [day.held, HELD], [day.released, RELEASED]];
        return (
          <g key={day.date} opacity={day.partial ? 0.55 : 1}>
            <title>{`${shortDate(day.date)}${day.partial ? " (today so far)" : ""}: ${day.flagged} abnormal of ${day.payments} payments — ${day.blocked} blocked, ${day.held} held, ${day.released} released`}</title>
            <rect x={pad.left + index * step} y={pad.top} width={step} height={height - pad.top - pad.bottom} fill="transparent" />
            {parts.map(([count, colour]) => {
              if (!count) return null;
              const rect = <rect key={colour} x={x} y={y(base + count)} width={barW} height={y(base) - y(base + count)} fill={colour} rx="1" />;
              base += count;
              return rect;
            })}
            {(index % 5 === 0 || index === days.length - 1) && (
              <text x={x + barW / 2} y={height - 8} textAnchor="middle" fontSize="10" fill="#5e6b68">{shortDate(day.date)}</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function ProvinceBars({ rows }: { rows: Province[] }) {
  const max = Math.max(1, ...rows.map((row) => row.flagged));
  return (
    <div role="list">
      {rows.map((row) => (
        <div key={row.province} role="listitem" className="prov" title={`${row.province}: ${row.flagged} abnormal of ${row.payments} payments (${row.rate}%) — ${row.blocked} blocked, ${row.held} held, ${row.released} released`}>
          <span className="prov-name">{row.province}</span>
          <span className="prov-track">
            <span className="prov-bar" style={{ width: `${(row.flagged / max) * 100}%` }}>
              <i style={{ flex: row.blocked, background: BLOCKED }} />
              <i style={{ flex: row.held, background: HELD }} />
              <i style={{ flex: row.released, background: RELEASED }} />
            </span>
          </span>
          <span className="prov-num">{row.flagged} <small>· {row.rate}%</small></span>
        </div>
      ))}
    </div>
  );
}
