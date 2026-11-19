"use client";

import { useCallback, useEffect, useState } from "react";
import { Note, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";

type Band = { from: number; to: number; cases: number; fraud: number; legitimate: number; hold_was_right: number | null };
type Proposal = {
  id: string; key: string; current: number; proposed: number; reason: string; status: string;
  created_at: string; decided_by: string | null; decision_note: string | null;
};
type View = {
  thresholds: Record<string, { value: number; default: number; min: number; max: number }>;
  evidence: { hold_score: number; bands: Band[]; answered_cases: number };
  min_cases: number;
  proposals: Proposal[];
  can_approve: boolean;
};

const pct = (x: number | null) => (x === null ? "n/a" : `${Math.round(x * 100)}%`);

export default function CalibrationPage() {
  const [data, setData] = useState<View | null>(null);
  const [note, setNote] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => api<View>("/api/calibration").then(setData).catch((err) => setError(err.message)), []);
  useEffect(() => { load(); }, [load]);

  async function propose() {
    setError(""); setMessage("");
    try {
      const body = await api<{ proposals: Proposal[] }>("/api/calibration/propose", { method: "POST" });
      setMessage(body.proposals.length ? "The Analytics agent wrote a proposal. It changes nothing until an analyst approves it." : "The evidence does not support a change right now.");
      load();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not ask for a proposal"); }
  }

  async function decide(id: string, decision: "approve" | "reject") {
    setError(""); setMessage("");
    try {
      await api(`/api/calibration/proposals/${id}/decide`, { method: "POST", body: JSON.stringify({ decision, note: note[id] || "" }) });
      setMessage(decision === "approve" ? "Approved. The new hold score is in force now." : "Rejected. Nothing changed.");
      load();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not record the decision"); }
  }

  async function reset() {
    setError(""); setMessage("");
    try { await api("/api/calibration/reset", { method: "POST" }); setMessage("Back to the default hold score."); load(); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not reset"); }
  }

  if (!data) return <p className="quiet">{error || "Loading"}</p>;
  const hold = data.thresholds.hold_score;

  return (
    <>
      <PageHeader crumb="Assure / Calibration" title="Rule calibration" subtitle="The Analytics agent learns from customers' answers and may propose a change to one number in the rules. Nothing changes until an analyst approves it." chip="HUMAN GATE" />
      {error && <Note tone="warn">{error}</Note>}
      {message && <Note>{message}</Note>}

      <div className="split">
        <div className="card">
          <h2>Hold score</h2>
          <p style={{ fontSize: 34, margin: "6px 0" }}>{hold.value}</p>
          <p className="quiet">Default {hold.default}. Limits {hold.min} to {hold.max}. A payment scoring this or more is held for the customer. The block-at-once rule and the swap-plus-warning-sign rule do not depend on it.</p>
          {hold.value !== hold.default && data.can_approve && <button className="btn" onClick={reset}>Reset to the default</button>}
        </div>
        <div className="card">
          <h2>What it learned from</h2>
          <p className="quiet">{data.evidence.answered_cases} held payments with a customer answer. It needs {data.min_cases} in the lowest band, and no confirmed fraud in it, before it will propose raising the score.</p>
          {<button className="btn primary" onClick={propose}>Ask the Analytics agent for a proposal</button>}
          <p className="fine">Limit: nobody reports fraud on payments that were released, so the system can only see whether holds were right. It cannot see what it missed.</p>
        </div>
      </div>

      <div className="card">
        <h2>Were the holds right?</h2>
        {data.evidence.bands.length === 0 ? (
          <p className="quiet">No held payment has a customer answer yet.</p>
        ) : (
          <table className="data" style={{ cursor: "default" }}>
            <thead><tr><th>Score band</th><th>Answered holds</th><th>Fraud</th><th>False holds</th><th>Hold was right</th></tr></thead>
            <tbody>
              {data.evidence.bands.map((b) => (
                <tr key={b.from}><td>{b.from} to {b.to}</td><td>{b.cases}</td><td>{b.fraud}</td><td>{b.legitimate}</td><td>{pct(b.hold_was_right)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="fine">Fraud means the customer said it was not them, or said it was them and then failed the security questions.</p>
      </div>

      <div className="card">
        <h2>Proposals</h2>
        {data.proposals.length === 0 && <p className="quiet">None yet.</p>}
        {data.proposals.map((p) => (
          <div key={p.id} style={{ borderTop: "1px solid var(--line)", padding: "12px 0" }}>
            <strong>Hold score {p.current} to {p.proposed}</strong> <span className="quiet">· {p.status}{p.decided_by ? ` by ${p.decided_by}` : ""}</span>
            <p style={{ margin: "6px 0" }}>{p.reason}</p>
            {p.decision_note && <p className="quiet">Note: {p.decision_note}</p>}
            {p.status === "pending" && data.can_approve && (
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <input placeholder="Why (optional)" value={note[p.id] || ""} onChange={(e) => setNote({ ...note, [p.id]: e.target.value })} style={{ flex: 1, minWidth: 200 }} />
                <button className="btn primary" onClick={() => decide(p.id, "approve")}>Approve</button>
                <button className="btn" onClick={() => decide(p.id, "reject")}>Reject</button>
              </div>
            )}
            {p.status === "pending" && !data.can_approve && <p className="quiet">Waiting for a fraud analyst or the super admin.</p>}
          </div>
        ))}
      </div>
    </>
  );
}
