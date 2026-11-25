"use client";

import { useEffect, useState } from "react";
import { DataTable, Note, PageHeader } from "@/components/ui";
import { matchesQuery, useShell } from "@/components/Shell";
import { api } from "@/lib/api";

type Agent = {
  id: string;
  name: string;
  task: string;
  kind: string;
  configured_model: string;
  fallback_model: string | null;
  last_provider: string | null;
  last_model: string | null;
  calls: number;
  errors: number;
  success_rate: number | null;
  latency_ms: number | null;
  fallbacks: number;
};

export default function AgentsPage() {
  const { refreshKey, query, setDetails } = useShell();
  const [rows, setRows] = useState<Agent[]>([]);
  const [charters, setCharters] = useState<{ id: string; name: string; task: string; tools: string[]; forbidden: string[]; autonomy: string }[]>([]);
  const [primary, setPrimary] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    api<{ agents: Agent[]; primary: { primary: string; meta_model: string; grok_model: string } }>("/api/agents")
      .then((body) => {
        setRows(body.agents);
        setPrimary(body.primary.primary === "mock" ? "No API key is set, so notes come from the deterministic mock." : `Primary provider: ${body.primary.primary}.`);
      })
      .catch(() => setRows([]));
    api<{ agents: { id: string; name: string; task: string; tools: string[]; forbidden: string[]; autonomy: string }[] }>("/api/charters")
      .then((body) => setCharters(body.agents))
      .catch(() => setCharters([]));
  }, [refreshKey]);

  const visible = rows.filter((row) => matchesQuery(query, [row.name, row.task, row.last_provider, row.configured_model]));

  function choose(row: Agent) {
    setSelected(row.id);
    setDetails({
      title: row.name,
      subtitle: row.task,
      sections: [
        { label: "Kind", value: row.kind === "rules" ? "Deterministic rules" : "Language model note" },
        { label: "Configured", value: row.configured_model },
        { label: "Fallback", value: row.fallback_model || "—" },
        { label: "Last provider", value: row.last_provider || "No call yet" },
        { label: "Last model", value: row.last_model || "—" },
        { label: "Calls", value: String(row.calls) },
        { label: "Success", value: row.success_rate == null ? "—" : `${row.success_rate}%` },
        { label: "Mean latency", value: row.latency_ms == null ? "—" : `${row.latency_ms} ms` },
        { label: "Fallbacks", value: String(row.fallbacks) },
      ],
    });
  }

  useEffect(() => {
    const telecom = visible.find((row) => row.id === "telecom") || visible[0];
    if (telecom) choose(telecom);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible[0]?.id]);

  return (
    <>
      <PageHeader crumb="Assure / Agents" title="Agents and models" subtitle="The model writes the note. The rules choose hold, block, or release." />
      <Note>{primary || "Loading engine status."} A model becomes the one on the case only when it answers. The policy row is rules, and it is never promoted by a model.</Note>
      <DataTable
        rows={visible}
        selected={selected}
        onSelect={choose}
        columns={[
          { key: "name", header: "Agent" },
          { key: "kind", header: "Kind", render: (row) => <span className={`chip ${row.kind === "rules" ? "neutral" : "info"}`}>{row.kind}</span> },
          { key: "configured_model", header: "Configured" },
          { key: "last_provider", header: "Last provider", render: (row) => row.last_provider || "—" },
          { key: "success_rate", header: "Success", render: (row) => row.success_rate == null ? "—" : `${row.success_rate}%` },
          { key: "latency_ms", header: "Latency", render: (row) => row.latency_ms == null ? "—" : `${row.latency_ms} ms` },
          { key: "fallbacks", header: "Fallbacks" },
          { key: "calls", header: "Calls" },
        ]}
      />
      <h2 style={{ marginTop: 18 }}>Charters</h2>
      <div className="split">
        {charters.map((item) => (
          <section className="card" key={item.id}>
            <h2>{item.name}</h2>
            <p>{item.task}</p>
            <p className="fine">Tools: {item.tools.join(", ") || "none"}</p>
            <p className="fine">Refused: {item.forbidden.join(", ")}</p>
            <p className="quiet">{item.autonomy}</p>
          </section>
        ))}
      </div>
    </>
  );
}
