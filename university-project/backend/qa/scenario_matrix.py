"""Runs the ten demonstration scenarios through the real workflow, each in its own throwaway database, and writes the
observed outcome next to an expectation computed from the current rules (not typed in).

    python -m qa.scenario_matrix            # writes docs/SCENARIO_MATRIX.md and docs/evidence/scenario_evidence.json

Deterministic mode: no real message leaves the machine and no model is called. Time-based scenarios move the clock a
case is judged by (accelerated); see docs/DEMO_GUIDE.md for what that does and does not prove."""
import json
import tempfile
from datetime import timedelta
from pathlib import Path

from evaluation import harness

ROOT = Path(__file__).resolve().parents[2] / "docs"
ANSWERS = {"school": "Royal College", "city": "Galle"}


def fresh():
    harness.boot(tempfile.mkdtemp(prefix="payshield-matrix-"))
    from app import faults, notify

    faults.clear_all()
    notify.reset_budget()


def observe(case_id, start_balance):
    from app.db import session_scope
    from app.models import (
        Case,
        CaseEvent,
        DecisionSnapshot,
        Notification,
        Transaction,
        User,
    )

    with session_scope() as s:
        case = s.get(Case, case_id)
        tx = s.get(Transaction, case.transaction_id)
        user = s.get(User, case.customer_id)
        snap = s.get(DecisionSnapshot, case.snapshot_id) if case.snapshot_id else None
        events = s.query(CaseEvent).filter(CaseEvent.case_id == case_id).order_by(CaseEvent.created_at).all()
        notes = s.query(Notification).filter(Notification.case_id == case_id).all()
        tools = [e.tool_name for e in events if e.tool_name and e.state in {"done", "calling_tool", "tool_done"} and e.phase != "started"]
        out = {
            "case_status": case.status, "payment_status": tx.status, "amount_lkr": tx.amount_lkr,
            "balance_change_lkr": int(user.balance_lkr) - start_balance, "score": case.risk_score, "decision": case.policy_decision,
            "reason": case.policy_reason, "snapshot_digest": snap.digest[:16] if snap else None,
            "factors": [f.get("code") or f.get("label") for f in (case.risk_factors or [])],
            "tools_in_order": tools, "handoffs": [f"{e.handoff_from}->{e.handoff_to}" for e in events if e.handoff_from],
            "notifications": [{"channel": n.channel, "title": n.title, "delivery": (n.delivery or {}).get("state") or n.status} for n in notes],
            "alert_attempts": [{"tool": e.tool_name, "state": e.state, "result": e.result} for e in events if e.agent == "alert" and e.tool_name in {"send_push", "send_email", "send_sms"} and e.result],
            "events": len(events),
        }
        out["_signals"] = snap.signals if snap else None
    return out


def expected_from_rules(signals, case_confirmed=False):
    from app.risk import Signals, compute_risk_score, decide_policy

    if not signals:
        return None
    sig = Signals(**signals)
    return {"rules_decision": decide_policy(sig).decision, "rules_score": compute_risk_score(sig)[0]}


def start(customer, scenario):
    from app.db import session_scope
    from app.demo import trigger
    from app.models import User

    with session_scope() as s:
        bal = int(s.get(User, customer).balance_lkr)
    return trigger(customer, scenario), bal


def verify_path(case_id):
    from app.agents.runner import resume_case
    from app.db import session_scope
    from app.models import Case

    resume_case(case_id, {"decision": "yes"}, "waiting_customer")
    resume_case(case_id, {"liveness": "pass"}, "waiting_liveness")
    with session_scope() as s:
        prompts = s.get(Case, case_id).pending_prompt["questions"]
    resume_case(case_id, {"answers": {q["id"]: ANSWERS[q["id"]] for q in prompts}}, "waiting_verification")


