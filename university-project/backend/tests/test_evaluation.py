"""The evaluation harness itself. A small run, checking the harness works and the safety invariants hold."""
from evaluation.archetypes import ARCHETYPES
from evaluation.run import run


def test_small_evaluation_run_is_consistent(boot):
    res = run(per_archetype=1, seed=11)
    system = res["system"]
    n = len(ARCHETYPES)
    assert res["meta"]["cases"] == n
    assert system["cases_with_no_decision"] == 0
    assert system["tool_calls"] > 0 and system["tool_calls_outside_charter"] == 0
    assert system["model_texts_seen"] > 0 and system["model_texts_with_raw_identifiers"] == 0

    # Every per-agent check passes. A failure here names the agent and the check.
    for agent, checks in res["agents"].items():
        for check in checks:
            assert check["passed"] == check["total"], (agent, check["name"])

    base = res["single_agent_baseline"]
    assert base["rules_single_agent"]["same_decision_as_multi_agent"] == f"{n} of {n}"
    assert base["model_single_agent"]["ran"] is False
    assert base["reach"]["multi_agent_max_for_text_exposed"] == 0 and base["reach"]["single_agent_count"] == 5

    pe = res["profile_effect"]
    assert pe["on"]["n"] == pe["off"]["n"] == n
    # The profile must not make missed fraud worse on the scenarios built to evade the thresholds.
    assert pe["on"]["recall"] >= pe["off"]["recall"] - 1e-9

    cal = res["calibration"]
    assert set(cal["what_if_hold_score"]) == {"45", "55", "65"}
    # Raising the hold score can only release more, never hold more.
    assert cal["what_if_hold_score"]["65"]["recall"] <= cal["what_if_hold_score"]["45"]["recall"] + 1e-9

    m = res["mcp"]
    if m["ran"]:
        assert m["same_decision_score_and_ledger"] == m["cases"] and m["same_tool_calls"] == m["cases"]
        assert m["money_tools_offered"] == [] and m["fallbacks_to_direct"] == 0

    inj = res["injection"]
    assert inj["decision_unchanged_and_clean"] == inj["total"]
