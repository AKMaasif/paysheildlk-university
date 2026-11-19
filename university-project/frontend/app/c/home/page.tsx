"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AppBar } from "@/components/CustomerChrome";
import { FaceCapture, type Capture } from "@/components/FaceCapture";
import { api, downloadAuthed, getToken, wsUrl } from "@/lib/api";

type Prompt = {
  type?: string;
  hints?: string[];
  face?: { token: string; steps: string[]; instructions: string[] };
  challenge?: string;
  retake?: boolean;
  round?: number;
  notice?: string;
  questions?: { id: string; prompt: string }[];
};
type Home = {
  customer: { full_name: string; bank: string; must_change_password?: boolean };
  active: { id: string; status: string; amount_lkr: number; payee: string; pending_prompt: Prompt | null } | null;
  result: { id: string; status: string; amount_lkr: number; payee: string; sim_freeze_ref: string | null; steps: string[] } | null;
  notifications?: { id: string; title: string; body: string; channel: string }[];
};
type Txn = { id: string; date: string; label: string; kind: string; amount_lkr: number; direction: "debit" | "credit"; status: string; status_code: string };
type Account = {
  bank: string;
  holder: string;
  account_number: string;
  branch: string | null;
  district: string | null;
  phone: string;
  email: string;
  balance_lkr: number;
  held_lkr: number;
  available_lkr: number;
  spent_30d_lkr: number;
  payees: number;
  transactions: Txn[];
  security: { registered_at: string | null; password_changed_at: string | null; face_scan: boolean; trusted_devices: number; must_change_password: boolean; limited: boolean };
};

const money = (n: number) => Number(n || 0).toLocaleString("en-US");
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Colombo" }) : "Not yet");
const shortDay = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "Asia/Colombo" });
const chipClass = (code: string) => (code === "blocked" ? "blocked" : code === "held" ? "hold" : code === "pending" ? "check" : "");

