"""The Response agent and the ledger it writes to: block, freeze, lock, and the analyst's manual switch."""
import pytest

from app import manual
from app.agents import tools
from app.agents.runner import begin_case
from app.db import session_scope
from app.demo import reset_demo, trigger
from app.ledger import (
    audit,
    block_transaction,
    bump_provider,
    hold_transaction,
    lock_account,
    release_transaction,
    request_sim_freeze,
    set_case_fields,
    verify_audit_chain,
)
from app.manual import ManualRefused
from app.models import AuditLog, Case, SimEvent, Transaction, User
from evaluation import harness
from evaluation.archetypes import Scenario


def _tx(status="pending", amount=200_000, kind="transfer"):
    sc = Scenario("t", "benign", False, amount, "Payee", False)
    ctx = harness.seed_scenario(sc)
    with session_scope() as session:
        session.get(Transaction, ctx["transaction_id"]).status = status
        session.get(Transaction, ctx["transaction_id"]).kind = kind
    return ctx, harness.open_case(ctx, amount)


def _balance(customer_id):
    with session_scope() as session:
        return session.get(User, customer_id).balance_lkr


def _status(tx_id):
    with session_scope() as session:
        return session.get(Transaction, tx_id).status


# ---------------------------------------------------------------- the ledger

@pytest.mark.parametrize("start,expect", [("pending", "held"), ("held", "held"), ("blocked", "blocked"), ("released", "released")])
def test_holding_never_undoes_a_decided_payment(boot, start, expect):
    ctx, case_id = _tx(start)
    out = hold_transaction(ctx["transaction_id"], case_id)
    assert out["status"] == expect and _status(ctx["transaction_id"]) == expect
    assert out["changed"] is (start in {"pending", "held"})


def test_blocking_records_why_and_does_not_touch_the_balance(boot):
    ctx, case_id = _tx("held")
    before = _balance(ctx["customer_id"])
    out = block_transaction(ctx["transaction_id"], case_id, "Blocked: not me")
    assert out["status"] == "blocked" and _balance(ctx["customer_id"]) == before
    with session_scope() as session:
        assert session.get(Transaction, ctx["transaction_id"]).detail["block_reason"] == "Blocked: not me"


def test_a_blocked_payment_cannot_be_released_and_a_release_pays_once(boot):
    ctx, case_id = _tx("held", amount=50_000)
    start = _balance(ctx["customer_id"])
    assert release_transaction(ctx["transaction_id"], case_id, "ok")["changed"] is True
    assert _balance(ctx["customer_id"]) == start - 50_000
    release_transaction(ctx["transaction_id"], case_id, "again")
    assert _balance(ctx["customer_id"]) == start - 50_000  # not charged twice
    ctx2, case2 = _tx("held")
    block_transaction(ctx2["transaction_id"], case2, "x")
    assert release_transaction(ctx2["transaction_id"], case2, "x")["changed"] is False and _status(ctx2["transaction_id"]) == "blocked"


@pytest.mark.parametrize("kind,debited", [("transfer", True), ("bill", False), ("purchase", False)])
def test_only_a_transfer_debits_the_balance_on_release(boot, kind, debited):
    ctx, case_id = _tx("held", amount=10_000, kind=kind)
    start = _balance(ctx["customer_id"])
    release_transaction(ctx["transaction_id"], case_id, "ok")
    assert _balance(ctx["customer_id"]) == (start - 10_000 if debited else start)


def test_the_balance_never_goes_below_zero(boot):
    ctx, case_id = _tx("held", amount=900_000_000)
    release_transaction(ctx["transaction_id"], case_id, "ok")
    assert _balance(ctx["customer_id"]) == 0


def test_unknown_things_are_reported_not_invented(boot):
    for fn in (lambda: hold_transaction("TX-none", "c"), lambda: block_transaction("TX-none", "c", "r"), lambda: release_transaction("TX-none", "c", "r")):
        with pytest.raises(LookupError):
            fn()
    with pytest.raises(LookupError):
        lock_account("cus-none", "c")
    set_case_fields("PS-none", status="x")  # a missing case is ignored, not an error
    bump_provider("PS-none", "mock")


def test_provider_counts_add_up(boot):
    ctx, case_id = _tx()
    for p in ("mock", "mock", "meta"):
        bump_provider(case_id, p)
    with session_scope() as session:
        assert session.get(Case, case_id).provider_counts == {"mock": 2, "meta": 1}


# ---------------------------------------------------------------- freeze and lock

def test_a_sim_freeze_returns_a_reference_and_is_recorded_on_the_case_and_the_feed(boot):
    ctx, case_id = _tx()
    out = request_sim_freeze(ctx["phone"], case_id, "customer said not me")
    assert out["reference"].startswith("FRZ-") and out["status"] == "request_recorded_simulated" and out["simulated"] is True and out["operator_contacted"] is False
    with session_scope() as session:
        assert session.get(Case, case_id).sim_freeze_ref == out["reference"]
        event = session.query(SimEvent).filter(SimEvent.phone == ctx["phone"], SimEvent.event_type == "sim_freeze").one()
        assert event.detail["reason"] == "customer said not me"
    assert request_sim_freeze(ctx["phone"], case_id, "again")["reference"] != out["reference"]


def test_locking_limits_the_four_risky_actions_and_is_visible(boot):
    ctx, case_id = _tx()
    out = lock_account(ctx["customer_id"], case_id)
    assert out["actions"] == ["transfer", "new_payee", "password_reset", "port_out"]
    with session_scope() as session:
        user = session.get(User, ctx["customer_id"])
        assert user.restricted and user.restricted_reason == f"demo:{case_id}"


