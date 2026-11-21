"""Runs scenarios through the real agent graph in a throwaway database. The real database is never touched.

Evaluation customers have no district, so the district emergency breaker (which turns releases into holds
after a burst of alerts) does not mix into the rule results. The breaker is tested separately in tests/test_emergencies.py."""

import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import timedelta

from evaluation.archetypes import Scenario

_counter = {"n": 0}


def boot(directory: str | None = None) -> str:
    """Point the app at a temporary database and start it. Returns the directory."""
    directory = directory or tempfile.mkdtemp(prefix="payshield-eval-")
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite:///{directory}/eval.db",
            "CHECKPOINT_PATH": f"{directory}/checkpoints.sqlite",
            "CHECKPOINTER": "sqlite",
            "REDIS_URL": "",
            "JWT_SECRET": "evaluation-only-secret-not-for-production",
            "STEP_DELAY_MS": "0",
            "FACE_ENGINE": "legacy",
            "DEMO_ASSIST": "true",
            "REALERT_SECONDS": "180",
            "ESCALATE_SECONDS": "600",
            "TEXTLK_API_KEY": "",
            "SMTP_HOST": "",
            "FIREBASE_PROJECT_ID": "",
        }
    )
    from app import ratelimit
    from app.runtime import init_runtime

    ratelimit.reset_all()
    init_runtime()
    return directory


def _next() -> int:
    _counter["n"] += 1
    return _counter["n"]


def seed_scenario(sc: Scenario, payee_override: str | None = None) -> dict:
    """Create a fresh customer, their history and the SIM events, then a pending payment. No case yet."""
    from app.db import session_scope
    from app.models import SimEvent, Transaction, User
    from app.security import hash_password
    from app.util import nid, utcnow

    n = _next()
    now = utcnow()
    user_id = f"ev_{n}"
    phone = f"+9470{n:07d}"
    account = f"9{n:09d}"
    nic = f"19{80 + n % 19}{n:08d}"[:12]
    email = f"eval{n}@mail.lk"
    payee = payee_override if payee_override is not None else sc.payee
    tx_id = f"EV-TX-{n}"
    with session_scope() as session:
        session.add(
            User(
                id=user_id, email=email, full_name=f"Eval Customer{n}", password_hash=hash_password("x" * 20), role="customer",
                phone=phone, bank="BankCylone", province=None, district=None, branch="Fort", account_number=account,
                nic=nic, balance_lkr=5_000_000, registered=False, status="active", created_at=now - timedelta(days=200),
            )
        )
        for who, amount, days in sc.history:
            session.add(
                Transaction(
                    id=nid("tx"), customer_id=user_id, kind="transfer", amount_lkr=amount, payee=who, new_payee=False,
                    status="completed", occurred_at=now - timedelta(days=days), origin="eval",
                )
            )

        def sim(kind, hours, detail=None):
            session.add(SimEvent(id=nid("sim"), phone=phone, event_type=kind, occurred_at=now - timedelta(hours=hours), detail=detail or {}, origin="eval", received_at=now))

        if sc.swap_h is not None:
            sim("sim_swap", sc.swap_h, {"channel": "ussd"})
        if sc.imei_h is not None:
            sim("new_imei", sc.imei_h, {"model": "Handset"})
        if sc.loc_h is not None:
            sim("location_change", sc.loc_h, {"from": "Colombo", "to": "Negombo"})
        if sc.port_out:
            sim("port_out", 1.0, {"status": "requested"})
        if sc.reset_h is not None:
            session.add(Transaction(id=nid("tx"), customer_id=user_id, kind="password_reset", amount_lkr=0, status="completed", occurred_at=now - timedelta(hours=sc.reset_h), origin="eval"))
        if sc.new_device:
            session.add(Transaction(id=nid("tx"), customer_id=user_id, kind="login", amount_lkr=0, status="completed", occurred_at=now - timedelta(hours=1), detail={"new_device": True}, origin="eval"))
        session.add(
            Transaction(
                id=tx_id, customer_id=user_id, kind="transfer", amount_lkr=sc.amount, payee=payee, payee_bank="BankCylone",
                new_payee=sc.new_payee, status="pending", occurred_at=now, origin="eval", detail={},
            )
        )
    return {"customer_id": user_id, "transaction_id": tx_id, "phone": phone, "account": account, "nic": nic, "email": email, "payee": payee, "n": n}


