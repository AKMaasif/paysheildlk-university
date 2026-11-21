"""The Supervisor agent and the machinery it runs on: the event bus, the fail-safe, the case runner, the
handoff protocol and the charters that bound every agent."""
import asyncio
import random
import threading

import pytest

from app.agents.runner import begin_case, case_lock, fail_safe, resume_case, spawn_case
from app.charters import CHARTERS, CharterViolation, enforce
from app.db import session_scope
from app.demo import trigger
from app.events import EventBus, emit, event_dict
from app.models import Case, CaseEvent, Transaction
from app.protocol import (
    ROUTES,
    HandoffRejected,
    open_sealed,
    seal,
    sign_envelope,
    verify_envelope,
)
from app.runtime import runtime
from evaluation import harness
from evaluation.archetypes import classic_takeover

MONEY = {"hold_transaction", "release_transaction", "block_transaction", "lock_account", "request_sim_freeze"}


# ---------------------------------------------------------------- charters: what each agent may and may not do

def test_every_agent_has_a_complete_charter_and_none_may_use_what_it_is_forbidden():
    assert set(CHARTERS) == {"supervisor", "telecom", "banking", "risk", "policy", "alert", "verification", "response", "analytics", "case"}
    for name, c in CHARTERS.items():
        assert c.role and c.task and c.scope and c.autonomy and c.tools, name
        assert not set(c.tools) & set(c.forbidden), f"{name} is allowed and forbidden the same tool"


def test_only_three_agents_can_touch_money_and_each_can_touch_only_its_own_part():
    reach = {name: set(c.tools) & MONEY for name, c in CHARTERS.items() if set(c.tools) & MONEY}
    assert reach == {
        "policy": {"hold_transaction", "release_transaction"},
        "verification": {"release_transaction"},
        "response": {"block_transaction", "lock_account", "request_sim_freeze"},
    }


def test_the_agents_that_read_attacker_typed_text_cannot_reach_money():
    for name in ("banking", "alert", "case", "telecom", "analytics", "risk", "supervisor"):
        assert not set(CHARTERS[name].tools) & MONEY, name


@pytest.mark.parametrize("agent,tool", [("telecom", "release_transaction"), ("risk", "hold_transaction"), ("alert", "block_transaction"), ("case", "lock_account"), ("analytics", "send_sms"), ("banking", "get_sim_swap_status"), ("response", "release_transaction"), ("supervisor", "generate_report")])
def test_a_call_outside_the_charter_is_refused(agent, tool):
    with pytest.raises(CharterViolation):
        enforce(agent, tool)


def test_an_unknown_agent_or_tool_is_refused():
    with pytest.raises(CharterViolation):
        enforce("intruder", "get_sim_swap_status")
    with pytest.raises(CharterViolation):
        enforce("telecom", "")


# ---------------------------------------------------------------- the handoff protocol

KEY = "k" * 32


def test_every_route_in_the_table_can_be_sealed_and_opened_once():
    for i, (a, b) in enumerate(sorted(ROUTES)):
        sealed = seal(KEY, a, b, f"C{i}", {"n": i})
        assert open_sealed(KEY, sealed, b, f"C{i}") == {"n": i}
        with pytest.raises(HandoffRejected, match="replayed"):
            open_sealed(KEY, sealed, b, f"C{i}")


def test_a_signature_depends_on_every_field():
    base = sign_envelope(KEY, "telecom", "risk", "C1", {"a": 1}, nonce="n1", timestamp=1_000)
    same = sign_envelope(KEY, "telecom", "risk", "C1", {"a": 1}, nonce="n1", timestamp=1_000)
    assert base.signature == same.signature
    for change in (
        dict(sender="banking"), dict(receiver="policy"), dict(case_id="C2"), dict(payload={"a": 2}), dict(nonce="n2"), dict(timestamp=1_001),
    ):
        args = dict(sender="telecom", receiver="risk", case_id="C1", payload={"a": 1}, nonce="n1", timestamp=1_000)
        args.update(change)
        other = sign_envelope(KEY, args["sender"], args["receiver"], args["case_id"], args["payload"], args["nonce"], args["timestamp"])
        assert other.signature != base.signature, change
    assert sign_envelope("other-key", "telecom", "risk", "C1", {"a": 1}, "n1", 1_000).signature != base.signature


