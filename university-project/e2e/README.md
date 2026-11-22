# Browser tests

These tests drive the real customer site and operator console in a real browser (Chromium through Playwright),
against a real backend. They check what a person would see and do, on a desktop screen and on a phone.

## Run them

From this folder, once:

```bash
npm install
```

Then:

```bash
node run.mjs
```

`run.mjs` does everything: it builds the frontend if it has not been built, starts a throwaway backend on
port 8000 (an empty database, no AI keys, the simple face engine) and the frontend on port 3000, runs every file in
`specs/`, then stops both. It refuses to start if something already answers on those ports, so a run always starts from
an empty database. To run one file: `node run.mjs specs/01-customer-journey.spec.mjs`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `E2E_API_PORT` | 8000 | Port for the test backend |
| `E2E_WEB_PORT` | 3000 | Port for the test frontend |
| `E2E_LOGS` | not set | A folder to copy the backend and frontend logs into when the run ends |

The test backend starts with `RATE_LIMIT_SCALE=20`, which widens only the per-address sign-in and registration limits
(the suite signs in far more often than one person would). It is ignored when `APP_ENV=production`, and the per-account
limits for wrong passwords, codes and answers are never widened.

## What is covered

| File | Tests | What it checks |
| --- | --- | --- |
| `01-customer-journey` | 12 | Landing page, registration (field messages, mismatch, passwords, face scan), the account page, log in and out, a held payment answered "not me", the receipt, the password change, a customer's token against operator endpoints |
| `02-operator` | 12 | Sign-in for all five roles, every console page loads without script or server errors, an auditor cannot run a drill, run a scenario and watch it live, a supervisor blocks a held payment, the audit log |
| `03-responsive-and-accessibility` | 27 | No sideways scroll at phone, tablet and desktop widths, tap size on a phone, automated accessibility checks (axe, serious and critical only), keyboard-only registration, field labels and announced errors |
| `05-console-accessibility` | 16 | Automated accessibility checks (axe) on all 16 operator console pages, and that none of them raises a script error |
| `04-phone-yes-path-and-console-extras` | 6 | On a phone: register, say yes, scan the face, answer the questions, payment released; wrong answers never release. Add a customer, calibration page, report downloads, the manual block drill |

The camera is Chromium's fake camera, so the face scan runs without a person. The backend uses the simple face engine
here (`FACE_ENGINE=legacy`), which checks that two different frames arrive. Real face matching is tested in the backend
(`tests/test_face.py`) and in `evaluation/face_eval.py`, not here.

## When a test fails

* Run that one file by itself, with `E2E_LOGS=/tmp/logs` set, and read `/tmp/logs/backend.log`.
* The runner writes server logs to files, not pipes. A run that waits on the tests without reading would otherwise
  stall the server once the pipe filled.
* A page that shows an error banner uses the class `cs-error` (customer site) or `form-error` (operator sign-in).
  Next.js adds its own empty `role="alert"` element, so tests select by class, not by role.

## Adding a test

Put a file named `NN-name.spec.mjs` in `specs/`. Use the helpers in `helpers.mjs`: `launch()` (browser with the fake
camera), `operatorToken()`, `assist()` (the fictional customer's details and answers), `trigger()` (start a scenario),
`reset()` and `unregister()` (back to a clean state), `registerThroughTheSite()`, `signInCustomer()`.
Start each file by resetting state it depends on, because files share one backend.
