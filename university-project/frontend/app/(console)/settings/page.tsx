"use client";

import { useEffect, useState } from "react";
import { Note, PageHeader } from "@/components/ui";
import { useShell } from "@/components/Shell";
import { api } from "@/lib/api";

type Settings = {
  database: string;
  checkpointer: string;
  redis: string;
  sms: string;
  email: string;
  push: string;
  telco: string;
  demo_assist: boolean;
  jwt_dev_secret: boolean;
  realert_seconds: number;
  escalate_seconds: number;
  step_delay_ms: number;
  timezone: string;
  llm: { primary: string; gemini_configured: boolean; meta_configured: boolean; gemini_model: string; meta_model: string; fallback: string };
};

export default function SettingsPage() {
  const { refreshKey } = useShell();
  const [data, setData] = useState<Settings | null>(null);
  useEffect(() => {
    api<Settings>("/api/settings").then(setData).catch(() => setData(null));
  }, [refreshKey]);
  if (!data) return <p className="quiet">Loading settings…</p>;
  const rows: Array<[string, string]> = [
    ["Telecom feed", data.telco === "camara-http" ? "Operator gateway" : "Mock CAMARA SIM Swap"],
    ["Primary model", data.llm.gemini_configured ? data.llm.gemini_model : "Not configured"],
    ["Fallback model", data.llm.meta_configured ? data.llm.meta_model : "Deterministic mock"],
    ["Engine now", data.llm.primary],
    ["SMS", data.sms === "textlk" ? "Text.lk" : "Log only, no link and no code"],
    ["Email", data.email === "smtp" ? "SMTP" : "Log only"],
    ["Push", data.push === "fcm" ? "Firebase, on the registered device" : "In-app and WebSocket"],
    ["Re-alert", `${Math.round(data.realert_seconds / 60)} min with no answer`],
    ["Escalate", `${Math.round(data.escalate_seconds / 60)} min, transfer stays held`],
    ["Demo assist", data.demo_assist ? "On, codes shown for fictional customers" : "Off"],
  ];
  return (
    <>
      <PageHeader crumb="Assure / Settings" title="Settings" subtitle="What is configured. Keys are not shown." />
      {data.jwt_dev_secret && <Note tone="warn">The API is using the default signing secret. Set JWT_SECRET before any shared pilot.</Note>}
      {data.demo_assist && <Note tone="warn">Demo assist is on. The operator console can show the registration details and scripted answers for the fictional customers. Set DEMO_ASSIST=false before any real person uses this.</Note>}
      <div className="table-wrap">
        <table className="data">
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label}><td style={{ width: 280 }}>{label}</td><td>{value}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