def open_case(ctx: dict, amount: int) -> str:
    from app.db import session_scope
    from app.demo import next_case_id
    from app.models import Case
    from app.util import utcnow

    with session_scope() as session:
        case_id = next_case_id(session)
        session.add(
            Case(
                id=case_id, customer_id=ctx["customer_id"], transaction_id=ctx["transaction_id"], status="new", created_at=utcnow(),
                updated_at=utcnow(), alert_count=0, origin="eval", scenario="evaluation", amount_lkr=amount, payee=ctx["payee"],
                bank="BankCylone", province=None, district=None, provider_counts={},
            )
        )
    return case_id


def run_case(sc: Scenario, payee_override: str | None = None) -> dict:
    """Seed, run the whole graph, and read back what the system decided."""
    from app.agents.runner import begin_case
    from app.db import session_scope
    from app.models import Case, CaseEvent, Transaction

    ctx = seed_scenario(sc, payee_override)
    case_id = open_case(ctx, sc.amount)
    started = time.perf_counter()
    begin_case(case_id)
    elapsed = time.perf_counter() - started
    with session_scope() as session:
        case = session.get(Case, case_id)
        tx = session.get(Transaction, ctx["transaction_id"])
        # A tool call is a start marker and a result. Outcomes are the results, so the start markers are left out.
        events = [e for e in session.query(CaseEvent).filter(CaseEvent.case_id == case_id).all() if e.phase != "started"]
        out = {
            "case_id": case_id,
            "decision": case.policy_decision,
            "score": case.risk_score,
            "status": case.status,
            "tx_status": tx.status,
            "latency_s": elapsed,
            "events": [(e.agent, e.tool_name, e.state, e.provider) for e in events],
            "notes": [e.message or "" for e in events if e.state == "done"],
            "ctx": ctx,
        }
    return out


@contextmanager
def model_probe():
    """Record every text the model layer receives. By then it has been through the privacy guard."""
    from app.runtime import runtime

    seen: list[str] = []
    original = runtime.llm._mock

    def spy(purpose, user, fell_back, fallback_from):
        seen.append(user)
        return original(purpose, user, fell_back, fallback_from)

    runtime.llm._mock = spy
    try:
        yield seen
    finally:
        runtime.llm._mock = original


_RAW = [
    ("phone", re.compile(r"(?<!\d)(?:\+94|0094|0)\s?\d{2}[\s-]?\d{3}[\s-]?\d{4}(?!\d)")),
    ("nic", re.compile(r"(?<![\w])(?:\d{12}|\d{9}[VvXx])(?![\w])")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("account", re.compile(r"(?<![\d-])\d(?: ?\d){7,18}(?![\d-])")),
]


def leaks_in(texts: list[str], contexts: list[dict]) -> int:
    """How many texts handed to the model still carry a raw identifier of one of our customers."""
    count = 0
    literals = [v for c in contexts for v in (c["phone"], c["account"], c["nic"], c["email"]) if v]
    for text in texts:
        if any(lit in text for lit in literals) or any(p.search(text) for _k, p in _RAW):
            count += 1
    return count


def tool_audit(events) -> tuple[int, int]:
    """(tool calls, calls outside the calling agent's charter). Handoff checks are not tools."""
    from app.charters import CHARTERS

    calls = bad = 0
    for agent, tool, state, _provider in events:
        if not tool or state != "calling_tool" or tool == "verify_handoff":
            continue
        calls += 1
        charter = CHARTERS.get(agent)
        if charter is None or tool not in charter.tools:
            bad += 1
    return calls, bad
