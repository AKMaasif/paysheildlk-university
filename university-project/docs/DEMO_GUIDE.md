# Demonstration guide

What can be shown, how to set it up, how to read what the screen says, and what has and has not been verified.

## Three modes

Set `DEMO_MODE` (default `deterministic`). The mode is applied when the backend starts and is shown on `/api/health`.

| Mode | What is real | What is not | Use for |
| --- | --- | --- | --- |
| `deterministic` | The whole workflow: agents, rules, handoffs, snapshots, tool traces, timers, customer site, face check | No SMS, email or push leaves the machine (they are logged or stored in the inbox). No model is called unless keys are set. The "bank" is a synthetic ledger and balances are fake. | Classroom, CI, anything offline |
| `hybrid` | Everything above, plus real SMS (Text.lk) and real email (SMTP), and models and Firebase if configured | The ledger is still synthetic. The telecom feed is still the local database. | A live demo with your own phone and mailbox |
| `external` | Hybrid, plus the SIM-swap fact is read from an operator gateway named by `TELCO_BASE_URL` (CAMARA-style) | Same synthetic ledger. | Showing integration with an operator service |
| `production` | Refuses development secrets and demo assist | | Not a demonstration mode |

The ledger is never described as real bank transfers. In `hybrid` and `external` a real message goes **only** to a recipient on `DEMO_ALLOWED_PHONES` / `DEMO_ALLOWED_EMAILS`. An empty list means nobody. Resetting the demo sends nothing (tested), so fictional seed customers are never messaged. Volume is bounded by `NOTIFY_MAX_PER_HOUR` (default 20) and each message by `NOTIFY_MAX_ATTEMPTS` (default 2).

## Exact setup for a live demo

Everything comes from environment variables (see `.env.example`). Nothing is read from source and nothing is printed.

| For | Set |
| --- | --- |
| Real SMS | `DEMO_MODE=hybrid`, `TEXTLK_API_KEY`, `DEMO_ALLOWED_PHONES=<your number>`. The test customer's registered mobile must be that number. |
| Real email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `DEMO_ALLOWED_EMAILS=<your address>`; the customer's email must be that address |
| Web push | `FIREBASE_*` on the backend and `NEXT_PUBLIC_FIREBASE_*` on the site, and a browser that granted permission |
| Models | `META_API_KEY` (main) and/or `XAI_API_KEY` (Grok, the fallback) |
| Operator gateway | `DEMO_MODE=external`, `TELCO_BASE_URL` |
| Real-time timers | defaults: reminder at 180 s, escalation at 600 s (`REALERT_SECONDS`, `ESCALATE_SECONDS`) |

## How to read a status

| Shown as | Meaning | Do not read it as |
| --- | --- | --- |
| LOGGED ONLY | The driver is off. Nothing was sent. | Sent |
| INBOX ONLY | Stored in the customer's PayShield inbox. No provider was asked. | Delivered to a phone |
| NOT SENT (not allow-listed / hourly limit) | A guard stopped a real message. | A failure of the provider |
| ACCEPTED, delivery UNCONFIRMED | The provider took the request and returned an id. | Delivered. An HTTP or SMTP success is not a delivery receipt. |
| DELIVERED | The provider confirmed delivery. (Needs a delivery-receipt integration; none is configured, so you will not see it today.) | |
| FAILED (reason) | The provider or network refused. Retries are bounded. | |
| SIM freeze request RECORDED (simulated) | A record in PayShield. | That the operator froze the SIM |

Dashboard times are Asia/Colombo. "Pause" on the live view only stops following; the history is already stored. "Clear" clears the view, not the history. The terminal is a log view, not an operating-system shell.

## Demo script (about 12 minutes)

Preparation: add the demo customer's authenticator to a phone app once (Demo control shows a QR code and key; it is a preset secret and stays the same, even after "Return to first-time state"). Sign in as `analyst@payshield.lk`; open **Live Agent Activity** on the projector; open the customer site (`/c`) on a phone for the registered customer.

