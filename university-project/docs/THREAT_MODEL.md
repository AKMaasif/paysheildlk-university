# Threat model

Who might attack PayShield.lk, what they would try, which control stops them, what test shows the control works, and what is
still open. It is written for a reviewer who wants to know where the real risk is, so the open items come first.

Scope: the backend, the customer site, the operator console and their data. Out of scope: the mobile operator's and the bank's own
systems, the customer's phone, the hosting platform.

## 1. The biggest open risks, in order

| # | Risk | Why it matters | What would close it |
| --- | --- | --- | --- |
| 1 | **Registration can be claimed by someone who knows five facts** (full name, email, NIC, mobile number, account number) | The first person to register binds *their* face and password to the customer's account. Those five facts are known to relatives, colleagues, anyone who has seen a payment slip, and appear in leaks. After that, the real customer is the one who is blocked | An activation code the bank delivers outside the app (posted to the address on record, or handed over at a branch), required at registration. A code sent to the phone is weaker: a SIM swap fraudster receives it |
| 2 | **Face check cannot tell a live person from a good fake** | A deepfake shown to the camera, or a 3D mask, is not detected. The random challenge, quality checks and a requirement for a fresh live capture stop a photo, a replayed video and a still image, nothing stronger | A trained presentation-attack detector, and a risk-based rule that a face match alone never releases a large payment |
| 3 | **A single supervisor can release a held payment** | One compromised or dishonest supervisor releases money. The action is audited, scoped to their bank and needs a typed reason, but nothing stops it first | A second approval above a payment size |
| 4 | **Rate limits and one-use face challenges live in one process's memory** | Run two backend processes and an attacker alternates between them. A restart clears every limit | Move the counters to Redis (see `docs/DEPLOYMENT.md`, section 3). Until then run exactly one process |
| 5 | **No real SIM-swap data unless an operator feed is configured** | With `TELCO_BASE_URL` empty the rules read a simulation. The service warns at start in production, but it would run | Connect a real CAMARA gateway; consider refusing to start in production without one |
| 6 | **Personal data is stored readable** (NIC, phone, account number, email) | A stolen database exposes them. Face templates, authenticator secrets and passwords are protected (below) | Column-level encryption, or database-level encryption plus tight access |
| 7 | **No key rotation tool** for `ENCRYPTION_KEY` | A leaked key cannot be replaced without making stored face templates unreadable | A re-encryption command |
| 8 | **Tokens are kept in the browser's local storage** | Script injected into the site could read them. The site escapes everything it shows, and tests feed hostile text through every field, but there is no second line of defence | A strict Content-Security-Policy at the proxy (the deployment guide asks for one); shorter tokens |
| 9 | **The audit log is not anchored outside the database** | The hash chain shows accidental or crude changes. Someone with write access to the whole table could rebuild it | Periodically publish the latest hash somewhere the database administrator cannot write |
| 10 | **No overall request-size or request-rate limit in the application** | Only sign-in, registration, face scans, codes and answers are limited. Other endpoints rely on the proxy | Limits at the proxy (the guide sets a body size) and per-user limits on expensive endpoints such as report PDFs |

Items 1 and 2 are the ones to settle before real customers use it.

## 2. What is being protected

| Asset | Where | How it is protected |
| --- | --- | --- |
| Customers' money (payments held or released) | the ledger | Only the rule layer, the customer's own confirmed answers and a supervisor's override can move it. The AI models cannot |
| Face templates | `users.face_ref` | AES-256-GCM with `ENCRYPTION_KEY`; never returned to a client, never sent to a model |
| Passwords | `users.password_hash` | Argon2id; older PBKDF2 hashes still verify and are upgraded |
| Security answers | per-answer salted PBKDF2 | Compared without saying which answer failed |
| Identity data (name, NIC, phone, email, account) | `users` | Readable in the database (risk 6). Masked in the customer's own screens and in logs; scoped by role and bank in the console |
| Sign-in tokens | browser | Signed, 30 minutes for access; purposes are checked |
| The audit log | `audit_log` | Hash chain, verified on demand and watched by a metric (risk 9) |
| The rules and thresholds | code, `policy_settings` | Only one number can change (the hold score), within fixed limits, and only when an analyst approves |

## 3. Who might attack, and from where

| Actor | Starts with | Wants |
| --- | --- | --- |
| **SIM-swap fraudster** | The victim's phone number, some personal details | A payment out of the victim's account |
| **Registration hijacker** | The five registration facts | The victim's account identity |
| **Internet attacker** | Nothing | An account, or to disturb the service |
| **Another customer** | A valid customer account | Someone else's data or case |
| **Hostile text** | A payee name, a reference, any text field | To trick an AI model or the page into acting |
| **Operator in one bank** | A valid console account | Another bank's data, or an unlogged action |
| **Insider with database access** | The database | Customer data, or to hide an action |
| **Supply chain** | A compromised dependency | Code running in the service |

## 4. Threats and controls

Rating is the risk that remains after the control, for this design: **Low**, **Medium**, **High**.

### Money and customer decisions

