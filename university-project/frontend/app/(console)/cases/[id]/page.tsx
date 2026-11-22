"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Note, PageHeader } from "@/components/ui";
import { useShell, type LiveEvent } from "@/components/Shell";
import { api, downloadAuthed } from "@/lib/api";
import { colombo, humanizeResult, lkr, statusLabel } from "@/lib/format";

type Detail = {
  case: {
    id: string;
    customer_name: string;
    status: string;
    amount_lkr: number;
    payee: string;
    risk_score: number | null;
    severity: string;
    policy_decision: string | null;
    policy_reason: string | null;
    telecom_summary: string | null;
    banking_summary: string | null;
    risk_summary: string | null;
    risk_factors: { label: string; points: number }[];
    verification_detail: string | null;
    customer_decision: string | null;
    sim_freeze_ref: string | null;
  };
  customer: { full_name: string; phone: string; email: string; bank: string; account_number: string; branch: string; district: string; province: string; restricted: boolean; restricted_actions: string[] };
  transaction: { id: string; status: string; amount_lkr: number; payee: string; payee_bank: string | null; new_payee: boolean; occurred_at: string } | null;
  events: LiveEvent[];
  report: { id: string; title: string } | null;
};

export default function CaseDetailPage() {
  const params = useParams<{ id: string }>();
  const { refreshKey, setDetails } = useShell();
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Detail>(`/api/cases/${params.id}`)
      .then((body) => { setData(body); setError(""); })
      .catch((err) => setError(err.message));
  }, [params.id, refreshKey]);

  useEffect(() => {
    const event = data?.events.at(-1);
    if (!event) return;
    setDetails({
      title: event.tool_name || event.agent,
      subtitle: statusLabel(event.state),
      sections: [
        { label: "When", value: colombo(event.created_at) },
        { label: "Agent", value: event.agent },
        { label: "Arguments", value: <pre className="pre">{JSON.stringify(event.tool_args || {}, null, 2)}</pre> },
        { label: "Result", value: humanizeResult(event.result_summary || event.message) || "—" },
        { label: "Provider", value: event.provider || "—" },
      ],
    });
  }, [data, setDetails]);

  if (error) return <Note tone="warn">{error}</Note>;
  if (!data) return <p className="quiet">Loading the case…</p>;
  const item = data.case;

  return (
    <>
      <PageHeader crumb={`Respond / Cases / ${item.id}`} title={item.customer_name} subtitle={`${item.id} · ${lkr(item.amount_lkr)} to ${item.payee}`} chip={statusLabel(item.status).toUpperCase()} />
      <Note>
        {item.customer_name} · {lkr(item.amount_lkr)} to {item.payee}. Rule: {item.policy_decision || "pending"}. Outcome: {statusLabel(item.status)}. Score {item.risk_score ?? "—"}.
      </Note>
      <div className="split">
        <div className="card">
          <h2>What the agents wrote</h2>
          <p><strong>Telecom. </strong>{item.telecom_summary || "Not written yet."}</p>
          <p><strong>Banking. </strong>{item.banking_summary || "Not written yet."}</p>
          <p><strong>Risk. </strong>{item.risk_summary || "Not written yet."}</p>
          <p><strong>Rule. </strong>{item.policy_reason || "Not decided yet."}</p>
          <p className="fine">Customer answer: {item.customer_decision || "none yet"}. {item.verification_detail || ""}</p>
        </div>
        <div className="card">
          <h2>Factors</h2>
          {(item.risk_factors || []).map((factor) => (
            <div key={factor.label} className="factor-line"><span>{factor.label}</span><span>{factor.points >= 0 ? "+" : "\u2212"}{Math.abs(factor.points)}</span></div>
          ))}
          {data.report && (
            <div className="row-actions">
              <button className="btn" onClick={() => downloadAuthed(`/api/reports/${data.report!.id}/markdown`, `${item.id}.md`)}>Markdown</button>
              <button className="btn" onClick={() => downloadAuthed(`/api/reports/${data.report!.id}/pdf`, `${item.id}.pdf`)}>PDF</button>
            </div>
          )}
          <p className="fine">{data.customer.bank} · {data.customer.account_number} · {data.customer.district}. Account limited: {data.customer.restricted ? "yes" : "no"}. Freeze: {item.sim_freeze_ref || "not requested"}.</p>
        </div>
      </div>
      <h2 style={{ fontSize: 16 }}>Timeline</h2>
      <div className="timeline">
        {data.events.map((event) => (
          <div
            key={event.id}
            className="tl-item"
            onClick={() => setDetails({
              title: event.tool_name || event.agent,
              subtitle: statusLabel(event.state),
              sections: [
                { label: "When", value: colombo(event.created_at) },
                { label: "Agent", value: event.agent },
                { label: "Arguments", value: <pre className="pre">{JSON.stringify(event.tool_args || {}, null, 2)}</pre> },
                { label: "Result", value: humanizeResult(event.result_summary || event.message) || "—" },
                { label: "Provider", value: event.provider || "—" },
                { label: "Structured result", value: <pre className="pre">{JSON.stringify(event.result || {}, null, 2)}</pre> },
                { label: "Trace", value: <pre className="pre">{JSON.stringify({ phase: event.phase || "single event", call_id: event.call_id, ...(event.meta || {}) }, null, 2)}</pre> },
              ],
            })}
          >
            <div className="when">{colombo(event.created_at)} · {event.agent} · {event.tool_name || event.state}</div>
            <div>{event.phase === "started" ? "in progress, no result yet" : humanizeResult(event.result_summary || event.message)}</div>
          </div>
        ))}
      </div>
    </>
  );
}