1. **Routine release.** Trigger "Routine bill of Rs. 4,850". Rules score it low and release it. Show the tool calls, the single debit, and the report.
2. **Suspicious payment rejected.** Trigger "SIM swap and Rs. 200,000". It is held, not debited. On the phone answer **No**. Payment blocked, a simulated freeze request recorded, the account limited.
3. **Successful verification.** Trigger it again. Answer **Yes**, scan your face, answer the questions. Before release the rules re-read the signals; with nothing new, the payment releases once.
4. **Failed verification.** Trigger again. Fail the scan, or give wrong answers in every round. The payment is blocked or held for a person; it is never released.
5. **Silence and escalation.** Trigger and do not answer. Show the wait line: phase, waiting since, reminders sent, time to escalation. Use the buttons to run the reminder and escalation now (this is *accelerated*; say so), or wait the real 3 and 10 minutes. The money stays held.
6. **Provider outage.** In Simulate turn on "SMS down" and "email down", trigger a case: the console shows `FAILED (provider unreachable)` for those channels and `INBOX ONLY` for the app. The decision is unchanged and the customer can still answer in the app.
7. **Agent crash.** Turn on "agent crash" for risk and trigger: the case is held and a person has it. No release.
8. **Emergency mode.** Switch a district to emergency and trigger "Rs. 75,000 payment". The rules alone would release; emergency mode holds it for the customer.
9. **Changing data.** Trigger a case and, while it waits, post a `port_out` for the customer to `POST /api/sim-events`. The case escalates with the reason on the timeline and the money stays held. Post the same event again: reported as a duplicate, nothing changes.

## What was verified

Automated (backend `pytest`, SQLite): the full suite passed on the final code of this phase without a failure; counts are in `docs/TEST_REPORT.md` and were not re-measured on Postgres/Redis or in the browser for these changes (see below). `python -m qa.scenario_matrix` ran twelve scenario paths through the real workflow and every one matched its expectation: `docs/SCENARIO_MATRIX.md`, with redacted evidence in `docs/evidence/scenario_evidence.json`.

Timing: scenarios that depend on waiting (silence, escalation) are shown two ways. *Accelerated*: the clock the sweep judges by is moved (`process_timeouts(now)`); this proves the logic, not the wall-clock wait. *Real time*: one test runs the real background sweep with 1 s and 3 s timers and observes the reminder and escalation happen (`tests/test_qa_timing.py`). Race tests run the customer's answer and the escalation at once and assert exactly one wins and money is never released.

Concurrency: the former global graph lock serialised unrelated cases. Per-case locks replaced it; in a measured probe at the time, ten simultaneous cases took about 5.9 times one case's time before and about 1.5 times after. That probe was a scratch script and is not in the repository; `tests/test_qa_concurrency.py` guards the behaviour (cases overlap, one case never runs twice at once, replay guard is thread-safe) but does not reproduce the numbers.

## Not verified here: what you need to supply

| Check | Blocked because |
| --- | --- |
| Real SMS reaches a phone | No Text.lk key or test number in this environment |
| Real email reaches an inbox | No SMTP credentials |
| Web push reaches a browser | No Firebase project |
| Live Meta / Grok behaviour and fallback between them | No model keys. Fallback logic is tested with simulated failures only. |
| Operator gateway (`external` mode) against a real service | No `TELCO_BASE_URL`. It is tested against a stand-in HTTP function. |
| Delivery receipts ("DELIVERED") | No provider callback is integrated, so that state is never produced today |
| Browser e2e, Postgres/Redis suites on this phase's changes | Not re-run in this session. CI runs them. |
| Camera and real face match on your device | Needs your hardware |

Not claimed: coverage of every failure, an unrestricted autonomous agent, or real bank transfers. Models only write notes and choose among allowed read-only lookups; rules decide.
