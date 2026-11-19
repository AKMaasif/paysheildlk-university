"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { DataTable, Note, PageHeader } from "@/components/ui";
import { matchesQuery, useShell } from "@/components/Shell";
import { api } from "@/lib/api";
import { chipFor, colombo, lkr, statusLabel } from "@/lib/format";

type CaseRow = {
  id: string;
  customer_name: string;
  phone: string;
  bank: string;
  province: string;
  district: string;
  amount_lkr: number;
  payee: string;
  risk_score: number | null;
  severity: string;
  status: string;
  policy_decision: string | null;
  policy_reason: string | null;
  updated_at: string;
  origin: string;
};

export default function CasesPage() {
  const { refreshKey, query, setDetails } = useShell();
  const [rows, setRows] = useState<CaseRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ cases: CaseRow[] }>("/api/cases")
      .then((body) => { setRows(body.cases); setError(""); })
      .catch((err) => setError(err.message));
  }, [refreshKey]);

  const visible = rows.filter((row) => matchesQuery(query, [row.id, row.customer_name, row.bank, row.payee, row.status, row.district]));

  function choose(row: CaseRow) {
    setSelected(row.id);
    setDetails({
      title: row.customer_name,
      subtitle: row.id,
      sections: [
        { label: "Amount", value: `${lkr(row.amount_lkr)} to ${row.payee}` },
        { label: "Bank", value: `${row.bank} · ${row.district}` },
        { label: "Phone", value: row.phone },
        { label: "Score", value: row.risk_score ?? "—" },
        { label: "Rule", value: row.policy_decision || "—" },
        { label: "Why", value: row.policy_reason || "—" },
        { label: "Updated", value: colombo(row.updated_at) },
        { label: "File", value: <Link href={`/cases/${row.id}`}>Open case</Link> },
      ],
    });
  }

  useEffect(() => {
    if (visible[0]) choose(visible[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible[0]?.id]);

  return (
    <>
      <PageHeader crumb="Respond / Cases" title="Cases" subtitle="A case is a lead with a rule decision, not a verdict." chip="DESK" />
      {error && <Note tone="warn">{error}</Note>}
      <DataTable
        rows={visible}
        selected={selected}
        onSelect={choose}
        columns={[
          { key: "severity", header: "Severity", render: (row) => <span className={`chip ${chipFor(row.severity)}`}>{row.severity}</span> },
          { key: "id", header: "Case", render: (row) => <Link href={`/cases/${row.id}`}>{row.id}</Link> },
          { key: "customer_name", header: "Customer" },
          { key: "bank", header: "Bank" },
          { key: "amount_lkr", header: "Amount", render: (row) => lkr(row.amount_lkr) },
          { key: "policy_decision", header: "Rule", render: (row) => row.policy_decision || "—" },
          { key: "status", header: "Status", render: (row) => <span className={`chip ${chipFor(row.status)}`}>{statusLabel(row.status)}</span> },
          { key: "updated_at", header: "Updated", render: (row) => colombo(row.updated_at) },
        ]}
      />
    </>
  );
}
