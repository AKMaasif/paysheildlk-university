# Deployment guide

How to run PayShield.lk for real users. Read section 1 first: it says what has and has not been checked.

## 1. What is and is not verified

Checked by tests in this repository: the backend on PostgreSQL 16 and Redis 7, the production start rules (section 4), the
database migrations, the browser tests against a production build of the front end.

**Not checked here:** the Docker images were never built (this environment has no Docker daemon), the reverse proxy sample in
section 6 was never run, and nothing has run with real SMS, email, push, operator SIM-swap or AI-model accounts. Do a trial
deployment and the checks in section 9 before trusting any of it.

## 2. The pieces

| Piece | What it is | Port |
| --- | --- | --- |
| Backend | FastAPI service: the agents, the rules, the API and the live stream (`/ws`) | 8000 |
| Front end | Next.js site: the customer pages under `/c`, the operator console | 3000 |
| PostgreSQL 16 | Data, case checkpoints | 5432 |
| Redis 7 | Live events between processes. Optional: without it events stay in memory | 6379 |

The browser talks only to the front end. The front end forwards `/backend/...` to the backend, so the API is not exposed to
the browser directly, except the live stream, which the browser opens to `/ws`.

## 3. Run exactly one backend process

Run **one** backend process (one `uvicorn` with no `--workers`). These live in memory inside the process:

* the rate limits (wrong passwords, registration, face scans, authenticator codes, security answers),
* the list of face challenges already used (a challenge may be used once),
* the drill switches and the handoff replay guard.

With two processes each keeps its own copy: a limit can be passed by switching between them, and a face challenge could be used
once on each. A second copy is safe only as a cold standby that takes no traffic. Moving these into Redis is the work needed
before running more than one process.

## 4. Production settings

Set `APP_ENV=production`. The backend then **refuses to start** unless all of this holds:

| Setting | Requirement |
| --- | --- |
| `JWT_SECRET`, `ENCRYPTION_KEY`, `HMAC_KEY`, `ANSWER_PEPPER` | Each set, at least 32 characters, not the development value. Make them with `openssl rand -hex 32` |
| `DEMO_ASSIST` | `false` (it exposes live authenticator codes) |
| `ALLOWED_ORIGINS` | The real site address(es), each starting `https://`, comma separated. Never `*` |
| `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` | Needed on the very first start only (section 5) |

Other settings:

| Setting | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | SQLite file | Use `postgresql+psycopg://user:password@host:5432/db` |
| `REDIS_URL` | empty | `redis://host:6379/0`. Empty means in-memory events |
| `CHECKPOINTER` | `auto` | `auto` uses Postgres when the database is Postgres, otherwise SQLite. If Postgres cannot be reached it falls back to a SQLite file and logs an error |
| `FACE_ENGINE` | `dlib` | `legacy` is a two-frame test heuristic. Never use it with real customers |
| `FACE_CHALLENGE` | `true` | The random look-left, blink, smile challenge. Keep on |
| `GEMINI_API_KEY`, `META_API_KEY` | empty | Optional. Without them the deterministic engine writes the notes. No decision depends on a model |
| `TEXTLK_API_KEY`, `TEXTLK_API_URL`, `SMS_SENDER_ID` | empty | SMS. Empty means messages are logged, not sent |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | empty | Email. Empty means logged, not sent |
| `FIREBASE_PROJECT_ID`, `FIREBASE_CLIENT_EMAIL`, `FIREBASE_PRIVATE_KEY` | empty | Push. All three are required |
| `TELCO_BASE_URL`, `TELCO_TOKEN` | empty | The operator's CAMARA SIM-swap gateway. Empty uses the built-in simulation, which is **not** real SIM data |
| `REALERT_SECONDS`, `ESCALATE_SECONDS` | 180, 600 | Customer reminder and hand-over to an analyst |
| `DRILLS_ENABLED` | `false` in production | Turns on the Simulate and Demo pages' endpoints. They write pretend events into customer records |
| `SIMULATED_BOOK` | `false` in production | Fills the dashboards with 30 days of invented payments. Off in production |
| `RATE_LIMIT_SCALE` | `1` | For browser tests only. Ignored in production |
| `LOG_FORMAT` | `text` | `json` writes one JSON object per line for a log collector. Tokens and passwords are blanked from every line either way |
| `LOG_LEVEL` | `INFO` | |
| `STEP_DELAY_MS` | 400 | A pause between agent steps so a person can watch. Set to `0` in production |
| `CUSTOMER_PROFILE`, `MCP_TOOLS` | `true`, `false` | Optional agent features, see the README |