def test_verification_rejects_a_bad_signature_without_remembering_the_nonce():
    env = sign_envelope(KEY, "telecom", "risk", "C1", {"a": 1}, nonce="fresh-1")
    env.signature = "0" * 64
    with pytest.raises(ValueError, match="bad signature"):
        verify_envelope(KEY, env)
    env.signature = sign_envelope(KEY, "telecom", "risk", "C1", {"a": 1}, nonce="fresh-1", timestamp=env.timestamp).signature
    verify_envelope(KEY, env)  # the forged attempt did not burn the nonce


@pytest.mark.parametrize("bad", [{}, {"sender": "telecom"}, "text", None, {"sender": "telecom", "receiver": "risk", "case_id": "C", "timestamp": 1, "nonce": "n", "payload": {}, "signature": "s", "extra": 1}])
def test_a_malformed_handoff_is_rejected(bad):
    with pytest.raises((HandoffRejected, TypeError, AttributeError)):
        open_sealed(KEY, bad, "risk", "C")


def test_routes_only_lead_forward_and_nothing_hands_back_to_the_supervisor():
    assert all(b != "supervisor" for _a, b in ROUTES)
    assert ("risk", "release") not in ROUTES and ("alert", "release") not in ROUTES and ("telecom", "policy") not in ROUTES
    assert {b for a, b in ROUTES if a == "policy"} == {"alert", "response", "release"}


# ---------------------------------------------------------------- the event bus

def test_events_reach_every_subscriber_and_a_repeat_is_ignored():
    async def go():
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        q1, q2 = bus.subscribe(), bus.subscribe()
        bus.publish_live({"id": "e1", "n": 1})
        bus.observe_remote({"id": "e1", "n": 1})  # the same event arriving again from Redis
        bus.observe_remote({"id": "e2", "n": 2})
        await asyncio.sleep(0.05)
        got1 = [q1.get_nowait()["id"] for _ in range(q1.qsize())]
        bus.unsubscribe(q1)
        bus.publish_live({"id": "e3"})
        await asyncio.sleep(0.05)
        return got1, q2.qsize(), q1.qsize()

    got1, q2_size, q1_size = asyncio.run(go())
    assert got1 == ["e1", "e2"] and q2_size == 3 and q1_size == 0


def test_a_slow_subscriber_loses_its_oldest_event_not_the_newest():
    async def go():
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        q = bus.subscribe()
        for i in range(450):
            bus.publish_live({"id": f"x{i}", "n": i})
        await asyncio.sleep(0.2)
        return q.qsize(), q.get_nowait()["n"], [q.get_nowait()["n"] for _ in range(q.qsize())][-1]

    size, first, last = asyncio.run(go())
    assert size == 400 and first == 50 and last == 449


def test_with_no_loop_bound_publishing_is_harmless_and_the_memory_of_seen_ids_is_bounded():
    bus = EventBus()
    bus.publish_live({"id": "a"})
    for i in range(4600):
        bus.publish_live({"id": f"i{i}"})
    assert len(bus.seen) <= 4500 and "i4599" in bus.seen


def test_unsubscribing_someone_who_never_subscribed_is_fine():
    EventBus().unsubscribe(asyncio.Queue())


