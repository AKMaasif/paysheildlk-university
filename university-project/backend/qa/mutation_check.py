"""A mutation spot-check: break one rule at a time on purpose and see whether the tests notice.

Each mutant is a small edit to the code (a threshold moved by one, a comparison made looser, a check removed).
The tests that guard that rule are run against the edited copy. A mutant is "killed" when a test fails.
A mutant that survives means the suite cannot tell the rule is broken.

Run from the backend folder:  python -m qa.mutation_check            (all mutants)
                              python -m qa.mutation_check M07 M12    (some of them)
The real code is never edited: the check works on a copy in a temporary folder."""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# id, file, old text, new text, what it breaks, tests that should notice
MUTANTS = [
    ("M01", "app/risk.py", "if age <= 24:", "if age < 24:", "SIM swap in 24 hours becomes 'under 24'", ["tests/test_qa_policy_risk.py"]),
    ("M02", "app/risk.py", 'add(40, "sim_swap_24h"', 'add(35, "sim_swap_24h"', "24 hour SIM swap scores 35 not 40", ["tests/test_qa_policy_risk.py"]),
    ("M03", "app/risk.py", "    if signals.amount_lkr >= 500_000:", "    if signals.amount_lkr > 500_000:", "Rs. 500,000 no longer counts as large", ["tests/test_qa_policy_risk.py"]),
    ("M04", "app/risk.py", "elif signals.amount_lkr >= 100_000:", "elif signals.amount_lkr > 100_000:", "Rs. 100,000 band edge", ["tests/test_qa_policy_risk.py"]),
    ("M05", "app/risk.py", "signals.password_reset_age_hours is not None and signals.password_reset_age_hours <= 12:", "signals.password_reset_age_hours is not None and signals.password_reset_age_hours <= 13:", "password reset window 12h to 13h", ["tests/test_qa_policy_risk.py"]),
    ("M06", "app/risk.py", "and signals.password_reset_age_hours <= 6", "and signals.password_reset_age_hours <= 7", "critical block rule: reset window 6h to 7h", ["tests/test_qa_policy_risk.py"]),
    ("M07", "app/risk.py", 'if score >= _thresholds["hold_score"] or correlated:', 'if score > _thresholds["hold_score"] or correlated:', "hold score 45 no longer holds", ["tests/test_qa_policy_risk.py"]),
    ("M08", "app/risk.py", "signals.sim_swap_age_hours <= 72", "signals.sim_swap_age_hours <= 73", "72 hour correlation window", ["tests/test_qa_policy_risk.py"]),
    ("M09", "app/risk.py", "signals.amount_lkr >= 50_000 and (", "signals.amount_lkr > 50_000 and (", "Rs. 50,000 correlation edge", ["tests/test_qa_policy_risk.py"]),
    ("M10", "app/risk.py", 'add(-25, "familiar_payment"', 'add(25, "familiar_payment"', "a familiar payment raises the score", ["tests/test_qa_policy_risk.py"]),
    ("M11", "app/ledger.py", 'if tx.status == "blocked":\n            return {"transaction_id": tx.id, "status": "blocked", "changed": False, "note": "Already blocked"}', 'if False:\n            return {"transaction_id": tx.id, "status": "blocked", "changed": False, "note": "Already blocked"}', "a blocked payment can be released", ["tests/test_qa_response.py"]),
    ("M12", "app/ledger.py", 'first_release = tx.status != "released"', "first_release = True", "a release is charged every time", ["tests/test_qa_response.py"]),
    ("M13", "app/protocol.py", "if not hmac.compare_digest(expected, envelope.signature):", "if False:", "handoff signatures are not checked", ["tests/test_qa_supervisor.py"]),
    ("M14", "app/charters.py", '        ("create_case",),\n        "Case id and which agents to wake.",', '        ("create_case", "release_transaction"),\n        "Case id and which agents to wake.",', "the Supervisor may release money", ["tests/test_qa_supervisor.py"]),
    ("M15", "app/critic.py", '_URL = re.compile(r"https?://|www\\.", re.IGNORECASE)', '_URL = re.compile(r"https?://", re.IGNORECASE)', "the critic misses a link without a scheme", ["tests/test_qa_analytics_report.py"]),
    ("M16", "app/ratelimit.py", '"login_fail": (5, 15 * 60),', '"login_fail": (500, 15 * 60),', "no lockout after failed passwords", ["tests/test_security.py", "tests/test_qa_platform.py"]),
    ("M17", "app/answers.py", 'return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", old.encode(), salt, _ROUNDS).hex(), digest_hex)', "return True", "any answer is accepted for an older-format security answer", ["tests/test_qa_phase3.py"]),
    ("M18", "app/routes/console.py", "if forced and case.bank != forced:\n        return False", "if False:\n        return False", "a bank supervisor sees other banks' cases", ["tests/test_qa_access.py"]),
    ("M19", "app/desk.py", "if case is not None and case.bank != actor.bank:", "if False:", "the manual switch ignores the bank", ["tests/test_qa_access.py"]),
    ("M20", "app/agents/tools.py", "    if limited:\n        found.append", "    if False:\n        found.append", "a limited account's payment is not held", ["tests/test_qa_response.py"]),
    ("M21", "app/emergency.py", "HOLD_ABOVE_LKR = 50_000", "HOLD_ABOVE_LKR = 500_000", "emergency mode holds only huge payments", ["tests/test_emergencies.py"]),
    ("M22", "app/notify.py", 'if _CODE.search(message or ""):', "if False:", "an SMS may carry a code", ["tests/test_qa_alert.py"]),
    ("M23", "app/face.py", "MATCH, LIKELY, UNCERTAIN = 80.0, 60.0, 30.0", "MATCH, LIKELY, UNCERTAIN = 70.0, 60.0, 30.0", "face match tier lowered to 70", ["tests/test_face.py", "tests/test_qa_face_edges.py"]),
    ("M24", "app/face.py", '_used[body["n"]] = now', "pass", "a face challenge can be used twice", ["tests/test_face.py"]),
    ("M25", "app/agents/runner.py", 'if tx is not None and tx.status == "pending":\n            tx.status = "held"', 'if False:\n            tx.status = "held"', "a crash leaves the payment free to move", ["tests/test_emergencies.py", "tests/test_qa_resilience.py"]),
    ("M26", "app/customer.py", "if len(password) < 8:", "if len(password) < 4:", "passwords of 4 characters accepted", ["tests/test_customer_site.py", "tests/test_security.py"]),
    ("M27", "app/emergency.py", "SPIKE_COUNT = 8", "SPIKE_COUNT = 80", "a fraud spike needs 80 cases, not 8", ["tests/test_emergencies.py"]),
    ("M29", "app/main.py", "allow_credentials=False,", "allow_credentials=True,", "browsers may send credentials to the API", ["tests/test_qa_cors.py"]),
    ("M30", "app/config.py", 'bad = [o for o in self.allowed_origins if o == "*" or not o.startswith("https://")]', "bad = []", "production accepts a wildcard origin", ["tests/test_qa_cors.py"]),
    ("M31", "app/routes/common.py", "if runtime.settings is None or not runtime.settings.drills_enabled:", "if False:", "drills run in production", ["tests/test_qa_production.py"]),
    ("M32", "app/seed.py", "if len(password) < 14 or password in published or", "if len(password) < 14 or", "a published demonstration password may start production", ["tests/test_qa_production.py"]),
    ("M33", "app/config.py", '_flag("DRILLS_ENABLED", "false" if production else "true")', '_flag("DRILLS_ENABLED", "true")', "drills default to on in production", ["tests/test_qa_production.py"]),
    ("M34", "app/logs.py", "record.args = tuple(scrub(a) if isinstance(a, str) else a for a in record.args)", "pass", "secrets in log arguments (the access log) are written out", ["tests/test_qa_monitoring.py"]),
    ("M35", "app/ops.py", 'ready = checks["database"] == "ok" and checks["checkpointer"] == "ok" and checks["timers"] == "ok"', "ready = True", "the service reports ready when it is not", ["tests/test_qa_monitoring.py"]),
    ("M36", "app/routes/stream.py", "await asyncio.wait({getter, closed}, timeout=20, return_when=asyncio.FIRST_COMPLETED)", "await asyncio.wait({getter}, timeout=20, return_when=asyncio.FIRST_COMPLETED)", "a departed live-stream client is kept until the next ping", ["tests/test_qa_monitoring.py"]),
    ("M37", "app/routes/console.py", 'if body.action == "release" and tx.kind == "transfer":', "if False:", "a supervisor's release does not take the money from the balance", ["tests/test_qa_response_more.py"]),
    ("M38", "app/routes/customer.py", '        if case.status != "waiting_customer":\n            # A late answer', '        if False:\n            # A late answer', "a late answer is written to a closed case's record", ["tests/test_qa_response_more.py"]),
    # Rules added with the demonstration and hardening work.
    ("M39", "app/routes/auth.py", "    if role == \"customer\":\n        audit(ident, \"sign_in_password_ok\"", "    if False:\n        audit(ident, \"sign_in_password_ok\"", "a customer's password alone opens a session", ["tests/test_qa_customer_login.py"]),
    ("M40", "app/security.py", "if (user_id, cleaned) in _used_codes:", "if False:", "an authenticator code can be used twice", ["tests/test_qa_customer_login.py"]),
    ("M41", "app/ratelimit.py", '"totp_fail": (5, 10 * 60)', '"totp_fail": (500, 10 * 60)', "wrong authenticator codes never lock the account", ["tests/test_qa_customer_login.py"]),
    ("M42", "app/routes/common.py", "    if not _trusted(peer):\n        return peer", "    if False:\n        return peer", "any visitor can name their own address", ["tests/test_qa_customer_login.py"]),
    ("M43", "app/routes/auth.py", 'attempt = f"{ident}|{ip}"', "attempt = ident", "one visitor's failures lock the customer out everywhere", ["tests/test_qa_customer_login.py"]),
    ("M44", "app/models.py", "        return seal_field(str(value), self._secret(), self.purpose)", "        return value", "personal fields are stored in plain text", ["tests/test_qa_phase3.py"]),
    ("M45", "app/crypto.py", "    if len(text) < MIN_KEY_CHARS:", "    if False:", "a short encryption key is accepted", ["tests/test_qa_phase3.py"]),
    ("M46", "app/answers.py", "            return _hasher.verify(stored, normalise_answer(answer, code))", "            return True", "any answer matches an Argon2id hash", ["tests/test_qa_phase3.py"]),
    ("M47", "app/knowledge.py", 'fresh = [item for item in questions if item["id"] not in (avoid or set())]', "fresh = list(questions)", "a question already asked is asked again at once", ["tests/test_qa_phase3.py"]),
    ("M48", "app/customer.py", "        if taken(chosen):", "        if False:", "two customers can share a username", ["tests/test_qa_phase4.py"]),
    ("M49", "app/limits.py", "    if sum(len(item) for item in frames) > TOTAL_FRAME_CHARS:", "    if False:", "the total size of the camera images is not limited", ["tests/test_qa_phase4.py"]),
    ("M50", "app/limits.py", "        if declared.isdigit() and int(declared) > self.limit:", "        if False:", "a request body of any size is accepted", ["tests/test_qa_phase4.py"]),
    ("M51", "app/sessions.py", 'raw.get(CSRF_HEADER.encode(), b"").decode() != CSRF_VALUE', "False", "a cookie-backed write needs no site header", ["tests/test_qa_cookies.py"]),
    ("M52", "app/routes/stream.py", "if token and origin and origin.rstrip(\"/\") not in runtime.settings.allowed_origins:", "if False:", "another site's page can open the live stream with the cookie", ["tests/test_qa_cookies.py"]),
    ("M53", "app/security.py", "    if jti and session.get(RevokedToken, jti) is not None:", "    if False:", "a signed-out token still works", ["tests/test_qa_revocation.py"]),
    ("M54", "app/ingest.py", "            if same_id or same_time:", "            if False:", "a repeated telecom event is stored again", ["tests/test_qa_ingest.py"]),
    ("M55", "app/ingest.py", "    if (moment - now).total_seconds() > FUTURE_SLACK_SECONDS:", "    if False:", "a future-dated telecom event is accepted", ["tests/test_qa_ingest.py"]),
    ("M56", "app/agents/tools.py", '        "ok": not reasons,', '        "ok": True,', "the last look before release never stops a payment", ["tests/test_qa_signals.py"]),
    ("M57", "app/llm.py", "                result.fell_back = True\n                result.fallback_from = providers[0]", "                result.fell_back = False\n                result.fallback_from = providers[0]", "an answer from the fallback model is not marked as a fallback", ["tests/test_llm.py"]),
    ("M58", "app/routes/auth.py", '            if sessions.portal_for_role(payload.get("role", "")) != portal:\n                continue', '            if False:\n                continue', "signing out of one portal ends the other portal's session", ["tests/test_qa_cookies.py"]),
    ("M28", "app/security.py", "if payload.get(\"purpose\") != purpose:", "if False:", "a refresh token works as an access token", ["tests/test_qa_access.py"]),
]


