# Runbook

What to do when something goes wrong, written for the person on call. Each entry says what you see, what it means, and what to do.
The service is built to fail safe: when a part breaks, **money stays held and a person gets the case**. Nothing here needs
a rush to stop money leaving; it needs someone to look.

Where things are: the console's Overview, Cases, Holds, Live and Security pages; `GET /api/health`; the backend log (standard
output); the audit log under Administration.

## 1. Quick checks

| Look at | Healthy |
| --- | --- |
| `GET /api/health` | `ok: true`; `database: postgres`; `redis: connected`; `sms` and `email` are real drivers, not `log` |
| Security page, or `POST /api/audit/verify` | audit chain `ok: true` |
| Holds page | held payments are being worked, none older than the escalation time (10 minutes) without an analyst on them |
| Backend log | no repeated `ERROR` lines |

## 2. Situations

### A payment is held and the customer cannot be reached
Seen: a case in "Waiting for customer" or "Escalated".
Means: the customer did not answer; after 3 minutes they were reminded, after 10 minutes the case went to an analyst.
Do: call the customer on the number **on their bank record**, not one from a message. Then either the customer answers in the app,
or a bank supervisor releases or blocks from the Holds page (a reason is required and is audited). If you cannot reach them and
the signs are strong, use the case's manual switch to freeze the account or block the payment.

### The customer says they never got the alert
Check the case timeline for the Alert agent's steps. `sms: log` or `email: log` in health means the channel is not configured.
A channel shown as failed means that gateway is down: the agent already tried the other two. Customers can always sign in and
see the pending question. Reminder wording never contains a link or a code, by design; tell the customer to open the app themselves.

### SMS or email gateway down
Seen: notification steps marked failed in several cases; Alert agent messages "fell back".
Means: the other channels (push, the in-app question) carried the alert. Do: fix the gateway or its credentials. Held payments
stay held. No action is needed on the money.

### The AI model is down or slow
Seen: provider shown as `mock` or `rules` on case steps; notes are plainer.
Means: the deterministic writer is used. **Decisions are never affected**: the rules decide, and the tests prove a model that
fails or returns junk changes nothing. Do: nothing urgent; check the key and quota later.

### An agent crashed
Seen: a case in "Escalated" with an agent step in error and a message that a human analyst has the case.
Means: the runner held the payment (or left an already-decided one as it was) and handed it to a person. Do: read the case
timeline for the error text, decide the payment manually from Holds, then report the error. Look in the backend log for the stack trace.

### A fraud spike in one district
Seen: eight or more held or blocked payments in a district within ten minutes. The district enters **emergency mode**: payments of
Rs. 50,000 or more there are held for the customer's confirmation even when the rules would release them. It can only turn a
release into a hold. An analyst can set or clear it by hand with the emergency switch on the Simulate page (the page still loads in production, with the drill buttons hidden), or `POST /api/emergency`.
Do: look at the cases for a shared payee or a telecom problem. Clear the mode when the cause is understood.

### Many customers locked out or wrong-password alerts
Seen: HTTP 429 on sign-in. Limits: 5 wrong passwords per account per 15 minutes; 30 sign-in attempts per address per 15 minutes
(12 registration attempts per address). Counts are in memory: **restarting the backend clears them**. Before you do that, consider
whether the burst is an attack on one account; if so, leave the limit in place.

### The backend will not start
Read the first error line. Common ones:

| Message | Fix |
| --- | --- |
| `APP_ENV=production but these secrets are unset, default or shorter than 32 characters` | Set `JWT_SECRET`, `ENCRYPTION_KEY`, `HMAC_KEY`, `ANSWER_PEPPER` |
| `... DEMO_ASSIST is on` | Set `DEMO_ASSIST=false` |
| `... ALLOWED_ORIGINS is not set to the real https:// site address` | Set `ALLOWED_ORIGINS=https://your.site` |
| `First start in production: set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD` | Only on an empty database. Set both |
| `BOOTSTRAP_ADMIN_PASSWORD must be at least 14 characters ...` | Choose a longer password |
| A database or migration error | The service refuses to run on a half-upgraded schema. Restore the backup taken before the upgrade, or fix the cause and start again |
| `Postgres checkpointer failed. Falling back to sqlite` (a warning, not a stop) | Postgres was unreachable for checkpoints. Cases still run, but checkpoints are in a local file and are lost if the container is replaced. Fix the connection and restart |

### The database is full or unreachable
Cases cannot be saved. The runner holds money first and records after, so an unreachable database leaves any payment already held
as held. Do: restore the database, then look for cases stuck in "running" on the Cases page and hand each to an analyst with `POST /api/demo/nudge` (`{"case_id": ..., "action": "escalate"}`); that endpoint stays on in production.

