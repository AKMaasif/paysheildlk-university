"use client";

import { useEffect, useMemo, useState } from "react";
import { FlowMap, stageState } from "@/components/FlowMap";
import { LiveTerminal } from "@/components/LiveTerminal";
import { Note, PageHeader } from "@/components/ui";
import { matchesQuery, useShell, type LiveEvent } from "@/components/Shell";
import { api } from "@/lib/api";
import { chipFor, colombo, humanizeResult, lkr, mmss, statusLabel } from "@/lib/format";
import { nodeFor } from "@/lib/terminal";

type CaseRow = {
  id: string;
  customer_name: string;
  status: string;
  amount_lkr: number;
  payee: string;
  waiting_since: string | null;
  alert_count: number;
  bank: string;
  updated_at: string;
};

const LANES = [
  ["supervisor", "Supervisor"],
  ["telecom", "Telecom"],
  ["banking", "Banking"],
  ["risk", "Risk"],
  ["policy", "Policy rules"],
  ["alert", "Alert"],
  ["verification", "Verification"],
  ["response", "Response"],
  ["case", "Case report"],
];

export default function LivePage() {
  const { events, refreshKey, query, setDetails, health } = useShell();
  const [cases, setCases] = useState<CaseRow[]>([]);
  const [caseId, setCaseId] = useState<string>("");
  const [history, setHistory] = useState<LiveEvent[]>([]);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    api<{ cases: CaseRow[] }>("/api/cases")
      .then((body) => setCases(body.cases))
      .catch(() => setCases([]));
  }, [refreshKey, events.length]);

  useEffect(() => {
    const waiting = cases.find((item) => item.status === "waiting_customer" || item.status === "waiting_verification");
    // A link such as /live?case=PS-26-00050 opens that case.
    const asked = typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("case") || "";
    setCaseId((current) => {
      if (asked && cases.some((item) => item.id === asked) && current !== asked && !current) return asked;
      return current && cases.some((item) => item.id === current) ? current : (cases.some((item) => item.id === asked) ? asked : (waiting || cases[0])?.id || "");
    });
  }, [cases]);

  useEffect(() => {
    if (!caseId) return;
    api<{ events: LiveEvent[] }>(`/api/events?case_id=${encodeURIComponent(caseId)}&limit=500`)
      .then((body) => setHistory(body.events))
      .catch(() => setHistory([]));
  }, [caseId, refreshKey]);

  const selected = cases.find((item) => item.id === caseId) || null;
  const caseEvents = useMemo(() => {
    const merged = new Map<string, LiveEvent>();
    history.forEach((event) => merged.set(event.id, event));
    events.filter((event) => event.case_id === caseId).forEach((event) => merged.set(event.id, event));
    return [...merged.values()]
      .filter((event) => matchesQuery(query, [event.agent, event.tool_name, event.result_summary, event.message, event.provider]))
      .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  }, [history, events, caseId, query]);
  const latestByAgent = new Map<string, LiveEvent>();
  caseEvents.forEach((event) => latestByAgent.set(event.agent, event));

  const waiting = selected && (selected.status === "waiting_customer" || selected.status === "waiting_verification");
  const realertMs = (health?.realert_seconds || 180) * 1000;
  const escalateMs = (health?.escalate_seconds || 600) * 1000;
  const started = selected?.waiting_since ? new Date(selected.waiting_since).getTime() : 0;
  const elapsed = started ? now - started : 0;

  function show(event: LiveEvent) {
    setDetails({
      title: event.tool_name || event.agent,
      subtitle: `${event.agent} · ${statusLabel(event.state)}`,
      sections: [
        { label: "When", value: colombo(event.created_at) },
        { label: "State", value: event.state },
        { label: "Tool", value: event.tool_name || "—" },
        { label: "Arguments", value: <pre className="pre">{JSON.stringify(event.tool_args || {}, null, 2)}</pre> },
        { label: "Result", value: humanizeResult(event.result_summary || event.message) || "—" },
        { label: "Handoff", value: event.handoff_from ? `${event.handoff_from} → ${event.handoff_to}` : "—" },
        { label: "Provider", value: event.provider ? `${event.provider}${event.fell_back ? " (fallback)" : ""}` : "—" },
        { label: "Latency", value: event.latency_ms != null ? `${event.latency_ms} ms` : "—" },
        { label: "Case", value: event.case_id },
      ],
    });
  }

  return (
    <>
      <PageHeader
        crumb="Monitor / Live activity"
        title="Live agent activity"
        subtitle="Telecom and banking run together. The rule layer decides."
        chip={waiting ? "WAITING" : "QUIET"}
        extra={
          <label className="saved">Case
            <select value={caseId} onChange={(event) => setCaseId(event.target.value)}>
              {cases.length === 0 && <option value="">No cases</option>}
              {cases.map((item) => (
                <option key={item.id} value={item.id}>{item.id} · {item.customer_name}</option>
              ))}
            </select>
          </label>
        }
      />
      <Note>
        Click a block to read the tool call or the model note. A dashed block did not run on this case. The rule step does not call a model.
      </Note>
      {waiting && selected && (
        <div className="wait-line">
          <span>Waiting for the customer · {selected.customer_name} · {lkr(selected.amount_lkr)} to {selected.payee}</span>
          <span>
            {selected.alert_count < 2 ? <>Re-alert in <strong>{mmss(realertMs - elapsed)}</strong> · </> : <>Reminder sent · </>}
            Escalate in <strong>{mmss(escalateMs - elapsed)}</strong>
          </span>
        </div>
      )}
      <FlowMap
        events={caseEvents}
        status={selected?.status}
        focusId={focusId}
        onPick={(event, meta) => {
          if (event) {
            setFocusId(nodeFor(event));
            show(event);
          } else setDetails({
            title: meta.name,
            subtitle: meta.chip,
            sections: [{ label: "Result", value: "This step did not run on this case." }],
          });
        }}
      />
      <LiveTerminal
        events={caseEvents}
        realertSeconds={health?.realert_seconds}
        escalateSeconds={health?.escalate_seconds}
        onSelect={(event, nodeId) => {
          setFocusId(nodeId);
          show(event);
        }}
      />
      <div className="lane-row">
          {LANES.map(([id, label]) => {
            const latest = latestByAgent.get(id);
            const state = stageState(id, caseEvents, selected?.status);
            return (
              <button key={id} className={`lane state-${state}`} onClick={() => latest && show(latest)}>
                <div className="name">{label}</div>
                <div className="meta">
                  <span className={`chip ${chipFor(state === "waiting_for_customer" ? "waiting_customer" : state === "error" ? "error" : state === "done" ? "release" : "running")}`}>{state.replaceAll("_", " ")}</span>
                  <span>{latest?.tool_name || ""}</span>
                </div>
              </button>
            );
          })}
      </div>
    </>
  );
}
