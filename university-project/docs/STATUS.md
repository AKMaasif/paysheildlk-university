# Status: what is done, what is left, how to run it

Branch `claude/nifty-mendel-serxgl`. Everything below was checked against the code and the test runs described in
`docs/TEST_REPORT.md` section 0.

## What the system does

PayShield.lk watches payments for SIM-swap fraud for a fictional bank (BankCylone). Ten agents (supervisor, telecom,
banking, risk, policy, alert, verification, response, report, analytics) run in a fixed graph. **Fixed rules decide hold,
block or release.** Models only write notes and pick a few extra read-only lookups. A held payment is put to the customer
on the customer site; they confirm with a face scan and security questions, and the rules re-read the signals once more
before any money moves. Failures never release money. See `docs/ARCHITECTURE_MAP.md`.

## Done

| Area | State |
| --- | --- |
| Core workflow, rules, signed handoffs, fail-safe | Done and tested |
| Customer site: register, face scan, questions, receipts, password change | Done |
| **Customer sign-in needs an authenticator code** (preset secret for demo customers, forced set-up for existing ones) | Done; password alone never opens a session |
| **Per-visitor login limits** (trusted-proxy aware, per username and address) | Done |
| **Chosen or generated usernames**, unique | Done |
| **Encrypted personal fields** (email, phone, NIC, account number) and HKDF-derived keys; wrong key stops the start | Done, with a migration |
| **One Argon2id scheme for security answers**, eight questions per demo customer, rounds avoid repeats | Done |
| **Selfie upload limits**, request size limit, no claimed verdicts | Done |
| **Real sign-out**: signing out revokes the tokens in use (a copy stops working at once), "sign out everywhere" ends every earlier session; checked on every request, refresh and the live stream | Done |
| **More data encrypted at rest**: telecom event phone numbers, notification text and report text, on top of the personal fields | Done, with migrations 0003 to 0005 |
| **Key rotation**: `python -m app.rekey` re-encrypts everything under a new `ENCRYPTION_KEY` in one transaction, proves each value reads back, supports a dry run, takes keys from the environment | Done |
| **Model providers**: Meta main, Grok (xAI) fallback, Gemini removed, deterministic text when neither answers | Done; not tested with live keys |
| Oversized chunked uploads get a clean 413 | Done |
| **Sessions in HttpOnly, SameSite=Strict cookies** for browsers (no token in page-readable storage), a site header on cookie-backed writes, logout, refresh from the cookie, live-stream origin check, content security policy | Done (browser mode). Other clients still use bearer tokens |
| Demonstration modes (deterministic, hybrid, external), allow-listed recipients, bounded retries, truthful delivery states | Done |
| Decision snapshots, signal provenance, pre-release re-check, telecom events while a case is open | Done |
| Code split: `api.py` into `app/routes/*`, `graph.py` into `app/agents/nodes/*` and `base.py` | Done, behaviour unchanged (all operations, all tests) |
| Tests: backend 1,791 (SQLite) and 1,787+ (Postgres/Redis), browser 75, frontend 31, 58 of 58 mutants killed; lint, types, Bandit and dependency audits clean | Latest run, `docs/TEST_REPORT.md` section 0 |

## Open, by choice or by limit

| Item | Detail |
| --- | --- |
| Audit log names | Staying in plain text on purpose: the audit log is hash-chained and reviewers need to read it. Case event messages are also plain text |
| Cookies need one site | The live stream connects to the API port directly and relies on the cookie being sent there. The site and the API must share a host name (or sit behind one proxy) |
| Customer self-registration | Does not collect security questions; the bank record carries them, and operator enrolment requires six |
| Delivery receipts | No provider callback is integrated, so "delivered" is never produced |
| Face-verification screenshots | The five in `docs/face-verification` need the real face engine, which the test setup does not run; left as they were. The customer-site screenshots were regenerated from the running site |
| Password change | Does not end other sessions of the same account (sign out everywhere does) |

## Needs you (live checks this environment cannot do)

Real SMS (Text.lk key and your number), real email (SMTP and your address), web push (Firebase), your Meta and Grok keys
(and a check that the Meta address and model names suit your access), an operator gateway URL, and a real camera. `docs/DEMO_GUIDE.md` lists exactly what to set. `docs/Proposal.docx` was not touched.

## Set up and run

```bash
git clone -b claude/nifty-mendel-serxgl https://github.com/AKMaasif/sim-swap-shield.git
cd sim-swap-shield/university-project
cp .env.example .env        # set ENCRYPTION_KEY to 32+ characters (openssl rand -hex 32) before real use
docker compose up --build   # console and customer site at http://localhost:3000
```

Console: `analyst@payshield.lk` / `pilot-analyst`. Customer site: `/c`. **Demo control** shows each demo customer's
registration details, security answers and the authenticator QR code: add it once to an authenticator app, then register,
sign in with the username, password and the app's 6-digit code.

Checks, one at a time (running them together makes timing-sensitive tests flaky):

```bash
cd backend && python -m pytest -q            # about 18 minutes
cd backend && python -m qa.scenario_matrix   # twelve scenario paths against their expectations
cd e2e && npm install && node run.mjs       # browser suite, about 8 minutes
cd frontend && node --test tests/*.test.ts
```

## Where to read more

`docs/ARCHITECTURE_MAP.md` (data flow, who decides what, where the code is), `docs/DEMO_GUIDE.md` (modes, setup, a ten-step
script, what each status means), `docs/SCENARIO_MATRIX.md` (expected against observed), `docs/FIX_PLAN.md` (the 15 review
findings and their order), `docs/TEST_REPORT.md`, `docs/DEPLOYMENT.md`, `docs/RUNBOOK.md`, `docs/THREAT_MODEL.md`.