### Redis is down
Seen: `redis: memory` in health. Live updates still work inside the one backend process. Do: restore Redis when convenient.
Nothing is lost.

### A face scan keeps failing for one customer
Means: poor light, a face partly hidden, the camera, or a changed appearance. The customer is never locked out: when the scan is
unsure they are asked the security questions, and there are fallbacks for no face on file. Do: ask them to try in good light facing
a window. If the stored face is old, the system refreshes it after a strong match. If the customer is genuine and keeps failing,
confirm identity through the bank, then clear their registration from the bank record and let them register again.

### Suspected misuse by an operator
Open Administration, then the audit log. Every sign-in, override, report download and manual block is recorded with the person's
address, in a hash chain. `POST /api/audit/verify` tells you whether the chain was altered. Suspend the account in Administration.

## 3. What to watch and when to alert

`GET /api/ready` is the health check. `GET /api/ops/metrics` (any signed-in operator; make a read-only `auditor` account for the
collector) returns the numbers below as JSON, or as Prometheus text with `?format=prometheus`. The counters start at zero
when the process restarts; the gauges come from the database. Each request also carries an `X-Request-ID`, and every failed or
slow request is logged with it, so a customer's problem can be matched to a log line.

| Signal | Alert when | Why it matters |
| --- | --- | --- |
| `/api/ready` not 200 | for 1 minute | The service cannot do its job. `timers: stalled` means customers are no longer being reminded or escalated |
| `payshield_oldest_waiting_customer_seconds` | above 900 | A customer was asked and nobody has followed up. The 10-minute escalation should have moved it to an analyst |
| `payshield_escalated_cases` | above 0 for 15 minutes | An analyst has a case and has not closed it |
| `payshield_oldest_held_payment_seconds` | above 3600 | Money has been held for an hour. Decide it |
| `payshield_agent_errors_last_hour` | above 0 | An agent crashed. The money was held; find out why |
| `payshield_fail_safe_total` | rising | The same, counted since the process started |
| `payshield_notification_failures_total{channel}` | rising for `sms` or `email` | A gateway is down. Customers are still reached through the app |
| `payshield_rate_limit_blocks_total{bucket="login_fail"}` | rising fast | Someone is guessing passwords |
| `payshield_rate_limit_blocks_total{bucket="register_ip"}` | rising fast | Someone is probing registration |
| `payshield_audit_chain_ok` | equals 0 | The audit log may have been altered. Treat as tampering |
| `payshield_http_5xx_total` | rising | A server error. Search the log for the request id |
| `payshield_emergency_districts` | above 0 | A district is in emergency mode; make sure it is meant to be |

The audit-chain check is cached for five minutes, because it reads the whole log.

## 4. Routine jobs

| When | Job |
| --- | --- |
| Daily | Look at the Holds page and the Overview; confirm `/api/health` |
| Weekly | Check the dependency audit in CI; review Administration for access requests; review the audit log for unusual overrides |
| Monthly | Test a backup restore into a scratch database; review the Calibration page (the Analytics agent may propose a hold-score change; only an analyst approves it) |
| On staff change | Suspend the leaver's console account the same day |
| Before any upgrade | Take a backup (see `docs/DEPLOYMENT.md`, section 7) |

## 5. Keys and secrets

| Secret | If it leaks | If you must change it |
| --- | --- | --- |
| `JWT_SECRET` | Anyone can make sign-in tokens. Change it at once | Everyone is signed out, nothing else is lost |
| `HMAC_KEY` | Handoffs between agents could be forged | Change it when no case is open: cases in flight fail safe and are held |
| `ENCRYPTION_KEY` | Face templates and authenticator secrets could be read from a stolen database | There is **no rotation tool**: changing it makes stored face templates and authenticator secrets unreadable. Customers fall back to the questions and must re-enrol their face; plan this deliberately |
| `ANSWER_PEPPER` | Only affects old-format security-answer hashes | Old-format answers stop matching; newer answers are unaffected |
| Gateway keys (SMS, email, push, SIM-swap feed) | Use the provider's console to revoke | Update the environment and restart |

## 6. When to escalate beyond the team

* A payment that was **released** and later found to be fraud: preserve the case, its receipt and the audit log; follow the bank's own incident process. The case report is the evidence pack.
* Evidence that a stored face template or the database was read by someone else: treat it as a personal-data incident under the bank's process and the applicable law.
* The audit chain does not verify: treat it as tampering until proven otherwise.
