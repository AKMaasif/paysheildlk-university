"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AppBar, Ico } from "@/components/CustomerChrome";
import { api, getToken } from "@/lib/api";

const RULES: [string, (p: string) => boolean][] = [
  ["At least 8 characters", (p) => p.length >= 8],
  ["A letter and a number", (p) => /[A-Za-z]/.test(p) && /\d/.test(p)],
];

function ChangePassword() {
  const router = useRouter();
  const required = useSearchParams().get("required") === "1";
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [again, setAgain] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ email: string; sms: string } | null>(null);

  useEffect(() => {
    if (!getToken("customer")) router.replace("/c/login");
  }, [router]);

  const rulesOk = RULES.every(([, test]) => test(newPw));
  const same = newPw.length > 0 && newPw === again;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    if (!rulesOk) return setError("Choose a new password that meets both rules below.");
    if (!same) return setError("The two new passwords are not the same.");
    setBusy(true);
    try {
      const body = await api<{ email: string; sms: string }>("/api/customer/password", {
        portal: "customer",
        method: "POST",
        body: JSON.stringify({ old_password: oldPw, new_password: newPw, confirm_password: again }),
      });
      setDone({ email: body.email, sms: body.sms });
    } catch (err) {
      setError(err instanceof Error ? err.message : "The password could not be changed");
    } finally {
      setBusy(false);
    }
  }

  const sent = (status: string) => (status === "failed" || status === "no_address" || status === "no_phone" ? "could not be sent" : "sent");

  return (
    <>
      <AppBar />
      <main className="cs-main">
        <div className="cs-wrap">
          <div className="cs-narrow">
            {done ? (
              <section className="cs-panel good">
                <div style={{ color: "var(--cs-green)", marginBottom: 10 }}><Ico name="check" size={30} /></div>
                <h2 style={{ fontSize: 26 }}>Your password has been changed</h2>
                <p style={{ color: "var(--cs-muted)", margin: "8px 0 16px" }}>Use the new password the next time you log in. We have told you about the change in two places.</p>
                <dl className="cs-kv">
                  <div><dt>Email confirmation</dt><dd>{sent(done.email)}</dd></div>
                  <div><dt>SMS confirmation</dt><dd>{sent(done.sms)}</dd></div>
                </dl>
                <p style={{ color: "var(--cs-muted)", fontSize: 14.5, marginTop: 14 }}>If you did not make this change, call BankCylone on the number printed on your card straight away.</p>
                <Link className="cs-btn primary" style={{ marginTop: 18 }} href="/c/home">Back to my account</Link>
              </section>
            ) : (
              <>
                <div className="cs-hello"><div><h1>Change your password</h1><p>Enter your current password, then choose a new one.</p></div></div>
                {required && (
                  <div className="cs-note" role="status">
                    <b>A payment alert on your account has just been settled.</b> As a precaution, please choose a new password now.
                  </div>
                )}
                <form className="cs-form" onSubmit={submit}>
                  {error && <div className="cs-error" role="alert">{error}</div>}
                  <div className="cs-field">
                    <label htmlFor="o">Current password</label>
                    <input id="o" className="cs-input" type={show ? "text" : "password"} value={oldPw} onChange={(e) => setOldPw(e.target.value)} autoComplete="current-password" required />
                  </div>
                  <div className="cs-field">
                    <label htmlFor="n">New password</label>
                    <div className="cs-pw">
                      <input id="n" className="cs-input" type={show ? "text" : "password"} value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" required />
                      <button type="button" onClick={() => setShow(!show)}>{show ? "Hide" : "Show"}</button>
                    </div>
                    <ul className="cs-rules">
                      {RULES.map(([label, test]) => (
                        <li key={label} className={test(newPw) ? "ok" : ""}><i />{label}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="cs-field">
                    <label htmlFor="r">Re-enter new password</label>
                    <input id="r" className={"cs-input" + (again && !same ? " bad" : "")} type={show ? "text" : "password"} value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" required />
                    {again && !same && <small style={{ color: "var(--cs-red)" }}>The two new passwords are not the same yet.</small>}
                  </div>
                  <div className="cs-actions">
                    {!required && <Link className="cs-btn ghost" href="/c/home">Cancel</Link>}
                    <button className="cs-btn primary" type="submit" disabled={busy}>{busy ? "Changing" : "Change password"}</button>
                  </div>
                </form>
              </>
            )}
          </div>
        </div>
      </main>
    </>
  );
}

export default function ChangePasswordPage() {
  return (
    <Suspense fallback={null}>
      <ChangePassword />
    </Suspense>
  );
}
