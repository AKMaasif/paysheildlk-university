"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthSide } from "@/components/AuthSide";
import { AuthenticatorStep, type Session } from "@/components/AuthenticatorStep";
import { api, setToken, setUser } from "@/lib/api";

export default function CustomerLogin() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ step: "totp" | "enrol"; pending_token: string } | null>(null);

  async function signIn(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const body = await api<{ step: "totp" | "enrol"; pending_token: string }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: username, password, portal: "customer" }),
      });
      // The password alone never opens a session. The next screen asks for the authenticator code.
      setPending(body);
      setBusy(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
      setBusy(false);
    }
  }

  function finish(session: Session) {
    setToken("customer", session.token, session.refresh_token);
    setUser("customer", session.user);
    router.replace("/c/home");
  }

  return (
    <div className="cs-auth">
      <AuthSide title="Welcome back." text="Sign in to see your BankCylone balance, your recent payments, and any payment waiting for your answer." />
      <main className="cs-auth-main">
        <div className="cs-auth-top">
          <Link href="/c">&larr; Back to home</Link>
          <span>New here? <Link href="/c/register">Register</Link></span>
        </div>
        <div className="cs-auth-box">
          <h1>Log in</h1>
          <p>{pending ? "One more step to keep your account safe." : "Use the username you chose when you registered."}</p>
          {pending && <AuthenticatorStep mode={pending.step} pendingToken={pending.pending_token} onDone={finish} onBack={() => { setPending(null); setPassword(""); }} />}
          {!pending && <form className="cs-form" onSubmit={signIn}>
            {error && <div className="cs-error" role="alert">{error}</div>}
            <div className="cs-field">
              <label htmlFor="username">Username</label>
              <input id="username" className="cs-input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" placeholder="The username you chose when you registered" required />
            </div>
            <div className="cs-field">
              <label htmlFor="password">Password</label>
              <div className="cs-pw">
                <input id="password" className="cs-input" type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
                <button type="button" onClick={() => setShow(!show)} aria-pressed={show}>{show ? "Hide" : "Show"}</button>
              </div>
            </div>
            <button className="cs-btn primary block" type="submit" disabled={busy}>{busy ? "Signing in" : "Log in"}</button>
          </form>}
          <p className="cs-switch">First time? <Link href="/c/register">Register your account</Link></p>
        </div>
      </main>
    </div>
  );
}
