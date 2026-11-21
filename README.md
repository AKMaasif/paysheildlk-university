<p align="center"><img src="university-project/docs/branding/logo-horizontal.png" alt="PayShield.lk" width="380"></p>

# PayShield.lk

A pilot fraud desk that stops **SIM-swap payment fraud** on Sri Lankan bank payments.

A criminal who takes over a victim's phone number can intercept SMS codes and move their money. PayShield reads a telecom feed and a bank feed side by side, and a fixed rule layer decides whether to **release, hold or block** the payment. For a held payment, the customer confirms on the phone they registered earlier, with a live face check and security questions, never by SMS code. The language model only writes the analyst's note. It never chooses the outcome.

All people, phone numbers and balances in the project are fictional.

## Where things are

Everything lives in the [`university-project/`](university-project) folder.

```
university-project/
├── README.md          full documentation, run instructions and demo script
├── project/           main.py launcher and requirements.txt
├── backend/           FastAPI service, agents, rules, tests and evaluation
├── frontend/          Next.js operator console and customer site
├── docs/              proposal, workflow, project details, screenshots, evaluation results
├── docker-compose.yml
└── .env.example
```

| Read this | For |
| --- | --- |
| [Full README](university-project/README.md) | Architecture, security model, emergencies and the demo script |
| [Proposal (PDF)](university-project/docs/Proposal.pdf) | Background, research, agent design and evaluation plan |
| [Workflow](university-project/docs/Workflow.md) | How a case moves from the first signal to the report |
| [Project details](university-project/docs/PROJECT_DETAILS.md) | Objectives, features, interfaces and limits |
| [Evaluation results](university-project/docs/evaluation/results.md) | How the system performed on synthetic scenarios |

## Run it

With Docker:

```bash
cd university-project
cp .env.example .env
docker compose up --build
```

Then open the operator console at http://localhost:3000 and the customer site at http://localhost:3000/c. API docs are at http://localhost:8000/docs.

Without Docker, and the sample sign-in accounts, are in the [full README](university-project/README.md).

## Tests

```bash
cd university-project/backend
pip install -r requirements.txt
pytest
```

## Technology

Python, FastAPI and LangGraph on the backend. Next.js and TypeScript on the frontend. PostgreSQL or SQLite, Redis streams, and Docker Compose.
