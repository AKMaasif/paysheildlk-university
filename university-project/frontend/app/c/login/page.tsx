"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthSide } from "@/components/AuthSide";
import { api, setToken, setUser } from "@/lib/api";

export default function CustomerLogin() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function signIn(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const body = await api<{ token: string; refresh_token?: string; user: unknown }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: username, password, portal: "customer" }),
      });
      setToken("customer", body.token, body.refresh_token);
      setUser("customer", body.user);
      router.replace("/c/home");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
      setBusy(false);
    }
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
          <p>Use the username you chose when you registered.</p>
          <form className="cs-form" onSubmit={signIn}>
            {error && <div className="cs-error" role="alert">{error}</div>}
            <div className="cs-field">
              <label htmlFor="username">Username</label>
              <input id="username" className="cs-input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" placeholder="Your full name, as you registered it" required />
            </div>
            <div className="cs-field">
              <label htmlFor="password">Password</label>
              <div className="cs-pw">
                <input id="password" className="cs-input" type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
                <button type="button" onClick={() => setShow(!show)} aria-pressed={show}>{show ? "Hide" : "Show"}</button>
              </div>
            </div>
            <button className="cs-btn primary block" type="submit" disabled={busy}>{busy ? "Signing in" : "Log in"}</button>
          </form>
          <p className="cs-switch">First time? <Link href="/c/register">Register your account</Link></p>
        </div>
      </main>
    </div>
  );
}