def test_an_event_is_stored_published_and_a_redis_failure_does_not_lose_it(boot, monkeypatch):
    class BrokenRedis:
        def xadd(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(runtime, "redis", BrokenRedis())
    case_id = trigger("cus_nimal", "routine_payment")
    payload = emit(case_id, agent="telecom", state="done", message="hello", provider="rules", model="deterministic", latency_ms=3)
    assert payload["agent"] == "telecom" and payload["message"] == "hello" and payload["created_at"]
    with session_scope() as session:
        row = session.query(CaseEvent).filter(CaseEvent.case_id == case_id).one()
        shaped = event_dict(row, "cus_nimal", "BankCylone")
    assert shaped["case_id"] == case_id and shaped["customer_id"] == "cus_nimal" and shaped["latency_ms"] == 3


# ---------------------------------------------------------------- the fail-safe: a crash never frees the money

def _case_with(tx_status):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    with session_scope() as session:
        case = session.get(Case, case_id)
        session.get(Transaction, case.transaction_id).status = tx_status
    return case_id


def _state(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        return case.status, session.get(Transaction, case.transaction_id).status


@pytest.mark.parametrize(
    "money,expected",
    [("pending", ("escalated", "held")), ("held", ("escalated", "held")), ("blocked", ("closed_blocked", "blocked")), ("released", ("closed_released", "released"))],
)
def test_a_crash_never_changes_a_decided_payment_and_always_holds_an_undecided_one(boot, money, expected):
    case_id = _case_with(money)
    assert fail_safe(case_id, "An agent failed.") == expected[0]
    assert _state(case_id) == expected


def test_the_fail_safe_for_a_missing_case_says_so_and_tells_a_held_customer(boot):
    assert fail_safe("PS-none", "x") == "unknown"
    case_id = _case_with("pending")
    fail_safe(case_id, "An agent failed.")
    from app.models import Notification

    with session_scope() as session:
        assert any("Payment still on hold" in n.title for n in session.query(Notification).filter(Notification.case_id == case_id))


def test_a_case_that_cannot_start_ends_held_and_with_an_analyst(boot, monkeypatch):
    import app.agents.runner as runner

    monkeypatch.setattr(runner, "invoke_initial", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("graph exploded")))
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    assert _state(case_id) == ("escalated", "held")


def test_a_case_that_fails_on_resume_ends_held(boot, monkeypatch):
    import app.agents.runner as runner

    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    monkeypatch.setattr(runner, "invoke_resume", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("resume exploded")))
    assert resume_case(case_id, {"decision": "yes"}, "waiting_customer") is True
    assert _state(case_id) == ("escalated", "held")


def test_resuming_a_case_that_is_not_waiting_for_that_does_nothing(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    assert resume_case(case_id, {"decision": "yes"}, "waiting_verification") is False
    assert resume_case("PS-none", {}, "waiting_customer") is False
    assert _state(case_id) == ("waiting_customer", "held")


def test_each_case_has_its_own_lock_and_the_same_case_always_the_same_one():
    assert case_lock("PS-1") is case_lock("PS-1") and case_lock("PS-1") is not case_lock("PS-2")


def test_two_threads_on_the_same_case_never_run_at_once(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    inside, overlap = [0], [False]
    import app.agents.runner as runner

    original = runner._begin

    def slow(cid):
        inside[0] += 1
        overlap[0] = overlap[0] or inside[0] > 1
        original(cid)
        inside[0] -= 1

    runner._begin = slow
    try:
        threads = [threading.Thread(target=begin_case, args=(case_id,)) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    finally:
        runner._begin = original
    assert overlap[0] is False


def test_a_spawned_case_runs_in_the_background_and_reaches_a_decision(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    spawn_case(case_id)
    import time

    for _ in range(100):
        if _state(case_id)[0] == "waiting_customer":
            break
        time.sleep(0.1)
    assert _state(case_id) == ("waiting_customer", "held")


# ---------------------------------------------------------------- the supervisor's own step

def test_the_supervisor_opens_the_case_and_hands_to_telecom_and_banking_with_signed_messages(boot):
    run = harness.run_case(classic_takeover(random.Random(1)))
    events = run["events"]
    first = [e for e in events if e[0] == "supervisor"]
    assert any(e[1] == "create_case" for e in first)
    assert {e[0] for e in events if e[1] == "verify_handoff"} >= {"telecom", "banking", "risk", "policy"}
    assert next(i for i, e in enumerate(events) if e[0] == "supervisor") < next(i for i, e in enumerate(events) if e[0] == "telecom")


def test_no_charter_lists_a_tool_as_both_allowed_and_forbidden():
    from app.charters import CHARTERS

    for name, charter in CHARTERS.items():
        assert not set(charter.tools) & set(charter.forbidden), name
