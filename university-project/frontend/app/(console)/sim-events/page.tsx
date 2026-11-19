"use client";

import { useEffect, useState } from "react";
import { DataTable, Note, PageHeader } from "@/components/ui";
import { matchesQuery, useShell } from "@/components/Shell";
import { api } from "@/lib/api";
import { chipFor, colombo, prettyPhone } from "@/lib/format";

type Source = { source: string; granularity: string; newest: string | null; age: string; status: string; basis: string };
type EventRow = {
  id: string;
  phone: string;
  event_type: string;
  occurred_at: string;
  age: string;
  detail: Record<string, unknown>;
  origin: string;
  case_id: string | null;
  customer_name: string;
  bank: string;
  district: string;
  status?: string;
};

export default function SimEventsPage() {
  const { refreshKey, query, setDetails } = useShell();
  const [sources, setSources] = useState<Source[]>([]);
  const [rows, setRows] = useState<EventRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    api<{ sources: Source[]; events: EventRow[] }>("/api/sim-events")
      .then((body) => { setSources(body.sources); setRows(body.events); })
      .catch(() => { setSources([]); setRows([]); });
  }, [refreshKey]);

  const visible = rows.filter((row) => matchesQuery(query, [row.customer_name, row.event_type, row.phone, row.district, row.case_id]));

  useEffect(() => {
    const row = visible[0];
    if (!row) return;
    setSelected(row.id);
    setDetails({
      title: row.event_type.replaceAll("_", " "),
      subtitle: row.customer_name,
      sections: [
        { label: "Phone", value: prettyPhone(row.phone) },
        { label: "When", value: `${colombo(row.occurred_at)} · ${row.age}` },
        { label: "Bank", value: `${row.bank} · ${row.district}` },
        { label: "Case", value: row.case_id || "—" },
        { label: "Payload", value: <pre className="pre">{JSON.stringify(row.detail, null, 2)}</pre> },
      ],
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible[0]?.id]);

  return (
    <>
      <PageHeader crumb="Monitor / SIM events" title="SIM events" subtitle="CAMARA check and retrieve-date, plus the extra mock feeds." />
      <div className="table-wrap" style={{ marginBottom: 12 }}>
        <table className="data">
          <thead>
            <tr><th>Source</th><th>Granularity</th><th>Newest</th><th>Age</th><th>Status</th><th>Basis</th></tr>
          </thead>
          <tbody>
            {sources.map((source) => (
              <tr key={source.source} onClick={() => setDetails({ title: source.source, sections: [{ label: "Status", value: source.status }, { label: "Basis", value: source.basis }, { label: "Newest", value: source.newest ? colombo(source.newest) : "never" }] })}>
                <td>{source.source}</td>
                <td>{source.granularity}</td>
                <td>{source.newest ? colombo(source.newest) : "—"}</td>
                <td>{source.age}</td>
                <td><span className={`chip ${source.status === "Not connected" ? "neutral" : source.status === "Mock feed" ? "info" : "neutral"}`}>{source.status}</span></td>
                <td>{source.basis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <DataTable
        rows={visible}
        selected={selected}
        onSelect={(row) => {
          setSelected(row.id);
          setDetails({
            title: row.event_type,
            subtitle: row.customer_name,
            sections: [
              { label: "Phone", value: prettyPhone(row.phone) },
              { label: "When", value: `${colombo(row.occurred_at)} · ${row.age}` },
              { label: "Bank", value: `${row.bank} · ${row.district}` },
              { label: "Case", value: row.case_id || "—" },
              { label: "Payload", value: <pre className="pre">{JSON.stringify(row.detail, null, 2)}</pre> },
            ],
          });
        }}
        columns={[
          { key: "event_type", header: "Type", render: (row) => <span className={`chip ${chipFor(row.event_type === "sim_freeze" ? "block" : "running")}`}>{row.event_type.replaceAll("_", " ")}</span> },
          { key: "customer_name", header: "Customer" },
          { key: "phone", header: "Number", render: (row) => prettyPhone(row.phone) },
          { key: "district", header: "District" },
          { key: "occurred_at", header: "When", render: (row) => colombo(row.occurred_at) },
          { key: "age", header: "Age" },
          { key: "case_id", header: "Case", render: (row) => row.case_id || "—" },
        ]}
      />
    </>
  );
}
