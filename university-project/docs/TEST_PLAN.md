# Test plan

> **Status:** executed. Results, defects found and what is not covered are in `docs/TEST_REPORT.md`. The numbers in this plan are the
> targets that were set before the work started; the report says where they were and were not reached.

How many tests this system needs, where, and how we will know they are enough. The plan is written first, then
executed stage by stage. Stage results go in `docs/TEST_REPORT.md`.

## 1. Principles

1. **Risk first.** Money moves only through the rule table, the signed handoffs and the customer's own answers. Those get the most tests, and the hardest ones.
2. **A count is a target, not a result.** A test counts only if it protects a distinct behaviour and would fail if that behaviour broke. Padding with copies does not count. Parametrised cases count once per distinct behaviour, and the report says how many cases ran.
3. **Evidence beyond the count.** Every stage ends with a measurement: coverage, a mutation spot-check, a scan, a timing. A suite that passes but cannot catch a broken rule is worth nothing, so we break rules on purpose and check the suite notices.
4. **Say what is not covered.** The report lists the gaps. Face recognition accuracy on real customers, a live AI model, real SMS and email, and a real bank or operator are outside what automated tests here can prove.

## 2. Test levels

| Level | What it checks | Tool |
| --- | --- | --- |
| L1 Unit | One function or rule, with exact inputs and outputs | pytest |
| L2 Agent | One agent's behaviour through its tools, charter and handoffs, with a stand-in for what it calls | pytest |
| L3 Integration | The whole graph with a real database: cases, ledger, events, reports | pytest |
| L4 API contract and roles | Every endpoint: shape, status codes, who may call it | pytest, generated from the OpenAPI schema |
| L5 End to end | A person's journey in a real browser, desktop and phone | Playwright |
| L6 Non-functional | Security, performance, resilience, accessibility, data integrity | scans, load script, axe |
| L7 Evaluation | Rule quality on synthetic scenarios, face matching on labelled photos | `evaluation/` |
| L8 Manual | Exploratory and acceptance checks a script cannot judge | checklist in this file |

## 3. Tests per agent

Categories: **H** happy path, **B** boundary, **N** negative (refused, bad input, charter), **F** failure (a part is down), **S** security and privacy, **C** contract (handoffs, events, shapes), **P** performance budget.
Modules show where each agent's code lives, so coverage can be read per agent.

| # | Agent | Code | H | B | N | F | S | C | P | **Target** |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Supervisor (with runner and protocol) | `agents/graph.py` (supervisor), `agents/runner.py`, `protocol.py` | 4 | 3 | 6 | 6 | 8 | 5 | 2 | **34** |
| 2 | Telecom (with investigator) | `facts.py` (SIM), `agents/tools.py` (telecom reads), `mcp_tools.py`, `investigator.py` | 5 | 6 | 5 | 4 | 3 | 4 | 2 | **29** |
| 3 | Banking (with profile) | `facts.py` (ledger), `profile.py`, `investigator.py` | 5 | 6 | 4 | 3 | 3 | 3 | 2 | **26** |
| 4 | Risk | `risk.py` (score), `facts.py` (signals) | 6 | 16 | 3 | 1 | 2 | 2 | 1 | **31** |
| 5 | Policy (with emergency and thresholds) | `risk.py` (decide), `emergency.py`, `calibration.py` | 8 | 20 | 4 | 3 | 3 | 2 | 1 | **41** |
| 6 | Alert | `notify.py`, `agents/tools.py` (alert), graph alert | 6 | 4 | 5 | 9 | 4 | 3 | 3 | **34** |
| 7 | Verification (face, questions) | `face.py`, `liveness.py`, `knowledge.py`, `answers.py`, graph verification | 12 | 14 | 12 | 8 | 10 | 6 | 3 | **65** |
| 8 | Response (with manual switch) | `ledger.py`, `manual.py`, graph response | 8 | 4 | 8 | 3 | 3 | 4 | 2 | **32** |
| 9 | Report (with critic) | `reports.py`, `report_pdf.py`, `critic.py` | 10 | 6 | 8 | 3 | 5 | 6 | 3 | **41** |
| 10 | Analytics (with calibration proposals) | `analytics.py`, `simbook.py`, `geo.py`, `calibration.py` | 8 | 6 | 4 | 2 | 3 | 4 | 2 | **29** |
| | **Agents total** | | 72 | 85 | 59 | 42 | 44 | 39 | 21 | **362** |

What the categories mean for each agent:

- **Supervisor.** F: an agent crash holds the money and assigns an analyst; the 3 and 10 minute timers; a restart resumes. S: forged, altered, replayed, stale, misaddressed handoffs. C: every route in the table, event fields.
- **Telecom.** B: swap at 239, 240 and 241 hours. F: the operator feed times out, returns junk, or is down. C: the CAMARA request and response shape. Investigator: extra lookups capped at three, a model asking for a money tool is refused.
- **Banking.** B: payee established after exactly 14 days and two payments, history of 2, 3, 4 and 5 payments. The profile never raises or lowers outside its guards.
- **Risk.** B: each factor at each edge, the cap at 100, the floor at 0. A property check that adding a warning sign never lowers the score.
- **Policy.** B: the 72 hour and Rs. 50,000 edges, score 44 and 45, the 6 hour reset, Rs. 499,999 and 500,000. The decision is identical with both models down. The hold score only moves with an analyst's approval.
- **Alert.** F: SMS down, email down, both down, push down. S: no link and no code in any SMS, attacker text never in an SMS. Timing: reminder at 3 minutes, escalation at 10.
- **Verification.** Face: each score tier, each quality failure, each liveness failure, the challenge (signed, bound, expiring, single use), template refresh rules. Questions: four rounds, never says which answer failed. Fallbacks: unsure, poor quality, no face on file, engine missing.
- **Response.** A blocked payment cannot be released. Locking limits transfers, new payees, password resets and port-out. The manual switch is audited and refuses a released payment.
- **Report.** The PDF is byte-identical on rebuild, its fingerprint matches, every milestone appears. The critic flags an invented number, a link, an instruction, a decision, a contradiction, a secret, an identifier.
- **Analytics.** Counts exactly equal an independent count. A reworded brief with an invented number is dropped. The agent never sees a name, phone or account. Proposals follow the evidence rules.

