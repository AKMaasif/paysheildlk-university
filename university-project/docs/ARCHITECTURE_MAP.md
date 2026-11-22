# Architecture map: from a fraud event to the dashboard

This describes what the code does today. Where it says "new", the behaviour was added for the demonstration work and is covered by tests named in the right-hand column.

## Data flow

```
 telecom side                          bank side
 SIM events (sim_events)               transactions, logins, payees
   - seeded / demo / injected            - demo trigger or customer action
   - POST /api/sim-events (new)
   - operator gateway (CAMARA, optional)
        |                                   |
        +----------------+------------------+
                         v
              case created (status new)  --> spawn_case()  (one thread per case, one lock per case)
                         v
        LangGraph supervisor graph  (SQLite or Postgres checkpointer)
          supervisor -> telecom  \                      telecom and banking run side by side,
                     -> banking  /  -> risk -> policy   each reads its core tools, then up to 3
                                                        extra read-only lookups the planner picks
          risk   : score from ONE decision snapshot (new)
          policy : rule decision on the SAME snapshot (new); emergency mode and account limits can only strengthen it
              |-- block  -> response (block payment, record SIM-freeze request, limit account)
              |-- hold   -> alert -> wait_customer (interrupt) -> liveness -> questions -> verify
              |-- release-> release
          release: last look (new): fresh snapshot compared with the decision snapshot; worse => stay held, person gets it
                         v
              report -> analytics -> case closed / escalated
```

Every hop between agents is a signed handoff (HMAC), checked against a route table and a replay guard. A tampered, expired, wrong-route or replayed handoff is refused and the case goes to the fail-safe, which holds the money.

## Who decides what

| Question | Decided by | Model involvement |
| --- | --- | --- |
| Hold, block or release | `decide_policy` (rules) plus emergency mode and account limits | None. A model cannot change it. |
| Score and factors | `compute_risk_score` | None |
| Which extra read-only lookups to run | planner (rules planner, or a model that may only name allowed tools; core lookups always run; maximum 3 extra) | Chooses among allowed reads only |
| The prose note an agent writes | model if available, else deterministic text; a critic checks it against the facts | Writes text only |
| Face verdict, answer grading | server-side verification | None |
| Money movement | `release_transaction` (debits once), `block_transaction`/`hold_transaction` (no debit) | None |

If every model is down the rules decide exactly as before. If an agent crashes, `fail_safe` holds a pending payment and gives the case to an analyst; a payment that already reached release or block is never reversed.

## Where state lives

| State | Where |
| --- | --- |
| Cases, payments, balances, notifications (with per-channel delivery record), events and tool traces | database (SQLite or Postgres) |
| Decision snapshots (signals, provenance, conflicts, digest, score, decision) (new) | `decision_snapshots` |
| Waiting graph state (survives restart) | checkpointer (SQLite file or Postgres) |
| Live fan-out to the console | event bus, WebSocket, Redis when configured; every event is also written to the database, so a refresh shows the same history |
| Emergency mode, calibration | database |
| Drill switches (model down, SMS down, agent crash) | memory only, reset on restart |

## The signal contract (new)

Each decision reads the world once into a snapshot: the signals, and for each observation its source (`local-db` or `camara-http`), origin, when it happened, when this system received it, its age, and the contract version. A digest over the facts (not the clock) lets two readings be compared. If the operator gateway answers, it is authoritative for the SIM-swap fact and a disagreeing local record is stored as a conflict, not silently merged. If the gateway is configured but unreachable the case fails safe to a held payment and a person; it never falls back to local data. The score, the policy decision and the final release check all refer to snapshot ids, so a case can be audited later against the exact inputs it used.

## Events that arrive while a case is open (new)

`POST /api/sim-events` (drills only, writer role) calls `app/ingest.py`: unknown type, unknown phone and future-dated events are refused; repeats (same id, or same type and time) change nothing; events older than 72 hours are recorded but do not reopen cases; otherwise the event is stored with its source and received time and the customer's open cases are compared against their snapshot. A materially worse picture escalates that case (money stays held); anything else only adds a timeline note. History is not rewritten. Only the event's own customer is touched.

## What the dashboard shows

The activity terminal and the case timeline are built from the stored events, not from the browser's memory. A tool call appears as a start and a finish sharing one call id, with redacted arguments and results, who planned it, and its duration. Delivery states are the ones in `docs/DEMO_GUIDE.md`; "logged only" and "inbox stored" are never shown as "delivered". The SIM freeze is shown as a recorded, simulated request.
