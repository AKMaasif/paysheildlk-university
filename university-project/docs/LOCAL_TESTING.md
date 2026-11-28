# Run it on your own computer and test it

Everything here runs offline by default: no SMS, email or model call leaves your machine until you turn it on (step 7).
The people and money in the demo are fictional.

## 1. What you need

- **Docker Desktop** (includes Docker Compose). It starts the backend, the website, Postgres 16 and Redis 7 for you.
- **Git**, and a browser (Chrome or Edge).
- **An authenticator app on your phone** (Google Authenticator, Microsoft Authenticator or similar). Customers sign in with a 6-digit code.
- A **webcam** for the face scan (see step 6 if you have none).

## 2. Get the code

```bash
git clone -b claude/nifty-mendel-serxgl https://github.com/AKMaasif/sim-swap-shield.git
cd sim-swap-shield/university-project
```

## 3. Create your settings file

Windows (PowerShell or Command Prompt): `copy .env.example .env`. Mac or Linux: `cp .env.example .env`.

Open `.env` and set a long private key for encrypting stored data (32 or more characters). One way:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Put the result on the `ENCRYPTION_KEY=` line. **Keep it: if you change it later the stored data cannot be read and the
service refuses to start.** Everything else can stay as it is for a first run. Never put real keys in chat, in code or in
git; `.env` is not committed.

## 4. Start it

```bash
docker compose up --build
```

The first build takes several minutes (the face-recognition models are about 100 MB). When it settles:

- Website and console: http://localhost:3000 (customer site: http://localhost:3000/c)
- API health: http://localhost:8000/api/health

Stop with `Ctrl+C`, then `docker compose down`. To wipe all data and start clean: `docker compose down -v`.

## 5. The walk-through (about 15 minutes)

1. **Sign in to the console** at http://localhost:3000 as `analyst@payshield.lk` / `pilot-analyst`.
2. Open **Demo control**. Leave the customer as **Nimal Perera**. It shows his registration details, his eight security
   answers and an **authenticator QR code**. Scan the QR code with your phone's authenticator app (once).
3. In a second browser window open http://localhost:3000/c and choose **Register**. Type the details from Demo control,
   choose a username and a password, do the face scan, then type the 6-digit code from your phone app. You land on the
   account page.
4. **A suspicious payment, answered "yes".** In Demo control choose *SIM swap and Rs. 200,000* and trigger it. Watch
   **Live Agent Activity** in the console: the telecom and banking agents run, the rules hold the payment. On the customer
   site the question appears. Choose *Yes, this was me*, do the face scan, answer the security questions (Demo control
   shows the answers). The payment is released and the balance drops once.
5. **A suspicious payment, answered "no".** Trigger it again and choose *No, this was not me*. The payment is blocked, a
   receipt is offered, and the report is in **Reports** (markdown and PDF).
6. **Other cases:** *Port-out, password reset and Rs. 800,000* blocks at once. *Routine bill of Rs. 4,850* releases with
   no question. A customer who does not answer: use *Run the 3-minute re-alert now* and *Run the 10-minute escalation
   now* in Demo control. The money stays held.
7. **Drills** (Simulate page): switch the SMS or email gateway off, the model providers off, or make one agent crash. The
   case still ends safe: the money is never released by a failure.
8. **Log out and in again** as the customer: username, password, then the code from your phone. A code works once, so wait
   for the app to show the next one if you log in twice within a minute.
9. **Sign out** in the console and the customer site: signing out ends the session for real.

If you have no webcam, or want to use a photo, set `FACE_ENGINE=legacy` and `FACE_CHALLENGE=false` in `.env` and restart
(`docker compose up --build`). This weakens the face check and is for demonstrations only.

## 6. Open the site from a phone

The camera only works in a secure context. `http://localhost` counts as secure, but `http://192.168.x.x:3000` does not,
so a phone on your Wi-Fi cannot use the face scan over plain http. Use your computer's browser for the camera steps, or
put the site behind HTTPS.

## 7. Real SMS and email (optional, to your own phone and inbox only)

Real messages go only to addresses you allow-list, and only in hybrid mode. In `.env`:

```
DEMO_MODE=hybrid
DEMO_ALLOWED_PHONES=+94xxxxxxxxx
DEMO_ALLOWED_EMAILS=you@example.com
TEXTLK_API_KEY=...           # your Text.lk key
SMS_SENDER_ID=...            # check what your Text.lk account allows
SMTP_HOST=smtp-relay.brevo.com   # for example Brevo; any SMTP server on port 587 with STARTTLS
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
SMTP_FROM=you@example.com
```

The demo customers have fictional phone numbers, so nothing is sent to them. In the console choose **Add customer**
(Enrol) and create a test customer with **your own phone and email**, then register that customer on the site and trigger a
case. The console shows each message honestly: "accepted, delivery unconfirmed", never "delivered". Restart with
`docker compose up -d` after changing `.env`.

Models (optional): set `META_API_KEY` (main) and `XAI_API_KEY` (Grok, fallback). With neither, built-in text is used and
nothing else changes. Check the model names in `.env.example` against your accounts.

## 8. Run the automated tests (optional)

These run from source, not inside Docker. Stop the Docker stack first (the browser tests use ports 3000 and 8000).

```bash
# backend (Python 3.12 or newer)
cd backend
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q             # about 20 minutes; run suites one at a time

# website logic
cd ../frontend && npm install && node --test tests/*.test.ts

# browser tests (starts its own throwaway backend and site)
cd ../e2e && npm install && node run.mjs
```

## 9. If something goes wrong

| Problem | Fix |
| --- | --- |
| "port is already allocated" | Something else uses 3000, 8000, 5432 or 6379. Stop it or change the port in `docker-compose.yml` |
| The backend stops at start and mentions `ENCRYPTION_KEY` | The key is under 32 characters, or it differs from the one the stored data was written with. Restore the old key, or reset with `docker compose down -v` |
| "That code is not valid, or it was already used" | Wait for the authenticator app to show a new code. Check your phone's clock is set automatically |
| The face scan will not start | Allow the camera in the browser, use http://localhost (not an IP address), or use `FACE_ENGINE=legacy` |
| The page says it cannot reach the API | Check http://localhost:8000/api/health. Run `docker compose logs backend` |
| Everything looks stale after a change | `docker compose up --build` again |