| ID | Threat | Controls | Evidence | Remaining |
| --- | --- | --- | --- | --- |
| M1 | SIM-swap fraudster approves their own payment using the victim's number | The alert is answered in the app on a registered device, never by SMS. No SMS or email holds a link or a code. A "yes" must still pass a live face scan and security questions. Wrong answers never release | `test_qa_alert.py` (SMS safety), `test_graph.py`, 01 and 04 browser specs | Medium: face fakes (risk 2) |
| M2 | A model is tricked into releasing, holding or blocking | Models only write notes. The decision comes from fixed rules; each agent can call only the tools its charter lists; a note that states a decision, a number not in the facts, a link or an instruction is replaced by the rules text | `test_injection.py` (38 cases), `test_critic.py`, `test_qa_resilience.py` (model errors and junk change nothing), mutation M13–M14 | Low |
| M3 | A forged or replayed message between agents | Every handoff is signed with an HMAC, carries a one-use number and a lifetime, and must follow the fixed route table | `test_qa_supervisor.py`, mutation M13 | Low |
| M4 | A crash or failure leaves money free to move | A failed agent turns a pending payment into held and gives the case to a person; a payment already decided stays so | `test_qa_resilience.py` (a crash at each node), mutation M25 | Low |
| M5 | A held payment on a limited account is released by the next payment | A transfer limit turns a would-be release into a hold | `test_qa_response.py`, mutation M20 | Low |
| M6 | Someone keeps guessing the security answers or face scans | Rounds are capped; liveness and verification attempts per case are limited; the answer never says which part failed; the challenge is random, bound to the case, short-lived and single use | `test_face.py`, `test_qa_load.py`, mutation M24 | Medium: the single-use list and the limits are in memory (risk 4) |
| M7 | Registration is claimed by the wrong person | All five details must match, with failed matches limited per account number and attempts per address | `test_customer_site.py`, `test_qa_load.py` | **High**: risk 1 |

### Access to data and functions

| ID | Threat | Controls | Evidence | Remaining |
| --- | --- | --- | --- | --- |
| A1 | Password guessing, credential stuffing | 5 wrong passwords per account per 15 minutes; 30 attempts per address; Argon2id; a lock applies even to the right password | `test_security.py`, `test_qa_load.py`, mutation M16 | Medium: many addresses; in-memory counts |
| A2 | Forged, expired or wrong-purpose tokens | Signature, issuer, expiry and purpose are checked; the role inside a token is ignored (it is read from the database); a refresh token cannot be used as an access token | `test_qa_access.py` | Low |
| A3 | One customer reads or acts on another's case | Every customer endpoint checks the case belongs to the caller; the live stream filters events to the caller | `test_qa_access.py`, browser spec 01 | Low |
| A4 | An operator in one bank reaches another bank | Scoped to the supervisor's bank in lists, case pages, reports, overrides and the manual switch | `test_qa_access.py`, mutations M18–M19 | Low |
| A5 | A role does something it should not | Every one of the 66 operations has a declared policy and is tested with no token, each wrong role and each allowed role | `test_api_matrix.py`, `test_qa_contract.py` | Low |
| A6 | A cross-site request uses a customer's session | Sign-in is a bearer token in a header, never a cookie. Only listed origins may call from a browser; no credentials, only GET and POST | `test_qa_cors.py`, mutations M29–M30 | Low |
| A7 | Script injected through a text field | The site renders text, not HTML; hostile strings go through every field in the API tests and the browser tests raise no script errors | `test_api_matrix.py`, browser specs | Medium: tokens in local storage (risk 8); no CSP from the app |
| A8 | The demo workspace or drills run in production | A production start refuses development secrets, demo helpers and a missing `ALLOWED_ORIGINS`; it creates one administrator and no demonstration accounts; drills and the simulated book are off | `test_qa_production.py`, mutations M31–M33 | Low |

### Data, logs and the service

| ID | Threat | Controls | Evidence | Remaining |
| --- | --- | --- | --- | --- |
| D1 | Stolen database | Face templates and authenticator secrets are AES-GCM encrypted; passwords Argon2id; answers salted | `test_qa_platform.py` (encryption), `test_security.py` | Medium: identity data readable (risk 6); key not rotatable (risk 7) |
| D2 | Secrets in logs | Every log line passes a filter that blanks tokens, passwords and keys in query strings and headers, including the server's access log, checked against a real server | `test_qa_monitoring.py`, mutation M34 | Low |
| D3 | The audit log is edited | Hash chain; verified on demand; the check is a metric that alerts | `test_qa_response.py`, `test_qa_monitoring.py` | Medium: risk 9 |
| D4 | Malformed or hostile requests crash the service | Wrong types, bad JSON, oversize bodies, odd ids and hostile text across every operation; a database "bad value" becomes a 422 | `test_qa_contract.py` and the matrix, run on SQLite and Postgres | Low |
| D5 | Overload | Measured: 50 cases at once and 200 simultaneous reads without failure; sign-in and registration limits hold in a burst | `test_qa_load.py` | Medium: risk 10 |
| D6 | The reminder timer dies unnoticed | The readiness check fails when the sweep stops; a metric shows its age | `test_qa_monitoring.py`, mutation M35 | Low |
| D7 | A dependency is compromised or has a known flaw | Versions pinned; the dependency audit and the static scan run on every push and weekly | CI workflow | Medium: transitive and build-time risk is not eliminated |
| D8 | An operator abuses access | Every sign-in, override, download and manual block is audited with the person's address; manual switch and overrides need a reason | `test_qa_response.py`, `test_qa_access.py` | Medium: risk 3 |

## 5. Assumptions this model depends on

* The service runs behind HTTPS with the headers listed in the deployment guide.
* Exactly one backend process runs (risk 4).
* The operator's SIM-swap feed is truthful and available, or its absence is understood (risk 5).
* The bank's own record of customers is correct; registration trusts it.
* The four secrets are random, long, kept out of the repository and out of the database backups.

## 6. Keeping this current

When a feature adds a way in (an endpoint, a role, a field that accepts text, a new data store) add a row with its control and the
test that proves it. A control without a test is listed as a risk until one exists. The mutation check (`python -m qa.mutation_check`)
should gain a mutant for each new control, so a regression in it is caught, not just assumed.
