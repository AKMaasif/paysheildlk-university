# Project details

## Objectives

- Stop fraudulent payments made after a SIM swap on Sri Lankan bank accounts before the money leaves.
- Confirm with the real customer on a device they registered, without relying on SMS codes.
- Keep every money decision in auditable rules, with language models limited to writing explanations.
- Give analysts one place to watch each case live and give the customer a clear receipt.

## Features

| Area | What it does |
| --- | --- |
| Detection | Telecom and bank feeds are read together. A SIM swap plus an unusual payment is scored and ruled on. |
| Customer site | Landing page, first-time registration that must match the BankCylone record (name, email, NIC, mobile, account number) with a live face scan and a password. Then username and password. Balance, transactions, not-me and yes paths, a live face scan matched to the face stored at registration, security questions, and a forced password change after any alert. |
| Alerts | Push, email and a generic SMS, re-sent at 3 minutes and escalated at 10. |
| Operator console | Live agent activity, case files, transactions on hold, reports, demo control, security status, simulate page. |
| Reports | Markdown and PDF with a timeline, charts and exact times. Customers get a receipt. |
| Analytics | District and province charts, hot spots and rising districts, with a short written brief. |
| Investigation | Telecom and Banking look further than their first reads when what they see calls for it, with a cap. Read-only. |
| Customer profile | Usual payees and amounts from the customer's own ledger adjust the score. |
| Note review | The Report agent checks each agent note against its facts before filing. |
| Calibration | The Analytics agent proposes a rule change from customer answers. An analyst approves it. |
| Evaluation | A harness that tests each agent alone, the whole system, a single-agent baseline, red-team scenarios and prompt injection. |
| Emergencies | Both models down, SMS or email failing, district fraud spike, agent crash, analyst manual freeze or block. |

## Functionality

- **Agents:** supervisor, telecom, banking, risk, policy, alert, verification, response, report and analytics. Each has a written charter that is enforced in code.
- **Model use:** Gemini first, Meta model as fallback, a deterministic mock when no keys are set. Policy never depends on a model.
- **Step-up checks:** selfie and answer grading happen on the server. A client cannot claim a pass.
- **Roles:** analyst, telco, bank supervisor (scoped to one bank), auditor (read-only), super admin.

## Technical details

| Layer | Technology |
| --- | --- |
| Backend | Python 3.12, FastAPI, SQLAlchemy, Pydantic |
| Agents | LangGraph supervisor graph with `interrupt()` and a Postgres or SQLite checkpointer |
| Data | PostgreSQL in Docker, SQLite when run locally; Redis for live events with an in-memory fallback |
| Frontend | Next.js 14 and React 18, hand-written SVG charts |
| Reports | fpdf2 PDF, SHA-256 fingerprint stored with each report |
| Tests | pytest on the backend, including prompt-injection, evaluation and MCP tests |

### Security

- Argon2id password hashes; salted hashes for security answers; AES-256-GCM for the face-scan reference and personal details.
- JWT access tokens (30 minutes) and refresh tokens (12 hours).
- Sliding-window rate limits on sign-in, registration, password change, answers and selfies.
- HMAC-SHA256 signed agent handoffs with replay protection.
- Phone, email, account and NIC numbers are masked before any text reaches a model.
- Audit log as a SHA-256 hash chain, re-checked on the Security page.
- With `APP_ENV=production` the backend refuses to start on development secrets.

### Interfaces

- `POST /sim-swap/v1/check` and `/retrieve-date` mirror the GSMA CAMARA SIM Swap API, so a real operator can replace the mock by setting `TELCO_BASE_URL`.
- API documentation is served at `/docs` when the backend runs.

### Repository layout

```
project/            entry point (main.py) and requirements.txt
docs/               proposal, workflow and these details
backend/app/        agents, rules, API, reports, analytics
backend/tests/      backend tests
frontend/           operator console and customer app
docker-compose.yml  Postgres, Redis, backend and frontend
```

### Limits

- Seed people, numbers and balances are fictional.
- Face verification uses a published face model and a random liveness challenge. It stops photos and replayed video but not a live deepfake or a 3D mask. Its thresholds are starting points until measured on your own images (`python -m evaluation.face_eval`).
- SMS and email use a log driver until Text.lk and SMTP credentials are set.
- Rate limits are held in memory, so they reset on restart.