The front end reads two settings **when it is built**, not when it runs:
`NEXT_PUBLIC_WS_URL` (for example `wss://bank.example/ws`) and `NEXT_PUBLIC_WS_PORT`. Pass them as build arguments.
Changing them later has no effect until the front end is rebuilt.

## 5. First start

1. Set the settings in section 4, plus `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` (at least 14 characters, a
   letter and a number, and not one of the published demonstration passwords).
2. Start the backend. On an empty database it creates the tables, marks them at the newest migration, and creates **one**
   `super_admin`. It creates no customers, no demonstration operators, no invitations and no simulated payments.
3. Sign in to the console with that address and password. Use Administration to approve operators and add each person with
   the narrowest role (analyst, telecom operator, bank supervisor tied to one bank, auditor).
4. Add customers on the Add customer page (the bank's record). A customer then registers on the customer site; every detail
   must match the record.
5. Remove `BOOTSTRAP_ADMIN_PASSWORD` from the environment. It is only read when the database has no users.

The development workspace (`pilot-admin` and the other published passwords) exists only when `APP_ENV` is not `production`.

## 6. HTTPS and the reverse proxy

Terminate TLS in front of both services. An nginx sketch (not run here):

```nginx
server {
  listen 443 ssl http2;
  server_name bank.example;
  # ssl_certificate and ssl_certificate_key here

  location /ws {                       # the live stream
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
  }
  location / {                         # the site; it forwards /backend/... to the API itself
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    client_max_body_size 8m;           # a face scan sends several camera frames
  }
}
```

Build the front end with `NEXT_PUBLIC_WS_URL=wss://bank.example/ws`, and set `ALLOWED_ORIGINS=https://bank.example`.
Do not publish port 8000 to the internet. Only the proxy needs it.

Add security headers at the proxy (`Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, a `Content-Security-Policy`
that allows the site's own scripts and the OpenStreetMap tile server the Map page loads from). The application does not set them.

## 7. Database

* **Migrations** run automatically at start (see the README, "Database changes"). A database from before migrations is
  brought up to date and marked. A failed migration stops the start with the error; the service does not run on a half-upgraded
  schema.
* **Backups.** Take them from Postgres: `pg_dump --format=custom --file=payshield-$(date +%F).dump "$DATABASE_URL"`. The case
  checkpoints live in the same database when Postgres is used, so one dump covers everything. Keep dumps encrypted and off the
  server. Face templates in the dump are encrypted with `ENCRYPTION_KEY`; keep that key separately from the dumps.
* **Restore** into an empty database with `pg_restore`, start the service with the same four secrets, then run section 9.
  Without the original `ENCRYPTION_KEY`, stored face templates cannot be read (customers fall back to the security questions)
  and any stored authenticator secrets are lost.
* **Test the restore** before you need it.

## 8. Upgrading

1. Take a backup. 2. Deploy the new image. 3. Start it: pending migrations run, then the service accepts traffic.
4. Run section 9. To go back, restore the backup: migrations are written to move forward, and a downgrade has not been tested.

## 9. After a deployment

* `GET /api/ready` returns 200 with `"ready": true`. Use it as the load balancer or container health check: it is 503 when the database or the checkpointer is down, or when the reminder and escalation timers have stopped. A missing Redis shows as `degraded` and is still 200.
* `GET /api/health` returns `"ok": true`, with `database`, `checkpointer`, `redis`, the channel drivers (`sms`, `email`, `push`) and `telco`.
  Check `database` is `postgres` and `redis` is `connected`; `sms: log` or `email: log` means messages are **not** being sent.
* Sign in as the administrator and open Security: the audit chain shows as intact, and the SMS and email drivers are the real ones, not `log`.
* `POST /api/audit/verify` returns `"ok": true`.
* Run one fictional end-to-end case with a test customer and your own phone: alert received, answer works, receipt downloads.
* Confirm port 8000 and the database are not reachable from the internet.

## 10. What a deployment still lacks

* More than one backend process (section 3).
* A rotation tool for `ENCRYPTION_KEY`. Rotating it today makes stored face templates and authenticator secrets unreadable.
* Real SIM-swap data, a real bank ledger, and a legal review of the face-data handling. See `docs/TEST_REPORT.md`, section 6.
