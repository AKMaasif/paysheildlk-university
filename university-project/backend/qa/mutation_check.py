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
    ("M17", "app/answers.py", "return hmac.compare_digest(digest.hex(), digest_hex)", "return True", "any security answer is accepted", ["tests/test_qa_telecom_banking_verification.py"]),
    ("M18", "app/api.py", "if forced and case.bank != forced:\n        return False", "if False:\n        return False", "a bank supervisor sees other banks' cases", ["tests/test_qa_access.py"]),
    ("M19", "app/desk.py", "if case is not None and case.bank != actor.bank:", "if False:", "the manual switch ignores the bank", ["tests/test_qa_access.py"]),
    ("M20", "app/agents/tools.py", "        if limited:", "        if False:", "a limited account's payment is not held", ["tests/test_qa_response.py"]),
    ("M21", "app/emergency.py", "HOLD_ABOVE_LKR = 50_000", "HOLD_ABOVE_LKR = 500_000", "emergency mode holds only huge payments", ["tests/test_emergencies.py"]),
    ("M22", "app/notify.py", 'if _CODE.search(message or ""):', "if False:", "an SMS may carry a code", ["tests/test_qa_alert.py"]),
    ("M23", "app/face.py", "MATCH, LIKELY, UNCERTAIN = 80.0, 60.0, 30.0", "MATCH, LIKELY, UNCERTAIN = 70.0, 60.0, 30.0", "face match tier lowered to 70", ["tests/test_face.py", "tests/test_qa_face_edges.py"]),
    ("M24", "app/face.py", '_used[body["n"]] = now', "pass", "a face challenge can be used twice", ["tests/test_face.py"]),
    ("M25", "app/agents/runner.py", 'if tx is not None and tx.status == "pending":\n            tx.status = "held"', 'if False:\n            tx.status = "held"', "a crash leaves the payment free to move", ["tests/test_emergencies.py", "tests/test_qa_resilience.py"]),
    ("M26", "app/customer.py", "if len(password) < 8:", "if len(password) < 4:", "passwords of 4 characters accepted", ["tests/test_customer_site.py", "tests/test_security.py"]),
    ("M27", "app/emergency.py", "SPIKE_COUNT = 8", "SPIKE_COUNT = 80", "a fraud spike needs 80 cases, not 8", ["tests/test_emergencies.py"]),
    ("M29", "app/main.py", "allow_credentials=False,", "allow_credentials=True,", "browsers may send credentials to the API", ["tests/test_qa_cors.py"]),
    ("M30", "app/config.py", 'bad = [o for o in self.allowed_origins if o == "*" or not o.startswith("https://")]', "bad = []", "production accepts a wildcard origin", ["tests/test_qa_cors.py"]),
    ("M31", "app/api.py", "if runtime.settings is None or not runtime.settings.drills_enabled:", "if False:", "drills run in production", ["tests/test_qa_production.py"]),
    ("M32", "app/seed.py", "if len(password) < 14 or password in published or", "if len(password) < 14 or", "a published demonstration password may start production", ["tests/test_qa_production.py"]),
    ("M33", "app/config.py", '_flag("DRILLS_ENABLED", "false" if production else "true")', '_flag("DRILLS_ENABLED", "true")', "drills default to on in production", ["tests/test_qa_production.py"]),
    ("M34", "app/logs.py", "record.args = tuple(scrub(a) if isinstance(a, str) else a for a in record.args)", "pass", "secrets in log arguments (the access log) are written out", ["tests/test_qa_monitoring.py"]),
    ("M35", "app/ops.py", 'ready = checks["database"] == "ok" and checks["checkpointer"] == "ok" and checks["timers"] == "ok"', "ready = True", "the service reports ready when it is not", ["tests/test_qa_monitoring.py"]),
    ("M36", "app/api.py", "await asyncio.wait({getter, closed}, timeout=20, return_when=asyncio.FIRST_COMPLETED)", "await asyncio.wait({getter}, timeout=20, return_when=asyncio.FIRST_COMPLETED)", "a departed live-stream client is kept until the next ping", ["tests/test_qa_monitoring.py"]),
    ("M37", "app/api.py", 'if body.action == "release" and tx.kind == "transfer":', "if False:", "a supervisor's release does not take the money from the balance", ["tests/test_qa_response_more.py"]),
    ("M38", "app/api.py", '        if case.status != "waiting_customer":\n            # A late answer', '        if False:\n            # A late answer', "a late answer is written to a closed case's record", ["tests/test_qa_response_more.py"]),
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
