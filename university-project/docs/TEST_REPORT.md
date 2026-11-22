# Test report

What was tested, what the tests found, what they did not cover. This is the result of running `docs/TEST_PLAN.md`.
Every number below was produced by a run in this repository; the commands to reproduce each are at the end.

## 1. Result in one table

| Exit criterion (from the plan) | Result | Evidence |
| --- | --- | --- |
| 1. Every test passes, twice in a row | **Met** | Backend suite green on consecutive full runs on SQLite and on Postgres with Redis; browser suite 73 of 73 on consecutive runs; frontend 20 of 20 |
| 2. Line coverage 85% overall, 95% on the money-critical modules, branch 75% | **Met** | 93.1% lines, 88.2% branches. `risk` 100, `critic` 100, `charters` 100, `privacy` 100, `ledger` 99.1, `profile` 98.1, `protocol` 98.4 |
| 3. Each agent has at least its planned number of tests | **Met for nine of ten agents** | See section 3. Risk is at about 28 of 31 |
| 4. Mutation spot-check kills at least 90% | **Met** | 38 mutants; the first 28 were 93% killed on the first run, the 2 survivors were dealt with, and all 38 are killed now |
| 5. No high-severity static finding; dependency findings fixed or explained | **Met** | Bandit: no high or medium findings. Python dependencies: none known. Frontend: `npm audit` reports 0 vulnerabilities after the upgrade to Next.js 15.5.29 with `postcss` pinned to 8.5.33 (section 4, item 10) |
| 6. Browser tests pass at desktop and phone widths; no serious accessibility violation on customer pages | **Met** | 73 browser tests; automated axe checks on the six customer and sign-in pages and on all 16 console pages find no serious or critical violation |
| 7. Performance budgets | **Met** | Median decision 0.31 s; 50 concurrent cases in 0.7 s; 200 concurrent reads in 3.7 s; rate limits hold under a burst |
| 8. No endpoint returns a 500 for malformed input | **Met for what was tried** | Every operation was sent wrong types, bad JSON, oversize bodies, odd identifiers and hostile text: no 500 |
| 9. The report says what the suite cannot prove | **Met** | Section 6 |

## 2. What was run

| Level | Tool | Size |
| --- | --- | --- |
| Backend: unit, agent, integration, API | pytest, on SQLite and again on Postgres 16 with Redis 7 | 610 test functions, 1,558 cases (4 skipped: they need real face photos; 2 more skip on Postgres because they hold a SQLite file lock or build an old SQLite database) |
| Permission matrix and contract | pytest, from the app's own OpenAPI schema | about 600 cases, all 66 operations |
| Browser | Playwright (Chromium), desktop, tablet, phone | 73 cases in 5 files |
| Frontend logic | node:test | 20 |
| Static scan | bandit, ruff (security rules), pip-audit, npm audit, secret pattern scan | |
| Load and concurrency | pytest against a real server process | 8 |
| Resilience | pytest | 23 cases |
| Migrations, production start, monitoring, CORS | pytest | 9 + 14 + 23 + 9 test functions |
| Mutation spot-check | `python -m qa.mutation_check` | 38 mutants |
| Evaluation | `python -m evaluation.run` (unchanged, separate document) | see `docs/evaluation/` |

## 3. Tests per agent

The plan set a target of distinct tests per agent (362 in all). The count below is approximate: test files are grouped
by the agent they mostly exercise, and some files serve several agents. Parametrised tests count once. A test counts only
if it pins a behaviour no other test pins; the new ones were each written for a gap found by reading the code, and several
found a defect (section 4).

| Agent | Plan | Distinct tests now (approx.) | Coverage of its own modules (lines / branches) |
| --- | --- | --- | --- |
| Supervisor | 34 | ~44 (including 13 crash and restart drills) | 94.4% / 87.8% |
| Telecom | 29 | ~36 with the investigator, MCP tools and the remote feed | 95.8% / 92.5% |
| Banking | 26 | ~35 with the customer profile | 98.1% / 91.7% |
| Risk | 31 | ~28 (many are parametrised: about 130 cases) | 100% / 100% |
| Policy | 41 | ~46 with emergency mode and calibration | 99.2% / 95.8% |
| Alert | 34 | ~36 | 100% / 100% |
| Verification | 65 | ~82 | 94.5% / 88.6% |
| Response | 32 | ~41 with the manual switch and supervisor overrides | 98.7% / 91.7% |
| Report | 41 | ~46 with the critic | 99.4% / 95.8% |
| Analytics | 29 | ~32 | 99.6% / 97.2% |

Nine of ten agents are at or above plan; Risk is three short. The weakest area by coverage is Verification at 94.5%: the real
face engine's matching code needs real photos, and the four tests that use them are skipped here.

## 4. What the testing found

Real defects, all fixed, each now covered by a test:

| # | Defect | How it was found | Fix |
| --- | --- | --- | --- |
| 1 | An account the fraud desk had limited could still send money: the next payment was judged on the rules alone, although the customer is told transfers stay locked | Writing the Response agent's tests | `apply_policy` turns a would-be release into a hold while the transfer limit is on |
| 2 | A bank supervisor could freeze or block a case belonging to another bank through the manual switch | Per-record access sweep | The switch now answers "not found" for another bank's case |
| 3 | Auditors were refused (403) on the Security and Agents pages they are meant to read | Permission matrix | A read-only operator check for those endpoints |
| 4 | A malformed stored security-answer hash made verification crash instead of failing | Verification helper tests | Invalid hex is treated as a mismatch |
| 5 | The face scan button could be pressed before the camera had a picture, so a quick press sent blank frames and was rejected | Browser test | The button waits until the camera is playing |
| 6 | Typing the wrong current password on the change-password form signed the customer out | Browser test | That path no longer counts a 401 as an ended session |
| 7 | Accessibility: low-contrast text in the step bar, tables and top bar; links told apart only by colour; a 32 px tap target | Axe and tap-size checks | Darker text colour, underlined in-text links, 40 px target |
| 8 | A device identifier was hashed with SHA-1 | Bandit | SHA-256 |
| 9 | The mutation check showed nothing tested the exact edge where a face score becomes a "match" | Mutation spot-check | Tier edge tests added |
| 10 | `next@14.2.35` carried a critical advisory group and its bundled `postcss` a high one | `npm audit` | Next.js 15.5.29 (the maintained backport line, past every fixed range listed) and a `postcss` override. The one breaking change, the async `params` of the proxy route, is fixed. All 57 browser tests and 20 frontend tests pass on the new version |
| 11 | On Postgres, an over-long value (a 6,000 character email) or a NUL byte in any text field gave a 500 error. SQLite accepted both, so every earlier run missed it | Running the whole suite on a real Postgres | A handler turns a database data error into a clean 422; a test covers it on any database |
| 12 | A proposal to change the hold score that had gone stale was meant to be marked "superseded", but the mark was rolled back with the error, so it stayed pending | Policy edge tests, through the real endpoint | The status is saved before the error is raised |
| 13 | A corrupt or tampered stored face record raised an error in the middle of verification instead of falling back to the security questions | Face engine tests | Treated as "no face on file" |
| 14 | A malformed stored legacy password hash raised an error on sign-in (the same pattern as defect 4) | Security helper tests | Treated as a wrong password |
| 15 | A customer added through the console's Add customer page had no map position, and the Overview and Map pages crashed with "Cannot read properties of null" once such a customer existed | Accessibility checks on every console page, run after other tests had added data | Enrolment sets the position from the district; the map endpoint and the map component both skip a customer with no position |
| 16 | Operator console accessibility: low-contrast text on the avatar, KPI captions, one trace chip and one filter button, a scrollable area the keyboard could not reach, and a link marked only by colour | Axe on all 16 console pages | Darker colours, the page area is focusable and labelled, in-text links underlined |
| 17 | A supervisor's release of a held payment marked it released but never took the money from the customer's balance, so the ledger and the balance disagreed | Response tests | The release applies the same debit rule as the automatic release, once |
| 18 | A supervisor's decision closed the case silently: the customer was not told and no report or receipt was filed (the analyst's manual switch did both) | Response tests | A supervisor's release or block now sends the customer a notice and files the report and receipt |
| 19 | A customer's late "yes" after a supervisor had blocked was refused, but was still written into the case's record as "Customer said yes" | Response tests | A late answer is refused before anything is written |
| 20 | The automatic "payment blocked" message did not say that no money left the account (the manual one did) | Response tests | The message now says so |
| 21 | The live stream held a departed browser's connection until the next 20-second ping, so each closed tab leaked a subscription and a shutdown took 20 seconds | Reading why a real-server test would not stop | The connection is released as soon as the client leaves; shutdown takes under a second |
| 22 | The server's access log printed each live-stream sign-in token in plain text | Reading the server log | A filter blanks tokens, passwords and keys in every log line; checked against a real server in both log formats |
| 23 | A production start would have created the demonstration workspace: fictional customers and five operator accounts with published passwords, plus drill endpoints that write pretend events into customer records | Writing the deployment guide | A production start creates one administrator from the environment and nothing else; drills and the simulated payment book are off by default |
| 24 | The API allowed requests from any web site, with credentials on | Planning the hardening work | Only listed origins, no credentials, only GET and POST; production refuses a wildcard, plain http or the development default |
| 25 | `similarity()` raised an overflow error for an absurd distance | Face tests | The exponent is clamped; the score is 0 or 100 at the extremes |

Also worth knowing:

