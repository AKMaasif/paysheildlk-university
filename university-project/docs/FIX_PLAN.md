# Fix plan for the 15 review findings

Each finding was checked against the code at commit `7a5166a` before planning. "Confirmed" means the code does what the finding says. **Status:** all 15 findings are now addressed (see `docs/STATUS.md` for the details and what remains open). The plan below is kept as it was written.

## Verification result

| # | Finding | Checked against the code | Note |
|---|---|---|---|
| 1 | No authenticator step at customer login | Confirmed: `/api/auth/login` returns tokens after the password. The `/api/auth/totp` endpoint and pending-token flow exist but only operators use them. | |
| 2 | One client IP behind the proxy | Confirmed: `_client_ip` returns `request.client.host`; the proxy forwards no client address. | `login_fail` is already keyed by username, so only `login_ip` and any other per-IP limits are affected. |
| 3 | Lint failing | Confirmed: `ruff check app qa evaluation tests` (the CI command) reports 31 errors, all in `qa/scenario_matrix.py` (E702), and `ruff format --check` would reformat it. | My earlier "ruff clean" claim only checked part of the tree. That was my miss. |
| 4 | Two answer-hash schemes | Confirmed: `answers.py` salted PBKDF2; `security.py` unsalted SHA-256 with `answer_pepper`; normalisation differs. | |
| 5 | Two questions per demo customer | Confirmed: seed holds a school and a city only. | |
| 6 | PII stored in plain text | Confirmed for `nic`, `phone`, `account_number`, `email` columns. | |
| 7 | Weak encryption key set-up | Confirmed: `crypto._key` is a bare SHA-256 with a literal fallback. | |
| 8 | Guessable usernames | Confirmed: `clean_name(display_name)`. | |
| 9 | Tokens in localStorage, no CSP | Confirmed: `lib/api.ts` uses `localStorage`. | |
| 10 | Untyped liveness body | Confirmed: `body: dict`. | |
| 11 | Test hooks and `/api/demo/nudge` | Confirmed: `VerifyIn.liveness`, `analyse(..., verdict)`; `demo/nudge` lacks `require_drills`. | |
| 12 | Oversized files | Confirmed: `api.py` 69 KB, `graph.py` 51 KB. | |
| 13 | Junk files | Partly: `backend/.coverage` and `docs/Proposal.docx` are tracked. Tracked images are 34 files, 4.7 MB, not 16 MB. | Do not edit or remove the proposal without asking; the owner may want it kept. |
| 14 | Docs mismatch | Confirmed: TEST_REPORT says 20 frontend tests (actual 28) and both 73 and 57 browser tests. | |
| 15 | Handover docs | Partly: architecture map, demo guide, scenario matrix exist; no status / remaining-work / setup doc. | |

## Order of work

Do the small safe fixes first so CI is green, then the security items in dependency order, then the large refactor last, because it touches the files the others edit.

### Phase 1: green and clean (about 1 hour)
- **3** Rewrite the `;` statements in `qa/scenario_matrix.py` by hand into real lines, run `ruff check --fix` and `ruff format` on that file, then run the exact CI commands. Done when `ruff check app qa evaluation tests` is clean and the CI lint job is green. Also run `ruff format --check` only if CI runs it.
- **13** `git rm --cached backend/.coverage`, add `.coverage`, `htmlcov/`, `*.sqlite` to `.gitignore`. Keep `Proposal.docx` unless told otherwise. List the 34 images, keep those the README and customer-site docs link to, remove the rest in one commit.
- **11b** Put `require_drills` on `/api/demo/nudge`. Add it to the API-matrix test.

