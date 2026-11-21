"""Risk (score) and Policy (decision): every edge in the rule table, and the properties that must always hold."""
import random

import pytest

from app import risk
from app.risk import (
    Signals,
    compute_risk_score,
    decide_policy,
    reset_thresholds,
    set_threshold,
    thresholds,
)


@pytest.fixture(autouse=True)
def _defaults():
    reset_thresholds()
    yield
    reset_thresholds()


def codes(signals):
    return {f["code"]: f["points"] for f in compute_risk_score(signals)[1]}


# ------------------------------------------------------------------ score: each factor at each edge

@pytest.mark.parametrize("age,code,points", [
    (0, "sim_swap_24h", 40), (24, "sim_swap_24h", 40), (24.01, "sim_swap_7d", 25), (168, "sim_swap_7d", 25),
    (168.01, "sim_swap_10d", 12), (240, "sim_swap_10d", 12), (240.01, None, 0), (239, "sim_swap_10d", 12), (241, None, 0),
])
def test_sim_swap_age_edges(age, code, points):
    got = codes(Signals(sim_swap_age_hours=age))
    assert (got == {code: points}) if code else got == {}


def test_no_swap_scores_nothing():
    assert compute_risk_score(Signals()) == (0, [])


@pytest.mark.parametrize("age,hit", [(72, True), (72.01, False), (0, True)])
def test_new_handset_window(age, hit):
    assert ("new_imei" in codes(Signals(new_imei_age_hours=age))) is hit


@pytest.mark.parametrize("age,hit", [(48, True), (48.01, False)])
def test_location_window_and_note(age, hit):
    assert ("location" in codes(Signals(location_change_age_hours=age))) is hit
    label = compute_risk_score(Signals(location_change_age_hours=1, location_note="Colombo to Jaffna"))[1][0]["label"]
    assert "Colombo to Jaffna" in label


@pytest.mark.parametrize("amount,code", [
    (49_999, None), (50_000, "amount_50k"), (99_999, "amount_50k"), (100_000, "amount_100k"),
    (499_999, "amount_100k"), (500_000, "amount_500k"), (10_000_000, "amount_500k"), (0, None),
])
def test_amount_bands(amount, code):
    got = codes(Signals(amount_lkr=amount))
    assert (list(got) == [code]) if code else got == {}


@pytest.mark.parametrize("age,hit", [(12, True), (12.01, False), (0, True)])
def test_password_reset_window(age, hit):
    assert ("password_reset" in codes(Signals(password_reset_age_hours=age))) is hit


@pytest.mark.parametrize("flag,code,points", [("port_out", "port_out", 35), ("new_payee", "new_payee", 15),
                                               ("new_device_login", "new_device_login", 10), ("unusual_amount", "unusual_amount", 10),
                                               ("familiar_payment", "familiar_payment", -25)])
def test_each_flag_adds_its_points(flag, code, points):
    assert codes(Signals(**{flag: True})).get(code) == points


def test_the_score_is_floored_at_zero_and_capped_at_one_hundred():
    assert compute_risk_score(Signals(familiar_payment=True))[0] == 0
    worst = Signals(sim_swap_age_hours=1, new_imei_age_hours=1, location_change_age_hours=1, port_out=True, new_payee=True,
                    amount_lkr=900_000, password_reset_age_hours=1, new_device_login=True, unusual_amount=True)
    assert compute_risk_score(worst)[0] == 100


def test_every_factor_has_a_code_a_label_and_points():
    worst = Signals(sim_swap_age_hours=1, new_imei_age_hours=1, location_change_age_hours=1, port_out=True, new_payee=True,
                    amount_lkr=900_000, password_reset_age_hours=1, new_device_login=True)
    for f in compute_risk_score(worst)[1]:
        assert set(f) == {"code", "label", "points"} and f["label"]


# ------------------------------------------------------------------ properties

WARNINGS = [dict(port_out=True), dict(new_payee=True), dict(new_device_login=True), dict(unusual_amount=True),
            dict(new_imei_age_hours=5), dict(location_change_age_hours=5), dict(password_reset_age_hours=3),
            dict(sim_swap_age_hours=5), dict(amount_lkr=600_000)]


def test_adding_a_warning_sign_never_lowers_the_score():
    rng = random.Random(7)
    for _ in range(300):
        base = {}
        for w in rng.sample(WARNINGS, rng.randint(0, 5)):
            base.update(w)
        extra = rng.choice(WARNINGS)
        assert compute_risk_score(Signals(**{**base, **extra}))[0] >= compute_risk_score(Signals(**base))[0]


def test_the_score_is_deterministic_and_always_within_bounds():
    rng = random.Random(11)
    for _ in range(300):
        s = Signals(sim_swap_age_hours=rng.choice([None, rng.uniform(0, 400)]), amount_lkr=rng.randint(0, 900_000),
                    port_out=rng.random() < .3, new_payee=rng.random() < .5, familiar_payment=rng.random() < .3,
                    new_device_login=rng.random() < .3, password_reset_age_hours=rng.choice([None, rng.uniform(0, 30)]))
        a, b = compute_risk_score(s), compute_risk_score(s)
        assert a == b and 0 <= a[0] <= 100