function greeting() {
  const hour = Number(new Date().toLocaleString("en-GB", { hour: "numeric", hour12: false, timeZone: "Asia/Colombo" }));
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

export default function CustomerHome() {
  const router = useRouter();
  const [home, setHome] = useState<Home | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const active = home?.active || null;
  const prompt = active?.pending_prompt || null;

  function load() {
    return Promise.all([
      api<Home>("/api/customer/home", { portal: "customer" }).then(setHome),
      api<Account>("/api/customer/account", { portal: "customer" }).then(setAccount),
    ]).catch((err) => setError(err.message));
  }

  useEffect(() => {
    if (!getToken("customer")) {
      router.replace("/c/login");
      return;
    }
    api("/api/customer/devices", { portal: "customer", method: "POST", body: JSON.stringify({ label: "This phone", platform: navigator.userAgent.includes("iPhone") ? "iPhone" : "Android Chrome" }) }).catch(() => undefined);
    load();
    let socket: WebSocket | null = null;
    let closed = false;
    let retry: number | undefined;
    const connect = () => {
      try {
        socket = new WebSocket(wsUrl("customer"));
      } catch {
        return;
      }
      socket.onmessage = (message) => {
        const body = JSON.parse(message.data);
        if (body.type === "event") {
          load();
          const event = body.event;
          if (event?.agent === "alert" && Notification.permission === "granted" && navigator.serviceWorker?.controller) {
            navigator.serviceWorker.controller.postMessage({ type: "show-notification", title: "PayShield.lk", body: event.message || "A payment needs you in the app." });
          }
        }
      };
      socket.onclose = () => { if (!closed) retry = window.setTimeout(connect, 2000); };
    };
    connect();
    const poll = window.setInterval(load, 4000);
    return () => { closed = true; if (retry) window.clearTimeout(retry); socket?.close(); window.clearInterval(poll); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  // After an alert is settled the customer must choose a new password before anything else.
  useEffect(() => {
    if (home && !home.active && home.customer.must_change_password) router.replace("/c/password?required=1");
  }, [home, router]);

  async function respond(decision: "yes" | "no") {
    if (!active) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/customer/cases/${active.id}/respond`, { portal: "customer", method: "POST", body: JSON.stringify({ decision }) });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the answer");
    } finally {
      setBusy(false);
    }
  }

  async function verify(event: React.FormEvent) {
    event.preventDefault();
    if (!active) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/customer/cases/${active.id}/verify`, { portal: "customer", method: "POST", body: JSON.stringify({ answers, liveness: true }) });
      setAnswers({});
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not check the answers");
    } finally {
      setBusy(false);
    }
  }

  async function submitFace(capture: Capture) {
    if (!active) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/customer/cases/${active.id}/liveness`, { portal: "customer", method: "POST", body: JSON.stringify(capture) });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The face scan could not be sent");
    } finally {
      setBusy(false);
    }
  }

  async function enableAlerts() {
    if ("Notification" in window) await Notification.requestPermission();
  }

  if (!home || !account) {
    return (
      <>
        <AppBar />
        <div className="cs-pad">{error ? <div className="cs-error" style={{ display: "inline-block" }}>{error}</div> : "Opening your account"}</div>
      </>
    );
  }

  const r = home.result;
  const resultHeading = r?.status === "closed_blocked" ? "Next steps" : r?.status === "escalated" ? "A person has this case" : "Nothing further is waiting";
  const resultTag = r && (r.status === "closed_released" || r.status === "closed_cleared") ? "Payment update" : "You are protected";

  return (
    <>
      <AppBar name={account.holder} />
      <main className="cs-main">
        <div className="cs-wrap">
          <div className="cs-hello">
            <div>
              <h1>{greeting()}, {account.holder.split(" ")[0]}</h1>
              <p>{account.bank} · Account {account.account_number}</p>
            </div>
          </div>

          {error && <div className="cs-error" role="alert">{error}</div>}

          {/* A payment waiting for the customer comes first. */}
          {active && prompt?.type === "payment_confirmation" && (
            <section className="cs-panel alert" style={{ marginBottom: 24 }}>
              <span className="tag">Needs you now</span>
              <p className="sub" style={{ color: "var(--cs-muted)" }}>Abnormal transaction detected on your account. Please verify.</p>
              <div className="cs-ask">Did you make this payment to {active.payee}?</div>
              <div className="cs-amount"><span>Rs</span>{money(active.amount_lkr)}</div>
              <p style={{ color: "var(--cs-muted)", fontSize: 15 }}>We will not text you a code. If someone calls and asks for one, hang up. The money stays held until you answer.</p>
              <div className="cs-answer">
                <button className="cs-btn primary" disabled={busy} onClick={() => respond("yes")}>Yes, this was me</button>
                <button className="cs-btn danger" disabled={busy} onClick={() => respond("no")}>No, this was not me</button>
              </div>
            </section>
          )}

          {active && prompt?.type === "liveness" && (
            <section className="cs-panel alert" style={{ marginBottom: 24, maxWidth: 560 }}>
              <span className="tag">Step 1 of 2 · Confirm it is you</span>
              <div className="cs-ask" style={{ fontSize: 22 }}>Scan your face again</div>
              <p style={{ color: "var(--cs-muted)", marginBottom: 14 }}>
                We compare this scan with the one you made when you registered. It must show a live person following the instructions. {prompt.retake ? "Let us try once more." : ""}
              </p>
              <FaceCapture
                key={prompt.face?.token || "legacy"}
                challenge={prompt.face || null}
                busy={busy}
                hints={prompt.hints}
                onCapture={submitFace}
                label={prompt.retake ? "Scan again" : "Start face scan"}
              />
            </section>
          )}

          {active && prompt?.type === "knowledge" && (
            <form className="cs-panel alert" style={{ marginBottom: 24, maxWidth: 640 }} onSubmit={verify}>
              <span className="tag">Step 2 of 2 · Round {prompt.round || 1}</span>
              <div className="cs-ask" style={{ fontSize: 22 }}>Answer these questions</div>
              <p style={{ color: "var(--cs-muted)", marginBottom: 12 }}>{prompt.notice || "Answer this set. We will not say which one was wrong."}</p>
              {(prompt.questions || []).map((question) => (
                <div className="cs-field" key={question.id}>
                  <label htmlFor={question.id}>{question.prompt}</label>
                  <input id={question.id} className="cs-input" value={answers[question.id] || ""} onChange={(event) => setAnswers({ ...answers, [question.id]: event.target.value })} autoComplete="off" />
                </div>
              ))}
              <button className="cs-btn primary" disabled={busy}>Submit answers</button>
            </form>
          )}

          {active && ["running", "new", "escalating"].includes(active.status) && (
            <section className="cs-panel" style={{ marginBottom: 24 }}>
              <div className="cs-ask" style={{ fontSize: 20 }}>The desk is still checking a payment.</div>
              <p style={{ color: "var(--cs-muted)" }}>This page updates on its own.</p>
            </section>
          )}

          {!active && r && (
            <section className={"cs-panel" + (r.status === "closed_blocked" ? "" : " good")} style={{ marginBottom: 24 }}>
              <p className="sub" style={{ marginBottom: 2 }}>{resultTag}</p>
              <div className="cs-ask" style={{ fontSize: 24 }}>{resultHeading}</div>
              <p>Rs {money(r.amount_lkr)} to {r.payee}.</p>
              <ol className="cs-steps">{r.steps.map((step) => <li key={step}>{step}</li>)}</ol>
              <button className="cs-btn primary small" style={{ marginTop: 14 }} onClick={() => downloadAuthed(`/api/customer/cases/${r.id}/receipt`, `PayShield-${r.id}-receipt.pdf`, "customer").catch((err) => setError(err instanceof Error ? err.message : "The receipt is not ready yet"))}>Download your receipt (PDF)</button>
              <p style={{ color: "var(--cs-muted)", fontSize: 14, marginTop: 8 }}>The receipt lists every step and the exact time it happened.</p>
            </section>
          )}

          <div className="cs-layout">
            <div className="cs-stack">
              <section className="cs-balance" aria-label="Balance">
                <div className="row">
                  <div>
                    <div className="bank">{account.bank}</div>
                    <div className="acct">Account {account.account_number}{account.branch ? ` · ${account.branch}` : ""}</div>
                  </div>
                  <div className="acct">LKR</div>
                </div>
                <div className="label">Available balance</div>
                <div className="big"><span>Rs</span>{money(account.available_lkr)}</div>
                <div className="pair">
                  <div><small>Account balance</small><b>Rs {money(account.balance_lkr)}</b></div>
                  <div><small>On hold</small><b>Rs {money(account.held_lkr)}</b></div>
                </div>
              </section>

              <div className="cs-stats">
                <div className="cs-stat"><small>Paid out, last 30 days</small><b>Rs {money(account.spent_30d_lkr)}</b></div>
                <div className="cs-stat"><small>Payees</small><b>{account.payees}</b></div>
                <div className="cs-stat"><small>Trusted phones</small><b>{account.security.trusted_devices}</b></div>
              </div>

              <section className="cs-panel">
                <h2>Recent transactions</h2>
                <p className="sub">The latest payments on your account.</p>
                {account.transactions.length === 0 ? (
                  <div className="cs-empty">No payments yet.</div>
                ) : (
                  <table className="cs-table">
                    <thead>
                      <tr><th>Details</th><th className="hide-sm">Status</th><th className="r">Amount</th></tr>
                    </thead>
                    <tbody>
                      {account.transactions.slice(0, 12).map((tx) => (
                        <tr key={tx.id}>
                          <td className="who"><b>{tx.label}</b><small>{shortDay(tx.date)} · {tx.kind}</small></td>
                          <td className="hide-sm"><span className={"cs-chip " + chipClass(tx.status_code)}>{tx.status}</span></td>
                          <td className={"r money " + tx.direction}>{tx.direction === "credit" ? "+" : "−"} Rs {money(tx.amount_lkr)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
            </div>

            <div className="cs-stack">
              <section className="cs-panel">
                <h2>Your account</h2>
                <dl className="cs-kv" style={{ marginTop: 10 }}>
                  <div><dt>Name</dt><dd>{account.holder}</dd></div>
                  <div><dt>Bank</dt><dd>{account.bank}</dd></div>
                  <div><dt>Account number</dt><dd>{account.account_number}</dd></div>
                  <div><dt>Branch</dt><dd>{account.branch || "Not set"}</dd></div>
                  <div><dt>Mobile</dt><dd>{account.phone}</dd></div>
                  <div><dt>Email</dt><dd>{account.email}</dd></div>
                </dl>
              </section>

              <section className="cs-panel">
                <h2>Protection</h2>
                <dl className="cs-kv" style={{ marginTop: 10 }}>
                  <div><dt>PayShield.lk</dt><dd><span className="cs-chip">{account.security.limited ? "Account limited" : "Watching"}</span></dd></div>
                  <div><dt>Face scan</dt><dd>{account.security.face_scan ? "Stored, encrypted" : "Not set"}</dd></div>
                  <div><dt>Registered</dt><dd>{day(account.security.registered_at)}</dd></div>
                  <div><dt>Password changed</dt><dd>{day(account.security.password_changed_at)}</dd></div>
                </dl>
                <Link className="cs-btn ghost small block" style={{ marginTop: 14 }} href="/c/password">Change password</Link>
              </section>

              <section className="cs-panel">
                <h2>Messages</h2>
                {(home.notifications || []).length === 0 ? (
                  <div className="cs-empty">Nothing yet. Alerts and receipts appear here.</div>
                ) : (
                  <div className="cs-msgs" style={{ marginTop: 8 }}>
                    {home.notifications!.slice(0, 5).map((note) => (
                      <p key={note.id}><b>{note.channel}</b>{note.body}</p>
                    ))}
                  </div>
                )}
                <button className="cs-btn ghost small" style={{ marginTop: 12 }} onClick={enableAlerts}>Allow alerts on this phone</button>
              </section>
            </div>
          </div>
        </div>
      </main>
    </>
  );
}