def run(selected: list[str]) -> int:
    work = Path(tempfile.mkdtemp(prefix="mutants-"))
    copy = work / "backend"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("__pycache__", "data", "*.sqlite*", ".pytest_cache", ".coverage*"))
    rows = []
    for mid, rel, old, new, what, tests in MUTANTS:
        if selected and mid not in selected:
            continue
        target = copy / rel
        original = target.read_text()
        if original.count(old) != 1:
            rows.append((mid, what, "NOT APPLIED", f"pattern found {original.count(old)} times"))
            continue
        target.write_text(original.replace(old, new))
        started = time.time()
        try:
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], cwd=copy, capture_output=True, text=True, timeout=900)
            verdict = "killed" if result.returncode != 0 else "SURVIVED"
            failed = next((line for line in result.stdout.splitlines() if line.startswith("FAILED")), "")
        finally:
            target.write_text(original)
        rows.append((mid, what, verdict, f"{time.time() - started:.0f}s {failed[:90]}"))
        print(f"{mid} {verdict:9} {what}", flush=True)
    shutil.rmtree(work, ignore_errors=True)
    applied = [r for r in rows if r[2] != "NOT APPLIED"]
    killed = [r for r in applied if r[2] == "killed"]
    print(f"\n{len(killed)} of {len(applied)} mutants killed ({100 * len(killed) // max(1, len(applied))}%)")
    for r in rows:
        if r[2] != "killed":
            print("  not killed:", r)
    return 0 if len(killed) == len(applied) and applied else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