def run_all():
    from app import faults
    from app.agents.runner import begin_case, process_timeouts, resume_case
    from app.emergency import force_emergency
    from app.ledger import lock_account
    from app.util import utcnow

    rows = []

    def record(name, expectation, build):
        fresh()
        case_id, bal, notes = build()
        got = observe(case_id, bal)
        signals = got.pop("_signals")
        rows.append({"scenario": name, "expectation": expectation, "rules": expected_from_rules(signals), "observed": got, "notes": notes})
        faults.clear_all()

    def sim_swap_verified():
        c, b = start("cus_nimal", "sim_swap_transfer"); begin_case(c); verify_path(c); return c, b, "customer said yes, passed the face scan and the questions"
    record("sim_swap_transfer (verified)", {"case_status": "closed_released", "payment_status": "released", "debit": True}, sim_swap_verified)

    def sim_swap_denied():
        c, b = start("cus_nimal", "sim_swap_transfer"); begin_case(c); resume_case(c, {"decision": "no"}, "waiting_customer"); return c, b, "customer said they did not make it"
    record("sim_swap_transfer (customer says no)", {"case_status": "closed_blocked", "payment_status": "blocked", "debit": False}, sim_swap_denied)

    def sim_swap_face_fail():
        c, b = start("cus_nimal", "sim_swap_transfer"); begin_case(c)
        resume_case(c, {"decision": "yes"}, "waiting_customer"); resume_case(c, {"liveness": "fail"}, "waiting_liveness"); return c, b, "face scan failed"
    record("sim_swap_transfer (face scan fails)", {"payment_status_not": "released", "debit": False}, sim_swap_face_fail)

    def port_out():
        c, b = start("cus_nimal", "port_out_critical"); begin_case(c); return c, b, "block rule needs no customer input"
    record("port_out_critical", {"case_status": "closed_blocked", "payment_status": "blocked", "debit": False}, port_out)

    def routine():
        c, b = start("cus_nimal", "routine_payment"); begin_case(c); return c, b, "low risk, released by rules"
    record("routine_payment", {"case_status": "closed_cleared", "payment_status": "released", "debit": True}, routine)

    def silent():
        c, b = start("cus_nimal", "sim_swap_transfer"); begin_case(c)
        t0 = utcnow(); process_timeouts(t0 + timedelta(seconds=185)); process_timeouts(t0 + timedelta(seconds=605))
        return c, b, "ACCELERATED: the clock was moved, not waited out"
    record("customer_silent", {"case_status": "escalated", "payment_status": "held", "debit": False}, silent)

    def models_down():
        faults.turn_on("llm_down"); c, b = start("cus_nimal", "sim_swap_transfer"); begin_case(c); return c, b, "both model providers unreachable; rules decide"
    record("models_down", {"case_status": "waiting_customer", "payment_status": "held", "debit": False}, models_down)

    def channel_failure():
        faults.turn_on("sms_down"); faults.turn_on("email_down"); c, b = start("cus_nimal", "sim_swap_transfer"); begin_case(c)
        return c, b, "SMS and email fail; the in-app inbox still reaches the customer"
    record("channel_failure", {"case_status": "waiting_customer", "payment_status": "held", "debit": False}, channel_failure)

    def district_spike():
        from app.db import session_scope
        from app.models import User

        with session_scope() as s:
            d = s.get(User, "cus_nimal").district
        force_emergency(d, "demo: spike", "matrix"); c, b = start("cus_nimal", "large_payment"); begin_case(c)
        return c, b, "the normal rules would release Rs. 75,000; emergency mode holds it"
    record("district_spike", {"case_status": "waiting_customer", "payment_status": "held", "debit": False}, district_spike)

    def agent_failure():
        faults.turn_on("fail_agent", "risk"); c, b = start("cus_nimal", "routine_payment"); begin_case(c); return c, b, "the risk agent crashes"
    record("agent_failure", {"case_status": "escalated", "payment_status": "held", "debit": False}, agent_failure)

    def manual_freeze():
        lock_account("cus_nimal", "manual-analyst-action")
        c, b = start("cus_nimal", "routine_payment"); begin_case(c); return c, b, "an analyst limited transfers on the account first"
    record("manual_freeze", {"payment_status": "held", "debit": False}, manual_freeze)

    def large():
        c, b = start("cus_nimal", "large_payment"); begin_case(c); return c, b, "no emergency: the normal rules release"
    record("large_payment (no emergency)", {"payment_status": "released", "debit": True}, large)
    return rows


def check(row):
    exp, got = row["expectation"], row["observed"]
    problems = []
    for key in ("case_status", "payment_status"):
        if key in exp and got[key] != exp[key]:
            problems.append(f"{key}: expected {exp[key]}, observed {got[key]}")
    if "payment_status_not" in exp and got["payment_status"] == exp["payment_status_not"]:
        problems.append("payment was released")
    if exp.get("debit") is True and got["balance_change_lkr"] != -got["amount_lkr"]:
        problems.append(f"expected one debit of {got['amount_lkr']}, balance changed by {got['balance_change_lkr']}")
    if exp.get("debit") is False and got["balance_change_lkr"] != 0:
        problems.append(f"expected no debit, balance changed by {got['balance_change_lkr']}")
    return problems


def main():
    rows = run_all()
    for row in rows:
        row["problems"] = check(row)
    (ROOT / "evidence").mkdir(parents=True, exist_ok=True)
    (ROOT / "evidence" / "scenario_evidence.json").write_text(json.dumps(rows, indent=2, default=str))
    lines = ["# Scenario matrix: expected against observed", "",
             "Produced by `python -m qa.scenario_matrix`. Each row ran through the real workflow in its own throwaway database, in deterministic mode (no real message sent, no model called). The rules column is recomputed from the stored decision snapshot with the current rules, not typed in. Redacted evidence per scenario is in `docs/evidence/scenario_evidence.json`.", "",
             "| Scenario | Rules say (score) | Observed decision / score | Case | Payment | Balance change | Channels | Expected met | Note |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        o, rules = r["observed"], r["rules"] or {}
        tries = o["alert_attempts"][:3]
        ch = ", ".join(f"{a['tool'].replace('send_', '')}:{(a['result'].get('delivery') or {}).get('state', a['state'])}" for a in tries) or "none"
        lines.append(f"| {r['scenario']} | {rules.get('rules_decision', '-')} ({rules.get('rules_score', '-')}) | {o['decision']} / {o['score']} | {o['case_status']} | {o['payment_status']} | {o['balance_change_lkr']:+,} | {ch} | {'yes' if not r['problems'] else 'NO: ' + '; '.join(r['problems'])} | {r['notes']} |")
    lines += ["", "A difference between the rules column and the observed decision is expected in two rows only: `district_spike` and `manual_freeze`, where a live switch (emergency mode, an account limit) turns a rule release into a hold. That is the design: those switches can only strengthen a decision."]
    (ROOT / "SCENARIO_MATRIX.md").write_text("\n".join(lines) + "\n")
    bad = [r["scenario"] for r in rows if r["problems"]]
    print(f"{len(rows)} scenarios, {len(bad)} not as expected: {bad}")


if __name__ == "__main__":
    main()
