"""Writes worked input/output samples and sample PDF incident reports from real runs of the workflow.

    python -m qa.make_samples        # writes docs/samples/

Each sample runs in a throwaway database in deterministic mode: no real message is sent and no model is called.
All people, numbers and payments are fictional."""

import json
import tempfile
from datetime import timezone
from pathlib import Path

from evaluation import harness

OUT = Path(__file__).resolve().parents[2] / "docs" / "samples"


def _mask(value: str | None) -> str | None:
    return None if value is None else ("*" * max(0, len(value) - 3)) + value[-3:]


def _iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if moment else None


def collect(case_id: str, customer_id: str, responses: list[dict]) -> tuple[dict, bytes]:
    from app.db import session_scope
    from app.models import (
        Case,
        CaseEvent,
        Notification,
        Report,
        SimEvent,
        Transaction,
        User,
    )
    from app.report_pdf import build_case_pdf

    with session_scope() as s:
        case = s.get(Case, case_id)
        user = s.get(User, customer_id)
        tx = s.get(Transaction, case.transaction_id)
        sims = s.query(SimEvent).filter(SimEvent.case_id == case_id).order_by(SimEvent.occurred_at).all()
        earlier = (
            s.query(Transaction)
            .filter(Transaction.case_id.is_(None), Transaction.customer_id == customer_id, Transaction.origin == "demo")
            .order_by(Transaction.occurred_at)
            .all()
        )
        events = s.query(CaseEvent).filter(CaseEvent.case_id == case_id).order_by(CaseEvent.created_at).all()
        notes = s.query(Notification).filter(Notification.case_id == case_id).all()
        report = s.query(Report).filter(Report.case_id == case_id).one()
        start_time = events[0].created_at if events else None
        sample = {
            "input": {
                "customer": {"id": user.id, "name": user.full_name, "bank": user.bank, "phone": _mask(user.phone)},
                "telecom_feed": [
                    {"event": e.event_type, "at": _iso(e.occurred_at), "detail": e.detail} for e in sims
                ],
                "bank_feed_before_the_payment": [
                    {"kind": t.kind, "payee": t.payee, "new_payee": t.new_payee, "at": _iso(t.occurred_at)}
                    for t in earlier
                ],
                "payment": {
                    "id": tx.id, "amount_lkr": tx.amount_lkr, "payee": tx.payee, "new_payee": tx.new_payee,
                    "status_when_it_arrived": "pending",
                },
                "customer_responses": responses,
            },
            "output": {
                "case_id": case.id,
                "risk_score": case.risk_score,
                "risk_factors": case.risk_factors,
                "policy_decision": case.policy_decision,
                "policy_reason": case.policy_reason,
                "case_status": case.status,
                "payment_status": tx.status,
                "notifications": [{"channel": n.channel, "title": n.title} for n in notes],
                "timeline": [
                    {
                        "t_plus_s": round((e.created_at - start_time).total_seconds(), 1) if start_time else None,
                        "agent": e.agent,
                        "message": e.message,
                        "tool": e.tool_name,
                    }
                    for e in events
                    if e.message or e.tool_name
                ],
                "report": {"title": report.title, "sha256_of_pdf": report.sha256},
            },
        }
    pdf = build_case_pdf(case_id, show_case_line=False)  # the same report the console serves: title block, timeline chart, factors, agents
    return sample, pdf


def main() -> None:
    harness.boot(tempfile.mkdtemp(prefix="payshield-samples-"))
    from app.agents.runner import begin_case, resume_case
    from app.db import session_scope
    from app.demo import trigger
    from app.models import Case
    from app.seed import PILOT_ANSWERS
    from qa.scenario_matrix import fresh, live_selfie

    answers = PILOT_ANSWERS["cus_nimal"]
    OUT.mkdir(parents=True, exist_ok=True)

    def run(slug: str, scenario: str, steps: str) -> None:
        fresh()
        case_id = trigger("cus_nimal", scenario)
        begin_case(case_id)
        responses: list[dict] = []
        if steps == "verify":
            resume_case(case_id, {"decision": "yes"}, "waiting_customer")
            responses.append({"step": "payment question", "answer": "Yes, this was me"})
            resume_case(case_id, live_selfie(), "waiting_liveness")
            responses.append({"step": "face scan", "answer": "20 live frames (look straight, turn left, turn right)"})
            with session_scope() as s:
                prompts = s.get(Case, case_id).pending_prompt["questions"]
            resume_case(case_id, {"answers": {q["id"]: answers[q["id"]] for q in prompts}}, "waiting_verification")
            responses.append({"step": "security questions", "answer": "all correct (answers not stored in this file)"})
        elif steps == "deny":
            resume_case(case_id, {"decision": "no"}, "waiting_customer")
            responses.append({"step": "payment question", "answer": "No, this was not me"})
        sample, pdf = collect(case_id, "cus_nimal", responses)
        sample["scenario"] = scenario
        (OUT / f"{slug}.json").write_text(json.dumps(sample, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (OUT / f"{slug}-incident-report.pdf").write_bytes(pdf)
        print(slug, sample["output"]["policy_decision"], sample["output"]["case_status"], sample["output"]["payment_status"])

    run("01-hold-then-verified-release", "sim_swap_transfer", "verify")
    run("02-hold-then-customer-denies-block", "sim_swap_transfer", "deny")
    run("03-port-out-critical-block", "port_out_critical", "none")
    run("04-routine-payment-release", "routine_payment", "none")


if __name__ == "__main__":
    main()
