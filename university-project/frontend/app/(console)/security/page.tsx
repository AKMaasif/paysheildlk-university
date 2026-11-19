"use client";

import { useEffect, useState } from "react";
import { Note, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";

type Status = {
  chain_ok: boolean;
  chain_checked: number;
  encryption: string;
  passwords: string;
  jwt_access_minutes: number;
  sms: string;
  email: string;
  last_rotation: string;
  emergency: { active: Record<string, string> };
};

export default function SecurityPage() {
  const [data, setData] = useState<Status | null>(null);
  const [note, setNote] = useState("");

  function load() {
    api<Status>("/api/security/status").then(setData).catch((err) => setNote(err.message));
  }
  useEffect(() => { load(); }, []);

  async function verify() {
    const body = await api<{ ok: boolean; checked: number }>("/api/audit/verify", { method: "POST" });
    setNote(body.ok ? `Chain intact. ${body.checked} entries checked.` : "The chain is broken.");
    load();
  }

  const districts = Object.keys(data?.emergency.active || {});

  return (
    <>
      <PageHeader crumb="Assure / Security" title="Security" subtitle="What is switched on for this desk." chip={data?.chain_ok ? "CHAIN OK" : "CHECK"} />
      {note && <Note>{note}</Note>}
      {districts.length > 0 && <Note tone="warn">Emergency mode: {districts.join(", ")}. Transfers at or above Rs. 50,000 in those districts are held.</Note>}
      <div className="split">
        <section className="card">
          <h2>Controls</h2>
          <p>Audit chain: {data?.chain_ok ? "intact" : "needs a check"} ({data?.chain_checked ?? 0} hashed entries).</p>
          <p>PII at rest: {data?.encryption}. Passwords and answers: {data?.passwords}, per-answer salt.</p>
          <p>Access tokens last {data?.jwt_access_minutes} minutes. SMS driver: {data?.sms}. Email driver: {data?.email}.</p>
          <p className="quiet">{data?.last_rotation}</p>
          <button className="btn primary" onClick={verify}>Verify chain</button>
        </section>
        <section className="card">
          <h2>What never leaves the desk</h2>
          <p>Answers, passwords, and authenticator secrets are not sent to a model. Phone numbers in a prompt are masked. A tool outside an agent's charter is refused and written to the chain.</p>
          <p>TLS is expected in front of this service when it is deployed. The pilot on this machine speaks HTTP so a room can run it without a certificate.</p>
        </section>
      </div>
    </>
  );
}
