"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Note, PageHeader } from "@/components/ui";
import { useShell } from "@/components/Shell";
import { api } from "@/lib/api";

type Customer = { id: string; full_name: string; email: string; phone: string; bank: string; district: string };
type Scenario = { id: string; title: string; detail: string };
type Assist = {
  full_name: string;
  email: string;
  nic: string;
  phone: string;
  account_number: string;
  bank: string;
  registered: boolean;
  username: string | null;
  answers: { school?: string; city?: string };
  note: string;
};

export default function DemoPage() {
  const { refresh } = useShell();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [customerId, setCustomerId] = useState("cus_nimal");
  const [scenario, setScenario] = useState("sim_swap_transfer");
  const [assist, setAssist] = useState<Assist | null>(null);
  const [message, setMessage] = useState("");
  const [caseId, setCaseId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ customers: Customer[]; scenarios: Scenario[] }>("/api/demo/customers").then((body) => {
      setCustomers(body.customers);
      setScenarios(body.scenarios);
    }).catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!customerId) return;
    let stop = false;
    const load = () => api<Assist>(`/api/demo/assist?customer_id=${customerId}`).then((body) => { if (!stop) setAssist(body); }).catch(() => { if (!stop) setAssist(null); });
    load();
    const timer = setInterval(load, 1000);
    return () => { stop = true; clearInterval(timer); };
  }, [customerId]);

  async function trigger() {
    setError("");
    try {
      const body = await api<{ case_id: string }>("/api/demo/trigger", { method: "POST", body: JSON.stringify({ customer_id: customerId, scenario }) });
      setCaseId(body.case_id);
      setMessage(`Case ${body.case_id} started.`);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Trigger failed");
    }
  }

  async function reset() {
    await api("/api/demo/reset", { method: "POST" });
    setCaseId("");
    setMessage("Demo cases removed. Seeded history is still here.");
    refresh();
  }

  async function nudge(action: "realert" | "escalate") {
    if (!caseId) return;
    try {
      await api("/api/demo/nudge", { method: "POST", body: JSON.stringify({ case_id: caseId, action }) });
      setMessage(action === "realert" ? "Reminder sent." : "Escalated to the desk. The transfer stays held.");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Nudge failed");
    }
  }

  return (
    <>
      <PageHeader crumb="Demo / Controls" title="Demo controls" subtitle="Raise a SIM swap and a payment, then watch the desk." chip="PILOT" />
      <Note>This writes a SIM event and a payment into the mock feeds, then starts the same graph a live feed would start. Reset removes only rows tagged as demo.</Note>
      {error && <Note tone="warn">{error}</Note>}
      {message && <Note>{message} {caseId && <Link href="/live">Open live activity</Link>}</Note>}
      <div className="split">
        <div className="card">
          <h2>Scenario</h2>
          <label className="field">
            <span>Customer</span>
            <select value={customerId} onChange={(event) => setCustomerId(event.target.value)}>
              {customers.map((customer) => (
                <option key={customer.id} value={customer.id}>{customer.full_name} · {customer.bank} · {customer.district}</option>
              ))}
            </select>
          </label>
          <div style={{ marginTop: 10 }}>
            {scenarios.map((item) => (
              <label key={item.id} className={`choice ${scenario === item.id ? "on" : ""}`}>
                <input type="radio" name="scenario" checked={scenario === item.id} onChange={() => setScenario(item.id)} />
                <span><strong>{item.title}</strong><br /><span className="quiet">{item.detail}</span></span>
              </label>
            ))}
          </div>
          <div className="row-actions">
            <button className="btn primary" onClick={trigger}>Trigger case</button>
            <button className="btn" onClick={reset}>Reset demo state</button>
          </div>
          <div className="row-actions">
            <button className="btn" disabled={!caseId} onClick={() => nudge("realert")}>Run the 3-minute re-alert now</button>
            <button className="btn" disabled={!caseId} onClick={() => nudge("escalate")}>Run the 10-minute escalation now</button>
          </div>
          <p className="fine">The timers still run on their own at 3 and 10 minutes. These buttons call the same handlers so a room does not have to wait.</p>
          <p className="fine">Emergency drills (models down, SMS and email failing, a fraud spike, an agent crash, a silent customer, the manual block switch) now run on the <Link href="/simulate">Simulate</Link> page, where each one drives the real agents.</p>
        </div>
        <div className="card">
          <h2>Customer registration details</h2>
          {assist ? (
            <div>
              <p className="fine" style={{ marginTop: 0 }}>Type these into the registration form at /c/register. Each one must match the bank record.</p>
              <table className="data" style={{ cursor: "default" }}>
                <tbody>
                  <tr><td>Name</td><td><strong>{assist.full_name}</strong></td></tr>
                  <tr><td>Email</td><td>{assist.email}</td></tr>
                  <tr><td>NIC</td><td>{assist.nic}</td></tr>
                  <tr><td>Mobile</td><td>{assist.phone}</td></tr>
                  <tr><td>Account number</td><td>{assist.account_number}</td></tr>
                  <tr><td>Bank</td><td>{assist.bank}</td></tr>
                  <tr><td>Status</td><td>{assist.registered ? `Registered. Username: ${assist.username}` : "Not registered yet"}</td></tr>
                </tbody>
              </table>
              <p>School: {assist.answers.school}. City of birth: {assist.answers.city}.</p>
              <p className="fine">{assist.note}</p>
              {assist.registered && (
                <button
                  className="btn"
                  onClick={async () => {
                    await api("/api/demo/unregister", { method: "POST", body: JSON.stringify({ customer_id: customerId }) });
                    setMessage("Back to first-time state. The customer can register again.");
                    setAssist({ ...assist, registered: false, username: null });
                  }}
                >
                  Return to first-time state
                </button>
              )}
            </div>
          ) : <p className="quiet">Assist is off or the customer is missing.</p>}
          <p className="fine">Customer site: open /c on this host, or on a phone that can reach this machine.</p>
        </div>
      </div>
    </>
  );
}
