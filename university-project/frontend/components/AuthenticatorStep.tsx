"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

// The session itself arrives as HttpOnly cookies; the body carries only the person's public details.
export type Session = { user: unknown };

// The second half of a customer sign-in. After the password the site holds only a short-lived pending token; a session
// is issued only when the 6-digit code from the customer's authenticator app is right. A customer with no authenticator
// yet (just registered, or an account from before codes were required) adds the account to their app first.
export function AuthenticatorStep({
  mode,
  pendingToken,
  onDone,
  onBack,
}: {
  mode: "totp" | "enrol";
  pendingToken: string;
  onDone: (session: Session) => void;
  onBack: () => void;
}) {
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<{ secret: string; qr_png_base64: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const auth = { Authorization: `Bearer ${pendingToken}` };

  useEffect(() => {
    if (mode !== "enrol") return;
    api<{ secret: string; qr_png_base64: string }>("/api/customer/totp/setup", { method: "POST", headers: auth })
      .then(setSetup)
      .catch((err) => setError(err instanceof Error ? err.message : "Could not start the authenticator set-up"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, pendingToken]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const session =
        mode === "enrol"
          ? await api<Session>("/api/customer/totp/confirm", { method: "POST", headers: auth, body: JSON.stringify({ code }) })
          : await api<Session>("/api/auth/totp", { method: "POST", body: JSON.stringify({ pending_token: pendingToken, code }) });
      onDone(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That code was not accepted");
      setCode("");
      setBusy(false);
    }
  }

  return (
    <form className="cs-form" onSubmit={submit} noValidate>
      {error && <div className="cs-error" role="alert">{error}</div>}
      {mode === "enrol" ? (
        <>
          <h2 className="cs-step-title">Set up your authenticator</h2>
          <p>Open an authenticator app (Google Authenticator, Microsoft Authenticator or similar), add a new account, and scan this code. Then type the 6-digit number the app shows.</p>
          {setup ? (
            <div className="cs-qr">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`data:image/png;base64,${setup.qr_png_base64}`} alt="QR code for your authenticator app" width={180} height={180} />
              <p className="fine">Cannot scan? Enter this key in the app instead: <code>{setup.secret}</code></p>
            </div>
          ) : (
            !error && <p className="fine">Preparing your code</p>
          )}
        </>
      ) : (
        <>
          <h2 className="cs-step-title">Enter your authenticator code</h2>
          <p>Open your authenticator app and type the current 6-digit code for PayShield. It changes every 30 seconds.</p>
        </>
      )}
      <div className="cs-field">
        <label htmlFor="code">6-digit code</label>
        <input
          id="code"
          className="cs-input cs-code"
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="123456"
          autoFocus
          required
        />
      </div>
      <div className="cs-actions">
        <button className="cs-btn" type="button" onClick={onBack}>Back</button>
        <button className="cs-btn primary" type="submit" disabled={busy || code.length !== 6 || (mode === "enrol" && !setup)}>
          {busy ? "Checking" : mode === "enrol" ? "Confirm and continue" : "Log in"}
        </button>
      </div>
    </form>
  );
}
