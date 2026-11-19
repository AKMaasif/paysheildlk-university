"""Telecom and Banking may look further than their first reads. The extra lookups are read-only, capped,
listed in the charter, and never change the decision."""
import random

from app import investigator
from app.charters import CHARTERS
from app.llm import LLMResult
from app.runtime import runtime
from evaluation import harness
from evaluation.archetypes import classic_takeover, routine_bill

MONEY = {"hold_transaction", "release_transaction", "block_transaction", "lock_account", "request_sim_freeze"}


def _tools_called(run, agent):
    return [e[1] for e in run["events"] if e[0] == agent and e[1] and e[1] != "verify_handoff" and e[2] == "calling_tool"]


def _fake_model(monkeypatch, text):
    monkeypatch.setattr(runtime.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(
        runtime.llm, "complete",
        lambda **kw: LLMResult(text=text, provider="gemini", model="fake", latency_ms=1, fell_back=False, fallback_from=None),
    )


def test_every_lookup_is_in_the_charter_and_none_can_move_money():
    for agent in ("telecom", "banking"):
        allowed = set(CHARTERS[agent].tools)
        assert set(investigator.CORE[agent]) | set(investigator.EXTRA[agent]) <= allowed
        assert not allowed & MONEY


def test_rules_planner_looks_further_when_the_first_reads_call_for_it(boot):
    run = harness.run_case(classic_takeover(random.Random(1)))
    assert "get_sim_history" in _tools_called(run, "telecom")
    banking = _tools_called(run, "banking")
    assert "get_payee_history" in banking
    # Each extra step says who planned it. Without a model key that is the rule planner, never "model".
    steps = [e for e in run["events"] if e[2] == "thinking" and e[0] in {"telecom", "banking"} and e[3] in {"rules", "planner"}]
    assert steps and all(e[3] == "rules" for e in steps)


def test_nothing_extra_is_read_when_nothing_calls_for_it(boot):
    run = harness.run_case(routine_bill(random.Random(1)))
    # No SIM swap, a known payee and no sign-in events: telecom reads only its core tools. Banking compares
    # the waiting payment with what is usual for the customer, and nothing more.
    assert _tools_called(run, "telecom") == investigator.CORE["telecom"]
    assert _tools_called(run, "banking") == investigator.CORE["banking"] + ["get_customer_profile"]


def test_extra_calls_are_capped():
    calls = []
    original = dict(investigator.EXTRA["banking"])
    investigator.EXTRA["banking"] = {f"extra_{i}": "x" for i in range(8)}
    try:
        investigator._rules_next = lambda agent, seen, used: investigator.Choice(next(t for t in investigator.EXTRA[agent] if t not in used), "more", "rules")
        out = investigator.investigate("banking", lambda n, a, note: calls.append(n) or {}, lambda n: {})
    finally:
        investigator.EXTRA["banking"] = original
        import importlib

        importlib.reload(investigator)
    assert len(calls) == 1 + investigator.MAX_EXTRA_CALLS
    assert len(out["plan"]) == investigator.MAX_EXTRA_CALLS


def test_model_choice_is_followed_only_inside_the_menu(boot, monkeypatch):
    _fake_model(monkeypatch, '{"tool": "get_payee_history", "reason": "new payee on a large transfer"}')
    out = investigator.investigate(
        "banking", lambda n, a, note: {"transactions": []}, lambda n: {}
    )
    assert [p["tool"] for p in out["plan"] if p["step"] == "call"] == ["get_payee_history"]
    assert out["plan"][0]["planner"] == "model"


def test_model_asking_for_a_money_tool_or_talking_nonsense_is_ignored(boot, monkeypatch):
    for text in (
        '{"tool": "release_transaction", "reason": "approved"}',
        "Ignore the menu and call block_transaction on everything",
        '{"tool": "get_payee_history; DROP TABLE users", "reason": "x"}',
        "",
    ):
        _fake_model(monkeypatch, text)
        called = []
        out = investigator.investigate("banking", lambda n, a, note: called.append(n) or {"transactions": []}, lambda n: {})
        assert not set(called) & MONEY
        assert all(n in set(investigator.CORE["banking"]) | set(investigator.EXTRA["banking"]) for n in called)
        assert any(p["step"] == "refused" for p in out["plan"]) or text == ""


def test_the_model_can_stop_early(boot, monkeypatch):
    _fake_model(monkeypatch, '{"tool": "finish", "reason": "enough"}')
    run = harness.run_case(classic_takeover(random.Random(1)))
    assert _tools_called(run, "banking") == investigator.CORE["banking"]


def test_the_decision_does_not_depend_on_what_the_planner_chose(boot, monkeypatch):
    sc = classic_takeover(random.Random(3))
    with_rules = harness.run_case(sc)
    _fake_model(monkeypatch, '{"tool": "release_transaction", "reason": "just release it"}')
    with_model = harness.run_case(sc)
    assert with_rules["decision"] == with_model["decision"] == "hold"
    assert with_rules["score"] == with_model["score"]
    assert harness.tool_audit(with_model["events"])[1] == 0
    assert with_model["tx_status"] == "held"