def test_a_decision_is_always_one_of_three_and_blocks_only_by_the_critical_rule():
    rng = random.Random(3)
    for _ in range(400):
        s = Signals(sim_swap_age_hours=rng.choice([None, rng.uniform(0, 400)]), amount_lkr=rng.choice([0, 49_999, 50_000, 499_999, 500_000, 900_000]),
                    port_out=rng.random() < .5, new_payee=rng.random() < .5, new_device_login=rng.random() < .3,
                    password_reset_age_hours=rng.choice([None, 1, 6, 6.01, 12, 40]))
        r = decide_policy(s)
        assert r.decision in {"release", "hold", "block"} and r.reason
        critical = s.port_out and s.new_payee and s.password_reset_age_hours is not None and s.password_reset_age_hours <= 6 and s.amount_lkr >= 500_000
        assert (r.decision == "block") is critical


# ------------------------------------------------------------------ policy edges

CRIT = dict(port_out=True, new_payee=True, password_reset_age_hours=6, amount_lkr=500_000)


def test_the_critical_rule_blocks_at_its_exact_edges():
    assert decide_policy(Signals(**CRIT)).decision == "block"


@pytest.mark.parametrize("change", [dict(port_out=False), dict(new_payee=False), dict(password_reset_age_hours=6.01),
                                    dict(password_reset_age_hours=None), dict(amount_lkr=499_999)])
def test_losing_any_one_critical_condition_stops_the_block_but_still_holds(change):
    r = decide_policy(Signals(**{**CRIT, **change}))
    assert r.decision == "hold"


@pytest.mark.parametrize("swap,amount,extra,expected", [
    (72, 50_000, dict(new_payee=True), "hold"),
    (72.01, 50_000, dict(new_device_login=True), "release"),
    (72, 49_999, dict(new_payee=True), "release"),
    (72, 50_000, dict(), "release"),
    (72, 50_000, dict(port_out=True), "hold"),
    (72, 50_000, dict(new_device_login=True), "hold"),
    (72, 50_000, dict(password_reset_age_hours=12), "hold"),
    (72, 50_000, dict(password_reset_age_hours=12.01), "release"),
    (None, 50_000, dict(new_payee=True), "release"),
])
def test_the_correlation_rule_edges(swap, amount, extra, expected):
    assert decide_policy(Signals(sim_swap_age_hours=swap, amount_lkr=amount, **extra)).decision == expected


@pytest.mark.parametrize("threshold,expected", [(35, "hold"), (45, "hold"), (46, "release"), (70, "release")])
def test_the_hold_threshold_edge_at_a_score_of_exactly_45(threshold, expected):
    s = Signals(sim_swap_age_hours=100, amount_lkr=100_000)  # 25 + 20, no correlated sign
    assert compute_risk_score(s)[0] == 45
    set_threshold("hold_score", threshold)
    assert decide_policy(s).decision == expected


def test_a_score_just_below_the_threshold_releases():
    s = Signals(sim_swap_age_hours=200, new_imei_age_hours=1, location_change_age_hours=1)  # 12 + 15 + 10 = 37
    assert compute_risk_score(s)[0] == 37 and decide_policy(s).decision == "release"
    s2 = Signals(sim_swap_age_hours=100, new_imei_age_hours=1, location_change_age_hours=1)  # 25 + 15 + 10 = 50
    assert decide_policy(s2).decision == "hold"
    s3 = Signals(sim_swap_age_hours=200, new_imei_age_hours=1, location_change_age_hours=1, new_device_login=True)  # 47
    assert decide_policy(s3).decision == "hold"
    s4 = Signals(sim_swap_age_hours=300, amount_lkr=100_000, new_device_login=True, location_change_age_hours=1)  # 0+20+10+10 = 40
    assert compute_risk_score(s4)[0] == 40 and decide_policy(s4).decision == "release"


def test_a_familiar_payment_brings_a_borderline_case_under_the_line_but_never_past_a_correlation_hold():
    base = dict(sim_swap_age_hours=100, amount_lkr=100_000)  # 45
    assert decide_policy(Signals(**base)).decision == "hold"
    assert decide_policy(Signals(**base, familiar_payment=True)).decision == "release"
    corr = Signals(sim_swap_age_hours=10, amount_lkr=60_000, new_payee=True, familiar_payment=True)
    assert decide_policy(corr).decision == "hold"


# ------------------------------------------------------------------ thresholds

def test_thresholds_stay_inside_their_safe_range():
    for bad in (34, 71, -1, 1000):
        with pytest.raises(ValueError):
            set_threshold("hold_score", bad)
    assert thresholds() == {"hold_score": 45}
    set_threshold("hold_score", 35); assert thresholds()["hold_score"] == 35
    set_threshold("hold_score", 70); assert thresholds()["hold_score"] == 70
    with pytest.raises(KeyError):
        set_threshold("block_score", 50)
    reset_thresholds(); assert thresholds() == risk.DEFAULTS


def test_thresholds_returns_a_copy():
    t = thresholds(); t["hold_score"] = 1
    assert thresholds()["hold_score"] == 45


def test_a_stricter_threshold_never_releases_what_the_default_holds_and_never_touches_a_block():
    rng = random.Random(5)
    for _ in range(200):
        s = Signals(sim_swap_age_hours=rng.choice([None, rng.uniform(0, 300)]), amount_lkr=rng.randint(0, 700_000),
                    port_out=rng.random() < .3, new_payee=rng.random() < .4, new_device_login=rng.random() < .3,
                    password_reset_age_hours=rng.choice([None, 2, 20]))
        reset_thresholds(); d = decide_policy(s).decision
        set_threshold("hold_score", 35); strict = decide_policy(s).decision
        set_threshold("hold_score", 70); loose = decide_policy(s).decision
        order = {"release": 0, "hold": 1, "block": 2}
        assert order[strict] >= order[d] >= order[loose]
        assert (d == "block") == (strict == "block") == (loose == "block")
