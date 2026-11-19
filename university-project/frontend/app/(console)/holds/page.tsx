"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { DataTable, Note, PageHeader } from "@/components/ui";
import { matchesQuery, useShell } from "@/components/Shell";
import { api } from "@/lib/api";
import { chipFor, colombo, lkr, statusLabel } from "@/lib/format";

type Hold = {
  transaction_id: string;
  case_id: string | null;
  customer_name: string;
  bank: string;
  district: string;
  amount_lkr: number;
  payee: string;
  new_payee: boolean;
  occurred_at: string;
  case_status: string | null;
  risk_score: number | null;
  severity: string;
  policy_reason: string | null;
  can_override: boolean;
};

export default function HoldsPage() {
  const { refreshKey, query, refresh, setDetails, user } = useShell();
  const [rows, setRows] = useState<Hold[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [reason, setReason] = useState("Confirmed with the customer off-channel.");
  const [error, setError] = useState("");

  function load() {
    api<{ holds: Hold[] }>("/api/holds")
      .then((body) => setRows(body.holds))
      .catch((err) => setError(err.message));
  }
  useEffect(() => { load(); }, [refreshKey]);

  async function act(row: Hold, action: "release" | "block") {
    setError("");
    try {
      await api(`/api/transactions/${row.transaction_id}/override`, { method: "POST", body: JSON.stringify({ action, reason }) });
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Override failed");
    }
  }

  const visible = rows.filter((row) => matchesQuery(query, [row.customer_name, row.payee, row.bank, row.case_id, row.transaction_id]));
  const can = user?.role === "bank_supervisor" || user?.role === "super_admin";

  function choose(row: Hold) {
    setSelected(row.transaction_id);
    setDetails({
      title: lkr(row.amount_lkr),
      subtitle: `${row.customer_name} → ${row.payee}`,
      sections: [
        { label: "Transaction", value: row.transaction_id },
        { label: "Case", value: row.case_id ? <Link href={`/cases/${row.case_id}`}>{row.case_id}</Link> : "—" },
        { label: "Why it is held", value: row.policy_reason || "—" },
        { label: "New payee", value: row.new_payee ? "Yes" : "No" },
        { label: "Override", value: can ? (
          <div>
            <label className="field"><span>Reason</span><textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
            <div className="row-actions">
              <button className="btn primary" onClick={() => act(row, "release")}>Release</button>
              <button className="btn danger" onClick={() => act(row, "block")}>Block</button>
            </div>
          </div>
        ) : "Release and block sit with the bank supervisor." },
      ],
    });
  }

  useEffect(() => {
    if (visible[0]) choose(visible[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible[0]?.transaction_id, can]);

  return (
    <>
      <PageHeader crumb="Respond / On hold" title="Transactions on hold" subtitle="Money in this list has not moved. A supervisor can release or block it." chip="HELD" />
      {user?.role === "auditor" && <Note tone="warn">You are signed in as an auditor. This list is read-only.</Note>}
      {error && <Note tone="warn">{error}</Note>}
      <DataTable
        rows={visible}
        selected={selected}
        rowId={(row) => row.transaction_id}
        onSelect={choose}
        columns={[
          { key: "severity", header: "Severity", render: (row) => <span className={`chip ${chipFor(row.severity)}`}>{row.severity}</span> },
          { key: "customer_name", header: "Customer" },
          { key: "bank", header: "Bank" },
          { key: "amount_lkr", header: "Amount", render: (row) => lkr(row.amount_lkr) },
          { key: "payee", header: "Payee" },
          { key: "case_status", header: "Case", render: (row) => <span className={`chip ${chipFor(row.case_status)}`}>{statusLabel(row.case_status)}</span> },
          { key: "occurred_at", header: "Since", render: (row) => colombo(row.occurred_at) },
        ]}
      />
    </>
  );
}
