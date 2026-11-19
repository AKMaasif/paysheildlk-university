"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { DataTable, Note, PageHeader } from "@/components/ui";
import { matchesQuery, useShell } from "@/components/Shell";
import { api, downloadAuthed } from "@/lib/api";
import { colombo, lkr, statusLabel } from "@/lib/format";

type ReportRow = { id: string; case_id: string; title: string; created_at: string; bank: string; status: string; amount_lkr: number };

export default function ReportsPage() {
  const { refreshKey, query, setDetails } = useShell();
  const [rows, setRows] = useState<ReportRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    api<{ reports: ReportRow[] }>("/api/reports").then((body) => setRows(body.reports)).catch(() => setRows([]));
  }, [refreshKey]);

  const visible = rows.filter((row) => matchesQuery(query, [row.case_id, row.title, row.bank, row.status]));

  async function choose(row: ReportRow) {
    setSelected(row.id);
    const full = await api<{ markdown: string }>(`/api/reports/${row.id}`);
    setDetails({
      title: row.title,
      subtitle: row.case_id,
      sections: [
        { label: "Downloads", value: <span><button className="btn" onClick={() => downloadAuthed(`/api/reports/${row.id}/markdown`, `${row.case_id}.md`)}>Markdown</button> <button className="btn" onClick={() => downloadAuthed(`/api/reports/${row.id}/pdf`, `${row.case_id}.pdf`)}>PDF</button></span> },
        { label: "Case", value: <Link href={`/cases/${row.case_id}`}>{row.case_id}</Link> },
        { label: "Preview", value: <pre className="pre">{full.markdown.slice(0, 900)}</pre> },
      ],
    });
  }

  useEffect(() => {
    if (visible[0]) choose(visible[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible[0]?.id]);

  return (
    <>
      <PageHeader crumb="Respond / Reports" title="Incident reports" subtitle="Demonstration notes for a fraud desk. Not a CBSL return." chip="FILED" />
      <DataTable
        rows={visible}
        selected={selected}
        onSelect={choose}
        columns={[
          { key: "case_id", header: "Case", render: (row) => <Link href={`/cases/${row.case_id}`}>{row.case_id}</Link> },
          { key: "bank", header: "Bank" },
          { key: "status", header: "Disposition", render: (row) => statusLabel(row.status) },
          { key: "amount_lkr", header: "Amount", render: (row) => lkr(row.amount_lkr) },
          { key: "created_at", header: "Filed", render: (row) => colombo(row.created_at) },
        ]}
      />
    </>
  );
}