def test_a_limited_account_has_its_next_payment_held_even_when_the_rules_would_release_it(boot):
    """The customer is told transfers stay locked until the fraud desk clears them. That has to be true."""
    ctx, case_id = _tx()
    lock_account(ctx["customer_id"], case_id)
    routine = Scenario("r", "benign", False, 4_850, "CEB Electricity", False)
    sc_ctx = harness.seed_scenario(routine)
    with session_scope() as session:
        user = session.get(User, sc_ctx["customer_id"])
        user.restricted, user.restricted_actions = True, ["transfer"]
    result = tools.apply_policy(sc_ctx["customer_id"], sc_ctx["transaction_id"])
    assert result["decision"] == "hold" and "limit" in result["reason"].lower()
    assert any("limited" in f["label"] for f in result["factors"])


def test_a_limit_never_lowers_a_block_and_never_applies_without_the_transfer_limit(boot):
    sc = Scenario("t", "attack", True, 800_000, "Mule", True, swap_h=1, port_out=True, reset_h=2)
    ctx = harness.seed_scenario(sc)
    with session_scope() as session:
        session.get(User, ctx["customer_id"]).restricted = True
        session.get(User, ctx["customer_id"]).restricted_actions = ["transfer"]
    assert tools.apply_policy(ctx["customer_id"], ctx["transaction_id"])["decision"] == "block"
    routine = harness.seed_scenario(Scenario("r", "benign", False, 4_850, "CEB", False))
    with session_scope() as session:
        session.get(User, routine["customer_id"]).restricted = True
        session.get(User, routine["customer_id"]).restricted_actions = ["port_out"]
    assert tools.apply_policy(routine["customer_id"], routine["transaction_id"])["decision"] == "release"


def test_a_customer_with_a_limited_account_is_asked_not_blocked_and_not_released(boot):
    case_id = trigger("cus_ravi", "port_out_critical")
    begin_case(case_id)
    follow = trigger("cus_ravi", "routine_payment")
    begin_case(follow)
    with session_scope() as session:
        case = session.get(Case, follow)
        assert case.policy_decision == "hold" and case.status == "waiting_customer"
        assert session.get(Transaction, case.transaction_id).status == "held"


def test_resetting_the_demo_lifts_demo_limits_but_not_an_analysts_own(boot):
    a, case_a = _tx()
    b, case_b = _tx()
    lock_account(a["customer_id"], case_a)
    with session_scope() as session:
        user = session.get(User, b["customer_id"])
        user.restricted, user.restricted_actions, user.restricted_reason = True, ["transfer"], "manual: analyst decision"
    out = reset_demo()
    with session_scope() as session:
        assert not session.get(User, a["customer_id"]).restricted and session.get(User, b["customer_id"]).restricted
    assert out["accounts_cleared"] >= 1


# ---------------------------------------------------------------- the analyst's manual switch

def _closed_case():
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    return case_id


def test_a_manual_block_blocks_locks_files_a_report_and_is_audited(boot):
    case_id = _closed_case()
    out = manual.apply(case_id, "block", "Customer rang to say it was not them", "analyst@payshield.lk")
    assert out["case_status"] == "closed_blocked"
    with session_scope() as session:
        case = session.get(Case, case_id)
        assert session.get(Transaction, case.transaction_id).status == "blocked" and session.get(User, case.customer_id).restricted
        assert any(a.action == "kill_block" and a.actor_email == "analyst@payshield.lk" and "rang" in (a.detail or "") for a in session.query(AuditLog).all())


def test_a_manual_freeze_limits_the_account_and_leaves_the_payment_for_the_agents(boot):
    case_id = _closed_case()
    out = manual.apply(case_id, "freeze", "Precaution while we call the customer", "analyst@payshield.lk")
    assert out["action"] == "freeze"
    with session_scope() as session:
        case = session.get(Case, case_id)
        assert session.get(User, case.customer_id).restricted and session.get(Transaction, case.transaction_id).status == "held"


@pytest.mark.parametrize("action", ["delete", "release", "", "BLOCK"])
def test_the_switch_only_knows_freeze_and_block(boot, action):
    with pytest.raises(ManualRefused):
        manual.apply("PS-any", action, "reason here", "a@b.lk")


def test_the_switch_refuses_a_released_payment_a_missing_case_and_a_second_block(boot):
    case_id = trigger("cus_dilani", "routine_payment")
    begin_case(case_id)
    with pytest.raises(ManualRefused, match="already released"):
        manual.apply(case_id, "block", "too late to stop it", "a@b.lk")
    with pytest.raises(LookupError):
        manual.apply("PS-none", "block", "no such case", "a@b.lk")
    held = _closed_case()
    manual.apply(held, "block", "first block of this payment", "a@b.lk")
    with pytest.raises(ManualRefused, match="already blocked"):
        manual.apply(held, "block", "second block of the same", "a@b.lk")


# ---------------------------------------------------------------- the audit chain

def test_the_audit_chain_verifies_and_notices_any_change(boot):
    for i in range(5):
        audit("a@b.lk", "action", f"t{i}", f"detail {i}")
    assert verify_audit_chain()["ok"] is True
    with session_scope() as session:
        row = session.query(AuditLog).order_by(AuditLog.created_at.desc()).first()
        row.detail = "quietly changed"
    broken = verify_audit_chain()
    assert broken["ok"] is False