### Phase 2: login security (about 1.5 days)
- **2** Proxy passes the caller's address in `X-Forwarded-For`; add a setting `TRUSTED_PROXIES` (default the proxy's own address). `_client_ip` honours the header only when the direct peer is in that list and takes the right-most untrusted entry. Key `login_ip` limits by IP, and the failed-login counter by `username + IP` so one attacker cannot lock a victim out from everywhere. Tests: two clients behind one proxy are limited separately; a forged header from an untrusted peer is ignored; the victim can still sign in from their own IP after an attacker's failures.
- **1** Customer password step returns a short-lived pending token (purpose `totp`) when TOTP is enrolled, then `/api/auth/totp` issues real tokens. Registration creates the authenticator secret, shows a QR code and the secret, and activates it only after a first valid code (the customer is not logged in until then). Add a code screen to `app/c/login/page.tsx` and the registration page. Decide with the owner what happens to existing customers with no authenticator: force enrolment at next login (recommended). The demo customers need seeded enrolment, or a `DEMO_ASSIST`-only way to read their secret, otherwise the live demo gets slower. Update the e2e tests, the README and the demo script. Run Phase 2 after Phase 1 so CI is meaningful.
- **9** Move tokens into HttpOnly, Secure, SameSite=Strict cookies set by the proxy route; add CSRF protection for state-changing calls (SameSite plus an origin check is enough here); add a Content-Security-Policy in `next.config`. Risk: the WebSocket stream authenticates with a token today; it needs a cookie or a one-time ticket. Do this after item 1 because both change the login pages.

### Phase 3: stored secrets (about 2 days)
- **7** Derive keys with HKDF from a required 32-byte `ENCRYPTION_KEY`, remove the literal fallback, refuse to start without it (tests set one). Existing ciphertext: add a version prefix (`gcm2$`), keep reading `gcm$` with the old derivation, and re-encrypt lazily or with a migration command.
- **6** Encrypt `nic`, `phone`, `account_number`, `email` with AES-GCM; add `*_hash` columns (HMAC with a separate key, not plain SHA) for lookups and uniqueness. Phone is used by the SIM-event join, so `sim_events.phone` needs the same keyed hash or the join breaks; plan the migration with Alembic and an old-database upgrade test. This is the riskiest change; do it in its own commit with a backup-and-restore test.
- **4** One module `answers`: normalise once (lower-case, strip punctuation and extra spaces), hash with Argon2id and a per-answer salt. Re-seed demo customers; migrate existing rows on next successful answer (verify old hash, rewrite new). Remove `answer_pepper` from the default path and the docs.
- **5** Seed 6 to 8 questions per demo customer; registration requires at least 6; the round builder draws 3 different questions in random order and does not repeat a question across rounds until the pool is used. Update the README answers for the demo.

### Phase 4: smaller items (about 0.5 day)
- **8** Customer picks a username at registration; check uniqueness (case-insensitive) in the database with a unique index; keep full-name login working for existing demo customers through a migration that fills `username` and flags the old rule.
- **10** Typed model for the liveness body: at most N frames, each decoded size capped (for example 600 KB), total capped, content type checked, reject early before decoding. Add a request-size limit at the proxy too.
- **11a** Remove `VerifyIn.liveness` and the `verdict` argument from production code; tests call a test helper in `tests/helpers.py` that patches the analyser. Make sure no e2e test relied on the hook.

### Phase 5: structure and docs (about 2 days)
- **12** Split `api.py` into routers (`routes/auth.py`, `customer.py`, `desk.py`, `demo.py`, `admin.py`) and `graph.py` into one module per agent plus `graph_build.py`. Mechanical moves only, no behaviour change, done in small commits with the full suite run after each. The API matrix test is the safety net for the routes. Do this last so earlier fixes do not conflict.
- **14** Re-run backend, frontend and browser suites, then fill the counts from the actual runs (backend 1,676 passed and 4 skipped on the latest SQLite run; frontend 28; browser count to be measured, not copied). Remove the contradictory numbers and the stale authenticator sentence once item 1 is true.
- **15** Add `docs/STATUS.md` (done, remaining, known limits, setup, demo script link, owner questions). Reuse `DEMO_GUIDE.md` and `ARCHITECTURE_MAP.md`.

## Questions that change the work
1. Should existing customers be forced to enrol an authenticator at next login (recommended), or only new registrations?
2. For the live demo, is it acceptable that a demo customer's authenticator is pre-enrolled with a seeded secret shown only when `DEMO_ASSIST` is on?
3. May `docs/Proposal.docx` and which screenshots be removed from git history, or only from the current tree (history rewrite is not planned)?

## Definition of done
CI green on the pushed commit; backend suite, Postgres/Redis suite, browser suite and frontend tests re-run and counted; a migration test for each stored-data change; README, DEMO_GUIDE, TEST_REPORT and STATUS agree with the code.