* Two mutation survivors were looked at honestly. One was an equivalent mutant (the charters' "forbidden" list is documentation; the allowed-tools list is what is enforced), so a test now asserts the two lists never overlap and the mutant was changed to edit the allowed list. The other was the real face-tier gap above.
* A suspected database-locking weakness under concurrent writes was tested and **did not exist** (a short lock is waited out). A change written for it was reverted.
* A stall during long browser runs came from the test runner, not the product: it left the server's log pipe unread. The runner now writes logs to files.

## 5. Non-functional results

| Check | Result |
| --- | --- |
| Decision time (mock model, one case at a time) | median 0.31 s, slowest 0.33 s over 12 cases |
| 50 cases started 25 at a time | all finished with the right decision, 0.7 s, no duplicate case numbers |
| 200 reads, 40 at a time, 8 different pages | all complete, 3.7 s |
| Reads and writes mixed, 90 requests | no error; the audit chain still verifies |
| Burst of wrong passwords | stopped by the limit within 6 tries; the right password is refused while locked |
| Burst of registration attempts from one address | stopped by the limit |
| Agent crash at each node (supervisor, telecom, banking, risk, policy, alert, response, report, verification) | the payment is never released; a person gets the case; the customer is told |
| Restart in the middle of a case | the case resumes and can still be answered; reminders and escalation still work |
| Models returning errors or junk | no decision changes |
| Database locked for 1.5 s | the write waits and succeeds |
| Old databases | one made before migrations is brought up to date and keeps its data; one at an older revision is upgraded; the scripts alone produce exactly the schema the models describe, on SQLite and on Postgres |
| Readiness | 503 when the database or the checkpointer is down or the reminder timers have stopped; a missing Redis is degraded, not down |
| Logs | no token or password in any line of a real server's log, in text or JSON |
| Forged, expired, wrong-purpose, wrong-role and unsigned tokens, and the live stream | all refused |
| Customer A against customer B's case, receipt, home page; bank against bank | refused, no data leaks |

All timings were taken on a small cloud sandbox with SQLite, one server process and the mock model. They show the code is
not slow or racy; they are not a capacity claim for a real deployment.

## 6. What this testing cannot prove

* **Face recognition on real people.** The thresholds (80 / 60 / 30) are starting points. The tests prove the tiers, the
  liveness challenge and the fallbacks behave as designed, not the false accept and false reject rates. Those need a few
  hundred labelled photos (`python -m evaluation.face_eval`). The head-turn checks were validated on synthetic landmarks.
* **Spoofing.** There is no learned attack-detection model. A deepfake on a screen or a 3D mask is out of reach of what is built.
* **Live services.** Real SMS, email, push, the operator's SIM-swap feed and a real AI model were not called. The tests use
  stand-ins that fail in the ways real ones do. The browser tests use a fake camera and the simple face engine.
* **Postgres and Redis at scale.** The whole backend suite also passes against PostgreSQL 16 (data and the LangGraph
  checkpointer) and Redis 7 (live events), set with `TEST_DATABASE_URL` and `TEST_REDIS_URL`. That was one machine, one
  server process and small data. Replicas, failover, connection limits and a Postgres under real load are not tested.
* **Dependencies.** Clean today. Advisories are published continually, so the audit has to be re-run (the CI workflow does it on every push and weekly).
* **Style.** `ruff check` now passes on the backend with a committed ruleset (`backend/ruff.toml`: errors, unused code, import order, likely bugs, security rules and hidden characters; line length and a few taste rules are off on purpose). The cleanup removed unused variables and imports, made exceptions chain (`raise ... from`), replaced an import-time `assert` with an explicit error, and wrote hidden direction-control characters in the injection test strings as visible escapes. Compact report scripts in `evaluation/` keep one-line statements. CI runs ruff on every push.
* **Deployment files.** The Docker images, the compose health check and the nginx sample in `docs/DEPLOYMENT.md` were written and
  syntax-checked, but never built or run here (no Docker daemon). Do a trial deployment before relying on them.
* **Manual acceptance.** The ten checks in `TEST_PLAN.md` section 9 need a person (wording, feel, one-handed use, bright
  and dim rooms, glasses and caps). They were **not run**. Checks 11 to 20 were never written.
* **Recall and synthetic data.** The evaluation numbers depend on invented histories and archetypes; they show the
  rules work as designed, not what they would catch in a real bank.

## 7. How to reproduce

From `university-project/backend`:

```bash
python -m pytest -q                                   # the whole backend suite
python -m pytest -q --cov=app --cov-branch --cov-report=json:cov.json
python qa/coverage_by_agent.py cov.json               # coverage per module and per agent
python -m qa.mutation_check                           # 38 mutants, takes about 15 minutes
bandit -q -r app && pip-audit -r requirements.txt     # static scan and dependency audit
```

From `university-project/e2e`: `npm install` once, then `node run.mjs` (see `e2e/README.md`).
From `university-project/frontend`: `npm test`.
