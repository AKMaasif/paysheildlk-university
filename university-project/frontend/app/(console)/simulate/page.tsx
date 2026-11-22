"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Note, PageHeader } from "@/components/ui";
import { useShell } from "@/components/Shell";
import { api, getUser } from "@/lib/api";

type Scenario = { id: string; group: string; title: string; expect: string };
type Customer = { id: string; name: string; district: string };
type Listing = {
  drills_enabled: boolean;
  scenarios: Scenario[];
  faults: Record<string, { detail: string; meaning: string }>;
  emergency: { active: Record<string, string>; hold_above_lkr: number };
  customers: Customer[];
  default_customer: string;
};
type Ran = { case_id: string; scenario: string; note: string };

const WRITERS = ["fraud_analyst", "telecom_operator", "bank_supervisor", "super_admin"];
const DISTRICTS = ["Ampara", "Anuradhapura", "Badulla", "Batticaloa", "Colombo", "Galle", "Gampaha", "Hambantota", "Jaffna", "Kalutara", "Kandy", "Kegalle", "Kilinochchi", "Kurunegala", "Mannar", "Matale", "Matara", "Monaragala", "Mullaitivu", "Nuwara Eliya", "Polonnaruwa", "Puttalam", "Ratnapura", "Trincomalee", "Vavuniya"];

export default function SimulatePage() {
  const { refresh, setDetails } = useShell();
  const [data, setData] = useState<Listing | null>(null);
  const [customer, setCustomer] = useState("cus_nimal");
  const [busy, setBusy] = useState("");
  const [ran, setRan] = useState<Ran | null>(null);
  const [error, setError] = useState("");
  const [district, setDistrict] = useState("Colombo");
  const [reason, setReason] = useState("fraud ring reported");
  const role = getUser<{ role: string }>("operator")?.role || "";
  const canWrite = WRITERS.includes(role);

  const load = useCallback(() => {
    api<Listing>("/api/simulate")
      .then((body) => { setData(body); setCustomer((current) => current || body.default_customer); })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => { setDetails(null); load(); }, [setDetails, load]);

  async function run(scenario: Scenario) {
    setBusy(scenario.id);
    setError("");
    try {
      setRan(await api<Ran>("/api/simulate/run", { method: "POST", body: JSON.stringify({ scenario: scenario.id, customer_id: customer }) }));
      load();
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The scenario did not start");
    } finally {
      setBusy("");
    }
  }

  async function clear() {
    setBusy("clear");
    try {
      await api("/api/simulate/clear", { method: "POST" });
      setRan(null);
      load();
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear the drills");
    } finally {
      setBusy("");
    }
  }

  async function switchDistrict(on: boolean) {
    setBusy("district");
    setError("");
    try {
      await api("/api/emergency", { method: "POST", body: JSON.stringify({ district, on, reason }) });
      load();
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The switch did not change");
    } finally {
      setBusy("");
    }
  }

  const faults = Object.entries(data?.faults || {});
  const emergencies = Object.entries(data?.emergency.active || {});
  const groups = Array.from(new Set((data?.scenarios || []).map((item) => item.group)));
  const active = faults.length > 0 || emergencies.length > 0;

  return (
    <>
      <PageHeader crumb="Assure / Simulate" title="Simulate" subtitle="One button per scenario. Each one runs the real agents and rules, and every run is written to the audit log." chip={active ? "DRILL ACTIVE" : "READY"} />
      {!canWrite && data && <Note>Your role can read this page but not run scenarios.</Note>}
      {data && !data.drills_enabled && <Note>Drills are switched off on this system, so there are no scenario buttons. The emergency switch below is a real control and still works.</Note>}
      {error && <Note tone="warn">{error}</Note>}

      {active && (
        <div className="card" style={{ marginBottom: 12, borderColor: "var(--amber-line)", background: "var(--amber-bg)" }}>
          <div className="panel-head">
            <h2>Active now</h2>
            <button className="btn" disabled={!canWrite || busy === "clear"} onClick={clear}>Clear drills</button>
          </div>
          <ul className="brief">
            {faults.map(([name, item]) => <li key={name}><strong>{name}</strong>: {item.meaning}{item.detail && item.detail !== "on" ? ` (${item.detail})` : ""}</li>)}
            {emergencies.map(([name, why]) => <li key={name}><strong>Emergency mode in {name}</strong>: {why}. Payments of Rs. {(data?.emergency.hold_above_lkr || 50000).toLocaleString("en-US")} or more are held.</li>)}
          </ul>
          <p className="fine">Clearing drills switches off simulated failures and removes the placeholder payments a spike drill added. An emergency switch set by an analyst stays until it is switched off below.</p>
        </div>
      )}

      {ran && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="panel-head"><h2>Started {ran.case_id}</h2><span><Link href={`/live?case=${ran.case_id}`}>Watch it live</Link> · <Link href={`/cases/${ran.case_id}`}>Case file</Link></span></div>
          {ran.note && <p>{ran.note}</p>}
        </div>
      )}

      {(!data || data.drills_enabled) && <div className="card" style={{ marginBottom: 12 }}>
        <label className="field" style={{ maxWidth: 360 }}>
          <span>Run as customer</span>
          <select value={customer} onChange={(event) => setCustomer(event.target.value)}>
            {(data?.customers || []).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.district}</option>)}
          </select>
        </label>
      </div>}

      {groups.map((group) => (
        <section key={group} style={{ marginBottom: 12 }}>
          <h2 style={{ fontSize: 14, margin: "0 0 8px" }}>{group}</h2>
          <div className="scenario-grid">
            {data!.scenarios.filter((item) => item.group === group).map((item) => (
              <div key={item.id} className="card scenario">
                <h3>{item.title}</h3>
                <p className="fine" style={{ margin: "4px 0 10px" }}><strong>Expect:</strong> {item.expect}</p>
                <button className="btn primary" disabled={!canWrite || !!busy} onClick={() => run(item)}>{busy === item.id ? "Starting…" : "Run"}</button>
              </div>
            ))}
          </div>
        </section>
      ))}

      <div className="card">
        <h2>Analyst emergency switch</h2>
        <p className="fine" style={{ marginTop: 0 }}>A real control, not a drill. While a district is on, payments of Rs. {(data?.emergency.hold_above_lkr || 50000).toLocaleString("en-US")} or more there are held for the customer's confirmation even when the normal rules would release them. It never releases or lowers a hold. It survives a restart, and each use is audited.</p>
        <div className="row-actions" style={{ alignItems: "end" }}>
          <label className="field"><span>District</span>
            <select value={district} onChange={(event) => setDistrict(event.target.value)}>{DISTRICTS.map((name) => <option key={name}>{name}</option>)}</select>
          </label>
          <label className="field" style={{ minWidth: 240 }}><span>Reason</span><input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
          <button className="btn primary" disabled={!canWrite || busy === "district" || reason.trim().length < 4} onClick={() => switchDistrict(true)}>Switch on</button>
          <button className="btn" disabled={!canWrite || busy === "district"} onClick={() => switchDistrict(false)}>Switch off</button>
        </div>
      </div>
    </>
  );
}
