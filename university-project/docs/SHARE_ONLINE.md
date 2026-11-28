# Share the demo over the internet (Option A)

Everything keeps running on your PC: the backend, Postgres, Redis, face matching and the AI calls. A tunnel publishes
one address. Anyone with the link can use the customer view on their phone. When your PC, Docker or the tunnel stops,
the link stops.

Status: the pieces below are written and unit-tested, but this exact tunnel set-up has **not been run end to end** by the
author. Do a full rehearsal on a phone using mobile data (not your Wi-Fi) before an audience sees it.

## 1. One-time

1. Install Cloudflare's tunnel tool. In PowerShell: `winget install --id Cloudflare.cloudflared`
2. In `.env` (the same folder as `docker-compose.yml`) set:
   ```
   COOKIE_SECURE=true
   DEMO_MODE=hybrid
   ```
   Leave SMS and email as they are (log only) unless you have set up Text.lk and SMTP and the allow-lists.

## 2. Each time

1. Start the app and the front door:
   ```
   docker compose --profile share up --build -d
   ```
2. Check it: open `http://localhost:8080` (the website). The backend health page is `http://localhost:8000/api/health`.
3. Start the tunnel and keep the window open:
   ```
   cloudflared tunnel --url http://localhost:8080
   ```
   It prints an address like `https://something-random.trycloudflare.com`. That is the link. A new one is made every time.
4. On your phone, open `https://<that address>/c`. Register or sign in as a customer.

## 3. Rules for a safe demo

- Share only the `/c` path. The fraud desk console is on the same site; keep its login private.
- Everyone shares one demo database. For each volunteer: console -> **Add customer** (made-up NIC and account number, their
  name, mobile and email), then they register at `/c/register` using exactly those details.
- Real SMS and email go only to numbers and addresses listed in `DEMO_ALLOWED_PHONES` and `DEMO_ALLOWED_EMAILS`, and only if
  the Text.lk key and SMTP settings are filled in. Edit `.env`, then `docker compose --profile share up -d`.
- Volunteers must agree to the face scan being stored (encrypted) on your PC.
- Turn off sleep on the PC. Press **Reset demo state** between sessions.

## 4. If something fails

| Symptom | Likely cause |
|---|---|
| Link does not open | Tunnel window closed, PC asleep, or Docker stopped |
| Camera will not start | The page was opened over `http`; use the `https` link |
| Alert only appears after a few seconds | Live updates not connected; the page still refreshes every 4 seconds |
| Cannot sign in on the phone | `COOKIE_SECURE` not set to `true`, or an old session; sign out and retry |
