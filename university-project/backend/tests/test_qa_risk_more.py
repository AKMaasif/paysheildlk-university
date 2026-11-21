"""More of the score's contract: odd inputs, purity, direction of change, and what the policy layer takes from it."""
import copy

import pytest

from app.risk import (
    PolicyResult,
    Signals,
    compute_risk_score,
    decide_policy,
    reset_thresholds,
)


@pytest.fixture(autouse=True)
def _defaults():
    reset_thresholds()
    yield
    reset_thresholds()


def test_a_quiet_case_scores_zero_with_no_factors():
    assert compute_risk_score(Signals()) == (0, [])


def test_the_score_calculation_does_not_change_what_it_is_given():
    s = Signals(sim_swap_age_hours=3, amount_lkr=120_000, new_payee=True, location_note="Colombo to Negombo", location_change_age_hours=2)
    before = copy.deepcopy(s)
    compute_risk_score(s)
    decide_policy(s)
    assert s == before


def test_an_age_of_zero_counts_but_no_age_at_all_does_not():
    assert compute_risk_score(Signals(sim_swap_age_hours=0))[0] == 40
    assert compute_risk_score(Signals(sim_swap_age_hours=None))[0] == 0


def test_an_event_dated_in_the_future_is_treated_as_fresh_not_ignored():
    """A clock that is slightly ahead must make the case look newer, never hide a SIM change."""
    assert compute_risk_score(Signals(sim_swap_age_hours=-2))[0] == 40
    assert compute_risk_score(Signals(new_imei_age_hours=-1))[0] == 15


@pytest.mark.parametrize("amount", [0, -1, -500_000, 49_999])
def test_a_zero_or_negative_amount_adds_no_amount_factor(amount):
    assert not [f for f in compute_risk_score(Signals(amount_lkr=amount))[1] if f["code"].startswith("amount")]


def test_an_enormous_amount_is_handled_and_capped():
    score, factors = compute_risk_score(Signals(amount_lkr=10**18))
    assert score == 25 and factors[0]["code"] == "amount_500k"


def test_a_larger_amount_never_gives_a_lower_score():
    scores = [compute_risk_score(Signals(sim_swap_age_hours=5, amount_lkr=a))[0] for a in range(0, 700_000, 5_000)]
    assert scores == sorted(scores)


def test_an_older_sim_swap_never_gives_a_higher_score():
    scores = [compute_risk_score(Signals(sim_swap_age_hours=h / 2))[0] for h in range(0, 800)]
    assert scores == sorted(scores, reverse=True)


def test_the_policy_result_carries_exactly_the_factors_and_score_of_the_calculation():
    s = Signals(sim_swap_age_hours=30, new_imei_age_hours=2, amount_lkr=75_000)
    score, factors = compute_risk_score(s)
    result = decide_policy(s)
    assert isinstance(result, PolicyResult) and result.score == score and result.factors == factors


@pytest.mark.parametrize("signals", [
    Signals(),
    Signals(sim_swap_age_hours=10, amount_lkr=80_000, new_payee=True),
    Signals(port_out=True, new_payee=True, password_reset_age_hours=1, amount_lkr=900_000),
])
def test_every_decision_explains_itself_in_a_sentence_that_starts_with_rule(signals):
    reason = decide_policy(signals).reason
    assert reason.startswith("Rule:") and reason.endswith(".") and len(reason) > 40


def test_a_blocked_payment_says_it_is_blocked_without_waiting_and_a_hold_says_it_waits_for_the_customer():
    blocked = decide_policy(Signals(port_out=True, new_payee=True, password_reset_age_hours=1, amount_lkr=900_000))
    held = decide_policy(Signals(sim_swap_age_hours=10, amount_lkr=80_000, new_payee=True))
    assert blocked.decision == "block" and "without waiting" in blocked.reason
    assert held.decision == "hold" and "customer answers" in held.reason


def test_the_location_factor_names_the_place_only_when_there_is_one():
    plain = [f for f in compute_risk_score(Signals(location_change_age_hours=1))[1] if f["code"] == "location"][0]["label"]
    named = [f for f in compute_risk_score(Signals(location_change_age_hours=1, location_note="Colombo to Jaffna"))[1] if f["code"] == "location"][0]["label"]
    assert "(" not in plain and named.endswith("(Colombo to Jaffna)")
