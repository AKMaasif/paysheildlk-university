"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Logo } from "@/components/Icons";
import { api, setToken, setUser } from "@/lib/api";

type Tab = "sign-in" | "request" | "activate";

export default function SignInPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("sign-in");
  const [email, setEmail] = useState("analyst@payshield.lk");
  const [password, setPassword] = useState("pilot-analyst");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [server, setServer] = useState("checking");
  const [form, setForm] = useState({
    name: "",
    email: "",
    organisation: "",
    role_asked: "fraud_analyst",
    scope_asked: "Western province",
    reason: "",
    token: "PS-INVITE-7K2M",
    password: "",
  });

  useEffect(() => {
    api<{ ok: boolean }>("/api/health")
      .then(() => setServer("connected"))
      .catch(() => setServer("unreachable"));
  }, []);

  async function signIn(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const body = await api<{ token: string; refresh_token?: string; user: unknown }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password, portal: "operator" }),
      });
      setToken("operator", body.token, body.refresh_token);
      setUser("operator", body.user);
      router.replace("/overview");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    }
  }

  async function requestAccess(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setInfo("");
    try {
      await api("/api/auth/request-access", { method: "POST", body: JSON.stringify(form) });
      setInfo("Request filed. A super admin decides it from Administration.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    }
  }

  async function activate(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setInfo("");
    try {
      await api("/api/auth/activate", {
        method: "POST",
        body: JSON.stringify({ token: form.token, email: form.email, password: form.password }),
      });
      setInfo("Account activated. Sign in with that email.");
      setTab("sign-in");
      setEmail(form.email);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Activation failed");
    }
  }

  return (
    <div className="signin-page">
      <form className="signin-card" onSubmit={tab === "sign-in" ? signIn : tab === "request" ? requestAccess : activate}>
        <div className="brand">
          <Logo size={40} />
          <div className="brand-copy">
            <h1>PayShield.lk</h1>
            <p>Sri Lanka · Simulated banking network</p>
          </div>
        </div>
        <div className="tabs">
          <button type="button" className={tab === "sign-in" ? "on" : ""} onClick={() => setTab("sign-in")}>Sign in</button>
          <button type="button" className={tab === "request" ? "on" : ""} onClick={() => setTab("request")}>Request access</button>
          <button type="button" className={tab === "activate" ? "on" : ""} onClick={() => setTab("activate")}>Activate account</button>
        </div>
        {tab === "sign-in" && (
          <>
            <label className="field"><span>Username</span><input value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" /></label>
            <label className="field"><span>Password</span><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
          </>
        )}
        {tab === "request" && (
          <>
            <label className="field"><span>Name</span><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
            <label className="field"><span>Email</span><input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required /></label>
            <label className="field"><span>Organisation</span><input value={form.organisation} onChange={(e) => setForm({ ...form, organisation: e.target.value })} required /></label>
            <label className="field">
              <span>Role asked</span>
              <select value={form.role_asked} onChange={(e) => setForm({ ...form, role_asked: e.target.value })}>
                <option value="fraud_analyst">Fraud analyst</option>
                <option value="telecom_operator">Telecom operator</option>
                <option value="bank_supervisor">Bank supervisor</option>
                <option value="auditor">Auditor</option>
              </select>
            </label>
            <label className="field"><span>Scope asked</span><input value={form.scope_asked} onChange={(e) => setForm({ ...form, scope_asked: e.target.value })} /></label>
            <label className="field"><span>Reason</span><textarea rows={3} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} required /></label>
          </>
        )}
        {tab === "activate" && (
          <>
            <label className="field"><span>Invite token</span><input value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} /></label>
            <label className="field"><span>Email</span><input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="priya.fernando@mail.lk" /></label>
            <label className="field"><span>Password</span><input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
          </>
        )}
        {error && <div className="form-error">{error}</div>}
        {info && <div className="quiet" style={{ marginTop: 8 }}>{info}</div>}
        <button className="btn primary" type="submit">{tab === "sign-in" ? "Sign in" : tab === "request" ? "Submit request" : "Activate"}</button>
        <div className="signin-meta">
          <span><i className={`dot ${server === "connected" ? "ok" : "bad"}`} /> Server: {server === "connected" ? "local API" : server}</span>
          <span>This browser · pilot</span>
        </div>
        <p className="fine-print">Access is granted per person and per scope by the super admin. All activity is logged.</p>
      </form>
    </div>
  );
}
