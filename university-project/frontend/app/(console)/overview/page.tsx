"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import Link from "next/link";
import { FraudAnalytics } from "@/components/FraudAnalytics";
import { Note, PageHeader, Spark } from "@/components/ui";
import { markerTone, type MapCustomer, type MapEvent } from "@/lib/markers";
import { matchesQuery, useShell } from "@/components/Shell";
import { api } from "@/lib/api";
import { colombo, lkr, statusLabel } from "@/lib/format";

const MapView = dynamic(() => import("@/components/SriLankaMap").then((mod) => mod.SriLankaMap), { ssr: false });

type CaseRow = {
  id: string;
  customer_name: string;
  bank: string;
  district: string;
  amount_lkr: number;
  payee: string;
  risk_score: number | null;
  status: string;
  policy_decision: string | null;
  updated_at: string;
};

type Overview = {
  kpis: {
    cases_today: number;
    open_cases: number;
    waiting: number;
    held_lkr: number;
    blocked_lkr: number;
    avg_reply_seconds: number | null;
  };
  weekly: { cases: number; blocked_lkr: number; held_lkr: number }[];
  queue: CaseRow[];
};

function money(amount: number): { num: string; unit: string } {
  if (amount >= 1_000_000) return { num: (amount / 1_000_000).toFixed(2), unit: "M Rs" };
  return { num: amount.toLocaleString("en-US"), unit: "Rs" };
}

function reply(seconds: number | null): { num: string; unit: string } {
  if (seconds == null) return { num: "—", unit: "" };
  if (seconds < 90) return { num: String(seconds), unit: "sec" };
  return { num: (seconds / 60).toFixed(1), unit: "min" };
}

export default function OverviewPage() {
  const { refreshKey, query, setDetails, health } = useShell();
  const [data, setData] = useState<Overview | null>(null);
  const [customers, setCustomers] = useState<MapCustomer[]>([]);
  const [events, setEvents] = useState<MapEvent[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let stop = false;
    api<Overview>("/api/overview")
      .then((body) => { if (!stop) { setData(body); setError(""); } })
      .catch((err) => { if (!stop) setError(err.message); });
    api<{ customers: MapCustomer[]; sim_events: MapEvent[] }>("/api/map")
      .then((body) => { if (!stop) { setCustomers(body.customers); setEvents(body.sim_events); } })
      .catch(() => undefined);
    return () => { stop = true; };
  }, [refreshKey]);

  const queue = (data?.queue || []).filter((row) => matchesQuery(query, [row.id, row.customer_name, row.bank, row.payee, row.district]));
  const weekly = data?.weekly || [];
  const held = money(data?.kpis.held_lkr || 0);
  const blocked = money(data?.kpis.blocked_lkr || 0);
  const replyStat = reply(data?.kpis.avg_reply_seconds ?? null);
  const offline = health && !health.llm.meta_configured && !health.llm.grok_configured;
  const chip = (data?.kpis.waiting || 0) > 0 ? "WATCH" : "PROTECTED";

  function choose(row: CaseRow) {
    setDetails({
      title: row.id,
      subtitle: row.customer_name,
      sections: [
        { label: "Amount", value: lkr(row.amount_lkr) },
        { label: "Payee", value: row.payee },
        { label: "Bank", value: `${row.bank} · ${row.district}` },
        { label: "Rule", value: row.policy_decision || "—" },
        { label: "Status", value: statusLabel(row.status) },
        { label: "Updated", value: colombo(row.updated_at) },
        { label: "Open", value: <Link href={`/cases/${row.id}`}>Case file</Link> },
      ],
    });
  }

  useEffect(() => {
    setDetails(null);
  }, [setDetails]);

  return (
    <>
      <PageHeader crumb="Monitor / Overview" title="Sri Lanka overview" subtitle="Simulated banking network · Asia/Colombo" chip={chip} />
      {offline && <Note>No model key found, so the agents use the offline rules engine. Add META_API_KEY or XAI_API_KEY and restart.</Note>}
      {health?.sms === "log" && <Note>SMS is log-only. Messages still contain no link and no code.</Note>}
      {error && <Note>{error}</Note>}
      <FraudAnalytics />
      <div className="kpi-strip">
        <Cell label="CASES TODAY" num={String(data?.kpis.cases_today ?? "—")} unit="" caption="opened today" series={weekly.map((point) => point.cases)} />
        <Cell label="ON HOLD" num={held.num} unit={held.unit} caption="money not moved" series={weekly.map((point) => point.held_lkr)} color="#b86e00" />
        <Cell label="REPLY TIME" num={replyStat.num} unit={replyStat.unit} caption="customer answer" series={weekly.map((point) => point.cases)} />
        <Cell label="BLOCKED" num={blocked.num} unit={blocked.unit} caption="stopped as fraud" series={weekly.map((point) => point.blocked_lkr)} color="#b42318" />
        <Cell label="WAITING" num={String(data?.kpis.waiting ?? "—")} unit="" caption="on the customer" series={weekly.map((point) => point.cases)} color="#9a6200" />
        <Cell label="OPEN CASES" num={String(data?.kpis.open_cases ?? "—")} unit="" caption="still with the desk" series={weekly.map((point) => point.cases)} />
      </div>
      <div className="split">
        <section className="card">
          <div className="panel-head">
            <h2>Customer map</h2>
            <Link href="/map">Full map</Link>
          </div>
          <div className="map-embed">
            <MapView
              customers={customers}
              events={events}
              showCustomers
              showEvents={false}
              onSelect={(title, sections) => setDetails({ title, sections: sections.map((section) => ({ label: section.label, value: section.label === "Held" ? lkr(Number(section.value)) : section.value })) })}
            />
          </div>
          <div className="legend-row">
            <span><i className="legend-dot" style={{ background: "#98a29f" }} /> Normal</span>
            <span><i className="legend-dot" style={{ background: "#1f8a84" }} /> Watch</span>
            <span><i className="legend-dot" style={{ background: "#b86e00" }} /> Held</span>
            <span><i className="legend-dot" style={{ background: "#b42318" }} /> Blocked</span>
          </div>
          <p className="fine">{customers.filter((item) => markerTone(item) !== "normal").length} customers outside normal. Green is not used here; teal means watch.</p>
        </section>
        <section className="card">
          <div className="panel-head">
            <h2>Work queue</h2>
            <Link href="/cases">All cases</Link>
          </div>
          {queue.length === 0 ? (
            <div className="queue-empty">No open cases. The book is quiet. Open Demo controls to raise a SIM-swap case.</div>
          ) : (
            <table className="data">
              <thead><tr><th>Case</th><th>Customer</th><th>Amount</th><th>Status</th></tr></thead>
              <tbody>
                {queue.map((row) => (
                  <tr key={row.id} onClick={() => choose(row)}>
                    <td>{row.id}</td>
                    <td>{row.customer_name}</td>
                    <td>{lkr(row.amount_lkr)}</td>
                    <td>{statusLabel(row.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </>
  );
}

function Cell({ label, num, unit, caption, series, color }: { label: string; num: string; unit: string; caption: string; series: number[]; color?: string }) {
  return (
    <div className="kpi-cell">
      <div className="label">{label}</div>
      <div className="figure"><span className="num">{num}</span>{unit && <span className="unit">{unit}</span>}</div>
      <Spark values={series} color={color} />
      <div className="caption">{caption}</div>
    </div>
  );
}
