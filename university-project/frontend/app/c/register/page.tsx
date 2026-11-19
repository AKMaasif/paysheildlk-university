"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthSide } from "@/components/AuthSide";
import { FaceCapture, type Capture, type Challenge } from "@/components/FaceCapture";
import { api, setToken, setUser } from "@/lib/api";

type Step = 1 | 2 | 3;

const RULES: [string, (p: string) => boolean][] = [
  ["At least 8 characters", (p) => p.length >= 8],
  ["A letter and a number", (p) => /[A-Za-z]/.test(p) && /\d/.test(p)],
];

export default function Register() {
  const router = useRouter();
  const [step, setStep] = useState<Step>(1);
  const [form, setForm] = useState({ full_name: "", email: "", nic: "", phone: "", account_number: "" });
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [challenge, setChallenge] = useState<Challenge>(null);
  const [hints, setHints] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });

  function detailsProblem(): string {
    if (form.full_name.trim().split(/\s+/).length < 2) return "Enter your full name, first and last, as BankCylone has it.";
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email.trim())) return "Enter the email address on your account.";
    if (!/^(\d{9}[VvXx]|\d{12})$/.test(form.nic.replace(/[\s-]/g, ""))) return "A NIC number has 9 digits and a letter (V or X), or 12 digits.";
    if (!/^(\+94|0)?\s?7\d[\s-]?\d{3}[\s-]?\d{4}$/.test(form.phone.trim())) return "Enter a Sri Lankan mobile number, for example 077 123 4567.";
    if (!/^[\d\s-]{6,32}$/.test(form.account_number)) return "Enter your account number using digits only.";
    return "";
  }

  function next(event: React.FormEvent) {
    event.preventDefault();
    const problem = detailsProblem();
    setError(problem);
    if (!problem) setStep(2);
  }

  function toScan(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    if (!rulesOk) return setError("Choose a password that meets both rules below.");
    if (!same) return setError("The two passwords are not the same.");
    setStep(3);
  }

  // A fresh random challenge each time the face scan opens.
  useEffect(() => {
    if (step !== 3) return;
    setChallenge(null);
    api<{ mode: string; token: string; steps: string[]; instructions: string[] }>("/api/customer/register/challenge")
      .then((c) => setChallenge(c.mode === "legacy" ? { token: "", steps: [], instructions: [] } : c))
      .catch((err) => setError(err.message));
  }, [step]);

  const rulesOk = RULES.every(([, test]) => test(password));
  const same = password.length > 0 && password === confirm;

  async function create(capture: Capture) {
    setError("");
    setHints([]);
    setBusy(true);
    try {
      const body = await api<{ token: string; refresh_token?: string; user: unknown }>("/api/customer/register", {
        method: "POST",
        body: JSON.stringify({ ...form, password, confirm_password: confirm, frames: capture.frames, offsets_ms: capture.offsets_ms, challenge: capture.challenge }),
      });
      setToken("customer", body.token, body.refresh_token);
      setUser("customer", body.user);
      router.replace("/c/home");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Registration failed";
      setBusy(false);
      if (message.includes("do not match")) {
        setError(message);
        setStep(1);
      } else if (message.includes("face scan")) {
        setError("The face scan was not accepted.");
        setHints(message.replace(/^The face scan (was not accepted|did not pass)\.?\s*/, "").split(/(?<=\.)\s+/).filter(Boolean));
        api<{ mode: string; token: string; steps: string[]; instructions: string[] }>("/api/customer/register/challenge").then((c) => setChallenge(c.mode === "legacy" ? { token: "", steps: [], instructions: [] } : c)).catch(() => undefined);
      } else {
        setError(message);
      }
    }
  }

  const steps = ["Your details", "Password", "Face scan"];

  return (
    <div className="cs-auth">
      <AuthSide title="Register in three short steps." text="BankCylone already holds your details. Registering proves you are the person on that record and lets you choose a password." />
      <main className="cs-auth-main">
        <div className="cs-auth-top">
          <Link href="/c">&larr; Back to home</Link>
          <span>Already registered? <Link href="/c/login">Log in</Link></span>
        </div>
        <div className="cs-auth-box">
          <h1>Register</h1>
          <p>Use the details exactly as they appear in your bank records.</p>
          <div className="cs-stepper" aria-label="Progress">
            {steps.map((label, i) => (
              <div key={label} className={step === i + 1 ? "on" : step > i + 1 ? "done" : ""}>{i + 1}. {label}</div>
            ))}
          </div>

          {step === 1 && (
            <form className="cs-form" onSubmit={next} noValidate>
              {error && <div className="cs-error" role="alert">{error}</div>}
              <div className="cs-chipline"><b>BankCylone</b> The bank that holds your account</div>
              <div className="cs-grid2">
                <div className="cs-field wide"><label htmlFor="n">Full name</label><input id="n" className="cs-input" value={form.full_name} onChange={set("full_name")} autoComplete="name" placeholder="As it appears on your account" /></div>
                <div className="cs-field wide"><label htmlFor="e">Email</label><input id="e" className="cs-input" value={form.email} onChange={set("email")} autoComplete="email" inputMode="email" /></div>
                <div className="cs-field"><label htmlFor="i">NIC number</label><input id="i" className="cs-input" value={form.nic} onChange={set("nic")} placeholder="198512345678 or 851234567V" /></div>
                <div className="cs-field"><label htmlFor="m">Mobile number</label><input id="m" className="cs-input" value={form.phone} onChange={set("phone")} autoComplete="tel" inputMode="tel" placeholder="077 123 4567" /></div>
                <div className="cs-field wide"><label htmlFor="a">Bank account number</label><input id="a" className="cs-input" value={form.account_number} onChange={set("account_number")} inputMode="numeric" /></div>
              </div>
              <div className="cs-actions"><button className="cs-btn primary" type="submit">Continue</button></div>
            </form>
          )}

          {step === 2 && (
            <form className="cs-form" onSubmit={toScan}>
              {error && <div className="cs-error" role="alert">{error}</div>}
              <div className="cs-field">
                <label htmlFor="p">Password</label>
                <div className="cs-pw">
                  <input id="p" className="cs-input" type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
                  <button type="button" onClick={() => setShow(!show)}>{show ? "Hide" : "Show"}</button>
                </div>
                <ul className="cs-rules">
                  {RULES.map(([label, test]) => (
                    <li key={label} className={test(password) ? "ok" : ""}><i />{label}</li>
                  ))}
                </ul>
              </div>
              <div className="cs-field">
                <label htmlFor="c">Re-enter password</label>
                <input id="c" className={"cs-input" + (confirm && !same ? " bad" : "")} type={show ? "text" : "password"} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
                {confirm && !same && <small style={{ color: "var(--cs-red)" }}>The two passwords are not the same yet.</small>}
              </div>
              <p style={{ color: "var(--cs-muted)", fontSize: 14, margin: "4px 0 0" }}>
                After this you log in with your username, which is your full name as you entered it, and this password.
              </p>
              <div className="cs-actions">
                <button className="cs-btn ghost" type="button" onClick={() => setStep(1)}>Back</button>
                <button className="cs-btn primary" type="submit">Continue</button>
              </div>
            </form>
          )}

          {step === 3 && (
            <div className="cs-form">
              {error && <div className="cs-error" role="alert">{error}</div>}
              <p style={{ color: "var(--cs-muted)", fontSize: 14.5, margin: "0 0 14px" }}>
                A short live scan. We check that a real person is here, then keep your face, encrypted, so we can check it is you later. It is never shown back and never sent to an AI model. Good light on your face helps.
              </p>
              {challenge ? (
                <FaceCapture challenge={challenge} busy={busy} hints={hints} onCapture={create} label="Start face scan" />
              ) : (
                <p className="cs-empty">Getting your instructions</p>
              )}
              <div className="cs-actions">
                <button className="cs-btn ghost" type="button" onClick={() => setStep(2)} disabled={busy}>Back</button>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
