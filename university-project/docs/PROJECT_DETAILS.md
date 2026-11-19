# Project Details

## Overview

**PayShield** is a pilot desk for SIM-swap fraud on Sri Lankan bank payments. A criminal who takes over a victim's phone number can intercept SMS codes and move money. PayShield watches for that pattern and stops the payment before the money leaves.

All people, phone numbers and balances in the seed data are fictional.

## Objectives

- Detect SIM-swap-related payment fraud using a telecom signal and a bank signal together.
- Keep the final hold, block or release decision in deterministic rules, not in a language model.
- Confirm with the customer on the device they already registered, never by SMS code.
- Give fraud analysts, telecom operators, supervisors and auditors one shared console with the correct access limits.

## Main components

| Component | Purpose |
| --- | --- |
| Backend API (`project/`) | FastAPI service with the rules, agents, database and event streams |
| Agents | Telecom, banking, risk, alert, verification and response agents coordinated by a LangGraph supervisor |
| Policy (`risk.py`) | Scores a case and applies the hold / block / release rules |
| Notifications | Push, email and generic SMS (no links, no codes) |
| Operator console | Desktop web UI for analysts and supervisors |
| Customer app | Narrow, phone-sized web app for answering the confirmation question |
| Reports | Markdown and PDF case reports |

## Technology

- **Language and API:** Python, FastAPI, Pydantic
- **Orchestration:** LangGraph with Postgres or SQLite checkpointing
- **Data:** PostgreSQL (SQLite for local runs), Redis streams for live events
- **Frontend:** Next.js and TypeScript
- **Security:** salted password hashing, TOTP authenticator, AES-256-GCM for secrets, JWT sessions
- **Packaging:** Docker Compose

## Security notes

- Customers sign in with email, password and an authenticator code. There is no SMS one-time code.
- Knowledge answers are stored as salted hashes. The authenticator secret is encrypted.
- Demo-assist features must be switched off before real use, and the JWT secret must be changed before sharing a host.

## Running it

See the top-level `README.md` for Docker and local run instructions, the demo script and the sample accounts.
