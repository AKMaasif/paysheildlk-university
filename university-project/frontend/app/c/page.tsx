"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Brand, Ico, SiteNav } from "@/components/CustomerChrome";
import { isSignedIn } from "@/lib/api";

function Tick() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M5 12.5 10 17.5 19 7" />
    </svg>
  );
}

export default function Landing() {
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => setSignedIn(isSignedIn("customer")), []);

  return (
    <>
      <SiteNav signedIn={signedIn} />

      <section className="cs-hero">
        <div className="cs-wrap">
          <div>
            <div className="cs-eyebrow">BankCylone customers</div>
            <h1>
              If someone takes over your SIM, your money <em>waits for you.</em>
            </h1>
            <p className="cs-lead">
              The most common way into a bank account is not the bank. It is your phone number. PayShield.lk watches for a SIM swap followed by a payment to someone new. When it sees that,
              the payment is held and we ask you, on the phone you registered, whether it was you.
            </p>
            <div className="cs-cta">
              {signedIn ? (
                <Link className="cs-btn primary" href="/c/home">Open my account</Link>
              ) : (
                <>
                  <Link className="cs-btn primary" href="/c/register">Register</Link>
                  <Link className="cs-btn ghost" href="/c/login">Log in</Link>
                </>
              )}
            </div>
            <ul className="cs-assure">
              <li><Tick /> We never text you a code</li>
              <li><Tick /> You see every step, with the time</li>
              <li><Tick /> A person can step in at any point</li>
            </ul>
          </div>

          <div className="cs-stage" aria-hidden>
            <div className="cs-phone">
              <div className="cs-screen">
                <div className="cs-screen-bar">PayShield.lk <span style={{ marginLeft: "auto", fontWeight: 400, opacity: 0.7, fontSize: 12 }}>Registered device</span></div>
                <div className="cs-screen-body">
                  <div className="cs-screen-card">
                    <small>Abnormal transaction detected on your account. Please verify.</small>
                    <div className="q">Did you make this payment to K.D. Enterprises?</div>
                    <div className="amt"><span>Rs</span>200,000</div>
                    <small>We will not text you a code. If someone calls and asks for one, hang up.</small>
                    <span className="cs-fake-btn yes">Yes, this was me</span>
                    <span className="cs-fake-btn no">No, this was not me</span>
                  </div>
                  <div className="cs-sms"><b>SMS</b> BankCylone: an unusual transaction on your account was held. Open the app to review. We will never ask you to share a code by text message.</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="cs-section tint" id="what-is-a-sim-swap">
        <div className="cs-wrap">
          <div className="cs-head">
            <h2>What a SIM swap is, and why it matters to your account</h2>
            <p>Banks send one-time codes by SMS. Whoever receives your SMS can approve things as you. A SIM swap is how someone else starts receiving them.</p>
          </div>
          <div className="cs-beats">
            <div className="cs-beat">
              <div className="n">01</div>
              <h3>Your number moves</h3>
              <p>A criminal persuades a mobile operator, or a shop, to move your number to a SIM they hold. Your phone loses signal. You may think it is a network fault.</p>
            </div>
            <div className="cs-beat">
              <div className="n">02</div>
              <h3>Your codes go to them</h3>
              <p>They use "forgot password" and the bank sends the code to your number, which is now theirs. They set a new password and add a new payee.</p>
            </div>
            <div className="cs-beat">
              <div className="n">03</div>
              <h3>The money leaves</h3>
              <p>A large transfer goes out, often within hours. By the time your phone works again, the account is empty.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="cs-section dark" id="how-it-works">
        <div className="cs-wrap">
          <div className="cs-head">
            <h2>What PayShield.lk does, in order</h2>
            <p>Four steps, and the decision about your money is always made by fixed, readable rules. No chatbot decides it.</p>
          </div>
          <ol className="cs-flow cs-flow-ul">
            <li>
              <h3>We notice</h3>
              <p>The mobile network tells us your SIM changed. The bank tells us what is being paid and to whom. We read both at the same time.</p>
            </li>
            <li>
              <h3>We hold</h3>
              <p>If a recent SIM change sits next to an unusual payment, the money is held. It does not leave your account.</p>
            </li>
            <li>
              <h3>We ask you</h3>
              <p>The question appears in the app on the phone you registered. Not by SMS, and never with a link or a code to share.</p>
            </li>
            <li>
              <h3>You decide</h3>
              <p>Say it was not you, or prove it was. If you do not answer, a reminder goes out and then a person at the bank takes over. The money stays held.</p>
            </li>
          </ol>
          <div className="cs-branch">
            <div className="no">
              <h3>If you say "No, this was not me"</h3>
              <p>The payment is blocked, your account is limited, we ask the operator to freeze the SIM, and you get a receipt with every step and its exact time.</p>
            </div>
            <div className="yes">
              <h3>If you say "Yes, this was me"</h3>
              <p>You take a live face scan, then answer questions only you should know. Wrong answers are never revealed. Pass, and the payment goes. Fail, and it is blocked.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="cs-section" id="what-you-get">
        <div className="cs-wrap">
          <div className="cs-head">
            <h2>What you get when you register</h2>
            <p>A view of your BankCylone account, and protection that works on the phone in your hand.</p>
          </div>
          <div className="cs-feats">
            <div className="cs-feat"><div className="ico"><Ico name="wallet" /></div><div><h3>Balance and history</h3><p>Your available balance, anything on hold, and your recent payments with their status.</p></div></div>
            <div className="cs-feat"><div className="ico"><Ico name="bell" /></div><div><h3>Alerts on your own phone</h3><p>The question about an unusual payment appears in the app. The text message only tells you to open it.</p></div></div>
            <div className="cs-feat"><div className="ico"><Ico name="receipt" /></div><div><h3>A receipt for every alert</h3><p>A PDF with a timeline of what happened and when, sent to your email, your phone and the app.</p></div></div>
            <div className="cs-feat"><div className="ico"><Ico name="key" /></div><div><h3>A fresh password when it counts</h3><p>After any alert is settled you are asked to choose a new password, and we confirm the change by email and SMS.</p></div></div>
          </div>
        </div>
      </section>

      <section className="cs-section tint" id="register">
        <div className="cs-wrap cs-ready">
          <div>
            <h2 style={{ fontSize: 34, lineHeight: 1.15, marginBottom: 14 }}>Registering takes a few minutes</h2>
            <p style={{ color: "var(--cs-muted)", fontSize: 17, marginBottom: 26 }}>
              You do not make a new account. BankCylone already holds your details. Registering proves that you are the person on that record, and lets you choose a password.
            </p>
            <div className="cs-cta" style={{ margin: 0 }}>
              <Link className="cs-btn primary" href="/c/register">Start registering</Link>
              <Link className="cs-btn ghost" href="/c/login">I already registered</Link>
            </div>
          </div>
          <ul>
            <li><b>Your name</b><span>exactly as BankCylone has it</span></li>
            <li><b>Email address</b><span>the one on your account</span></li>
            <li><b>NIC number</b><span>old or new format</span></li>
            <li><b>Mobile number</b><span>the SIM that gets your alerts</span></li>
            <li><b>Account number</b><span>from your statement or card letter</span></li>
            <li><b>A camera</b><span>for a short live face scan</span></li>
          </ul>
        </div>
      </section>

      <section className="cs-section" id="questions">
        <div className="cs-wrap">
          <div className="cs-head"><h2>Questions people ask</h2></div>
          <div className="cs-faq">
            <details>
              <summary>Why do you ask for a face scan?</summary>
              <p>When you register, the camera takes two quick frames to check a real person is there, not a photo held up to the screen. A reference frame is stored encrypted against your account and is never shown back or sent to any AI model.</p>
            </details>
            <details>
              <summary>Will you ever ask me for a code or a password?</summary>
              <p>No. Not by SMS, email or phone call. If anyone claiming to be from BankCylone or PayShield.lk asks for one, hang up and call the number printed on your card.</p>
            </details>
            <details>
              <summary>What if I do not answer the question?</summary>
              <p>You get a reminder after 3 minutes. After 10 minutes a person at the bank's fraud desk takes the case. Through all of it the payment stays held.</p>
            </details>
            <details>
              <summary>Can someone with my phone number sign in as me?</summary>
              <p>Not with the number alone. Signing in needs your username and your password. The alert goes to the app on your registered phone, which is not tied to the SIM.</p>
            </details>
            <details>
              <summary>Is this a live bank?</summary>
              <p>No. This is a pilot. The people, numbers and balances are fictional, and it is not connected to a real bank or mobile operator.</p>
            </details>
          </div>
        </div>
      </section>

      <footer className="cs-foot">
        <div className="cs-wrap">
          <div>
            <Brand onDark />
            <p>PayShield.lk protects BankCylone customers from SIM-swap payment fraud. Pilot only: simulated network, fictional customers, not connected to a live bank or operator.</p>
          </div>
          <nav>
            <Link href="/c/login">Log in</Link>
            <Link href="/c/register">Register</Link>
          </nav>
        </div>
      </footer>
    </>
  );
}