## 4. Tests for everything that is not an agent

| Area | What is tested | Target |
| --- | --- | --- |
| Customer site | Registration (every field must match, duplicates, weak passwords, face scan), login by username, account summary, balance, password change, forced change after an alert, email and SMS confirmation | 45 |
| Authentication and sessions | Login, refresh, token purposes, expiry, rate limits (password, registration, password change, liveness, step-up), lockouts | 24 |
| Roles and permissions | Every API endpoint with no token, the wrong role and the right role; scope limits (bank supervisor, customer sees only their own case) | 110 endpoints checked, about 90 explicit expectations |
| API contract | Every endpoint's status codes and response shape against the OpenAPI schema; bad input never returns a 500 | 1 generated check per operation, plus 25 hand-written |
| Data integrity | Ledger and case status always agree, balance debited once, audit chain verifies and detects tampering, encryption at rest, migrations bring an old database up to date | 20 |
| Configuration and startup | Production refuses development secrets, settings parsing, engine fallback modes | 10 |
| MCP and tools | Tool lists per agent, no money tool offered, same results as direct calls | 10 |
| Frontend logic | API client (refresh, errors), terminal message mapping, formatting | 20 |

## 5. Quality assurance beyond unit tests

| QA type | What we do | Target |
| --- | --- | --- |
| End-to-end in a real browser | Customer: land, register with a face scan, log in, see balance, answer an alert yes and no, change password, log out. Operator: sign in per role, trigger each scenario, watch live activity, manual block, reports, calibration, add customer, simulate drills | 32 specs |
| Mobile and responsive | The customer site at phone, tablet and desktop widths: no sideways scroll, buttons reachable | 9 |
| Accessibility | Automated axe checks on every customer page and the sign-in page; keyboard path through registration and login | 14 |
| Security testing | Static scan (bandit), dependency audit, secret scan, injection payloads on every string field of the API, authorisation bypass attempts (another customer's case, report, receipt), token misuse | 40 |
| Performance and load | Decision latency budget, 50 concurrent cases, 200 concurrent reads of the dashboard, rate limits hold under burst | 8 |
| Resilience | Models down, SMS down, email down, an agent crash, Redis down, database busy, restart mid-case | 12 |
| Mutation spot-check | Deliberately break 24 rules (a threshold, a comparison, a removed check) and confirm the suite fails each time | 24 mutants |
| Evaluation | Rule quality on synthetic scenarios; face matching on labelled photos | existing harness, rerun |
| Manual acceptance | A checklist for what a script cannot judge: wording, layout, the feel of the flow | 20 checks |

## 6. Totals

| Group | Tests |
| --- | --- |
| Agents | 362 |
| Non-agent areas (section 4) | about 250 including the generated API checks |
| QA (section 5) | about 140 specs, scans and checks, plus 24 mutants |
| **Planned in all** | **about 750 distinct tests and checks** |

The suite runs far more cases than that, because many tests are parametrised. The report states both numbers.

## 7. Exit criteria

The system is considered tested enough when all of these hold. Any that fail are reported as open, not hidden.

1. Every test passes, twice in a row.
2. Line coverage at least 85% overall and at least 95% for `risk.py`, `protocol.py`, `charters.py`, `privacy.py`, `critic.py`, `ledger.py` and `profile.py`. Branch coverage at least 75% overall.
3. Each agent has at least its target number of tests, or the report says which categories fell short and why.
4. The mutation spot-check kills at least 90% of the mutants.
5. No high-severity finding from the static scan. Dependency findings are fixed or written down with a reason.
6. All end-to-end specs pass at desktop and phone widths. No serious or critical accessibility violation on the customer pages.
7. Performance budgets met: a decision in under one second at the median (mock model), 50 concurrent cases with no failures, rate limits still enforced under a burst.
8. No endpoint returns a 500 for malformed input.
9. The test report lists what the suite cannot prove.

## 8. Execution order

| Stage | Work | Output |
| --- | --- | --- |
| A | Baseline: coverage per module and per agent, test counts, static scans | Gap list |
| B | Agent tests, in the order Policy, Risk, Supervisor, Verification, Response, Alert, Report, Telecom, Banking, Analytics | Tests added, coverage up |
| C | Generated API and role-permission matrix | Contract and permission tests |
| D | Playwright suite in the repository, desktop and phone, with axe | E2E and accessibility tests |
| E | Security tests, load and concurrency, resilience, mutation spot-check | Non-functional results |
| F | Final run, report, push | `docs/TEST_REPORT.md` |

## 9. Manual acceptance checklist

1. Every sentence on the landing page is true of the product.
2. A first-time customer can register without help, and every error says what to fix.
3. The "Yes, it was me" step explains what is about to happen before the camera starts.
4. A customer who does not answer gets the reminder and then a person, and is told so plainly.
5. No screen shows a score, a template or another customer's data to a customer.
6. The operator console shows every automated step with its time.
7. Every message the customer receives says that PayShield.lk never asks for a code or a password.
8. A receipt can be understood by someone who has never seen the system.
9. The registration works with a bright window behind the customer, in a dim room, with glasses, and with a cap.
10. The site is usable with one hand on a phone.
11 to 20. Not written. The test report lists all ten checks above as not yet run, because each needs a person.
