"use client";

import { useEffect, useState } from "react";
import { Kpi, Note, PageHeader } from "@/components/ui";
import { matchesQuery, useShell } from "@/components/Shell";
import { api } from "@/lib/api";
import { colombo, statusLabel } from "@/lib/format";

type Summary = { pending_requests: number; users: number; disabled_users: number; active_users: number; audit_entries: number; refused_recent: number };
type RequestRow = { id: string; name: string; email: string; organisation: string; role_asked: string; scope_asked: string; reason: string; state: string; decided_by: string | null; created_at: string };
type UserRow = { id: string; full_name: string; email: string; role: string; role_label: string; status: string; bank: string | null };
type AuditRow = { id: string; actor_email: string; action: string; target: string | null; detail: string | null; created_label: string };

export default function AdminPage() {
  const { refreshKey, refresh, query, setDetails } = useShell();
  const [tab, setTab] = useState<"requests" | "users" | "audit">("requests");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [requests, setRequests] = useState<RequestRow[]>([]);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api<Summary>("/api/admin/summary"),
      api<{ requests: RequestRow[] }>("/api/admin/requests"),
      api<{ users: UserRow[] }>("/api/admin/users"),
      api<{ entries: AuditRow[] }>("/api/admin/audit"),
    ]).then(([s, r, u, a]) => {
      setSummary(s); setRequests(r.requests); setUsers(u.users); setAudit(a.entries); setError("");
      const first = r.requests.find((row) => row.state === "pending") || r.requests[0];
      if (first) {
        setDetails({
          title: first.name,
          subtitle: first.email,
          sections: [
            { label: "Organisation", value: first.organisation },
            { label: "Role", value: first.role_asked },
            { label: "Scope", value: first.scope_asked },
            { label: "Reason", value: first.reason },
            { label: "State", value: first.state },
          ],
        });
      }
    }).catch((err) => setError(err.message));
  }, [refreshKey, setDetails]);

  async function decide(row: RequestRow, decision: "approve" | "refuse") {
    const body = await api<{ invite_token?: string }>(`/api/admin/requests/${row.id}`, { method: "POST", body: JSON.stringify({ decision }) });
    setDetails({
      title: row.name,
      sections: [
        { label: "Decision", value: decision },
        { label: "Invite token", value: body.invite_token || "No new invite. The email may already have an account." },
      ],
    });
    refresh();
  }

  async function setStatus(row: UserRow, status: "active" | "disabled") {
    await api(`/api/admin/users/${row.id}`, { method: "POST", body: JSON.stringify({ status }) });
    refresh();
  }

  return (
    <>
      <PageHeader crumb="Assure / Administration" title="Administration" subtitle="Access is granted per person and written to the audit log." />
      <Note>Everything here is enforced on the server and recorded in the audit log. Access is approved per person. Nobody can approve their own removal, and at least one super admin must remain able to sign in.</Note>
      {error && <Note tone="warn">{error}</Note>}
      <div className="kpis">
        <Kpi label="Pending requests" value={String(summary?.pending_requests ?? "—")} sub="Waiting for a decision" />
        <Kpi label="Operator users" value={String(summary?.users ?? "—")} sub={`${summary?.disabled_users ?? 0} disabled · ${summary?.active_users ?? 0} active`} />
        <Kpi label="Audit entries" value={String(summary?.audit_entries ?? "—")} sub={`${summary?.refused_recent ?? 0} refusals on record`} />
      </div>
      <div className="tabs-inline">
        {(["requests", "users", "audit"] as const).map((item) => (
          <button key={item} className={`btn ${tab === item ? "on" : ""}`} onClick={() => setTab(item)}>{item[0].toUpperCase() + item.slice(1)}</button>
        ))}
      </div>
      {tab === "requests" && (
        <table className="data">
          <thead><tr><th>Person</th><th>Role asked</th><th>Scope asked</th><th>State</th><th>Asked</th></tr></thead>
          <tbody>
            {requests.filter((row) => matchesQuery(query, [row.name, row.email, row.organisation, row.state])).map((row) => (
              <tr key={row.id} onClick={() => setDetails({
                title: row.name,
                subtitle: row.email,
                sections: [
                  { label: "Organisation", value: row.organisation },
                  { label: "Role", value: row.role_asked },
                  { label: "Scope", value: row.scope_asked },
                  { label: "Reason", value: row.reason },
                  { label: "State", value: row.state },
                  { label: "Decide", value: row.state === "pending" ? (
                    <div className="row-actions">
                      <button className="btn primary" onClick={() => decide(row, "approve")}>Approve</button>
                      <button className="btn danger" onClick={() => decide(row, "refuse")}>Refuse</button>
                    </div>
                  ) : (row.decided_by || "Decided") },
                ],
              })}>
                <td>{row.name}</td><td>{row.role_asked}</td><td>{row.scope_asked}</td><td>{row.state}</td><td>{colombo(row.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {tab === "users" && (
        <table className="data">
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th></tr></thead>
          <tbody>
            {users.filter((row) => matchesQuery(query, [row.full_name, row.email, row.role])).map((row) => (
              <tr key={row.id} onClick={() => setDetails({
                title: row.full_name,
                subtitle: row.email,
                sections: [
                  { label: "Role", value: row.role_label },
                  { label: "Bank scope", value: row.bank || "All banks" },
                  { label: "Status", value: statusLabel(row.status) },
                  { label: "Change", value: (
                    <div className="row-actions">
                      <button className="btn" onClick={() => setStatus(row, "active")}>Enable</button>
                      <button className="btn danger" onClick={() => setStatus(row, "disabled")}>Disable</button>
                    </div>
                  ) },
                ],
              })}>
                <td>{row.full_name}</td><td>{row.email}</td><td>{row.role_label}</td><td>{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {tab === "audit" && (
        <table className="data">
          <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
          <tbody>
            {audit.filter((row) => matchesQuery(query, [row.actor_email, row.action, row.target, row.detail])).map((row) => (
              <tr key={row.id} onClick={() => setDetails({ title: row.action, subtitle: row.actor_email, sections: [{ label: "When", value: row.created_label }, { label: "Target", value: row.target || "—" }, { label: "Detail", value: row.detail || "—" }] })}>
                <td>{row.created_label}</td><td>{row.actor_email}</td><td>{row.action}</td><td>{row.target}</td><td>{row.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
