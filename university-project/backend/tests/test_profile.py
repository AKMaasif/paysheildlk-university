"""The customer profile: usual payees and amounts from the customer's own ledger. It lowers the score for
familiar behaviour and raises it for an unusually large amount after a SIM change, and never touches the
critical block rule or the swap-plus-warning hold rule."""
import random

from app.db import session_scope
from app.facts import collect_signals
from app.profile import build_profile, judge
from app.report_pdf import build_case_pdf
from app.risk import Signals, compute_risk_score, decide_policy
from app.util import utcnow
from evaluation import harness
from evaluation.archetypes import Scenario, legit_new_phone, routine_bill


def _sc(**kw):
    base = dict(archetype="t", group="benign", fraud=False, amount=10_000, payee="Landlord", new_payee=False)
    base.update(kw)
    return Scenario(**base)


def _history(payee, n=3, amount=10_000, first_days=40):
    return [(payee, amount, first_days - i * 5) for i in range(n)]


def _signals(sc, use_profile=True):
    ctx = harness.seed_scenario(sc)
    signals, _ = collect_signals(ctx["customer_id"], ctx["transaction_id"], use_profile=use_profile)
    return signals, ctx


def test_familiar_payee_and_usual_amount_lowers_the_score(boot):
    sc = _sc(swap_h=20, imei_h=19, history=_history("Landlord"))
    with_profile, _ = _signals(sc)
    without, _ = _signals(sc, use_profile=False)
    assert with_profile.familiar_payment and not without.familiar_payment
    assert compute_risk_score(without)[0] == 55 and decide_policy(without).decision == "hold"
    assert compute_risk_score(with_profile)[0] == 30 and decide_policy(with_profile).decision == "release"


def test_a_payee_added_days_ago_is_not_established(boot):
    # One payment, three days ago: the pattern of a mule added just before the attack.
    sc = _sc(swap_h=20, imei_h=19, new_payee=False, history=[("Mule", 3_000, 3.0)] + _history("Other", 3))
    signals, _ = _signals(sc)
    assert not signals.familiar_payment
    # Two payments but both recent is still not enough.
    sc = _sc(swap_h=20, history=[("Mule", 9_000, 9.0), ("Mule", 9_500, 3.0)] + _history("Other", 3))
    assert not _signals(sc)[0].familiar_payment


def test_too_little_history_changes_nothing(boot):
    sc = _sc(swap_h=20, imei_h=19, history=_history("Landlord", n=2))
    signals, _ = _signals(sc)
    assert not signals.familiar_payment and not signals.unusual_amount


def test_new_payee_flag_blocks_the_discount(boot):
    sc = _sc(swap_h=20, new_payee=True, history=_history("Landlord"))
    assert not _signals(sc)[0].familiar_payment


def test_an_unusual_amount_after_a_swap_adds_points_but_only_then(boot):
    history = _history("Landlord", n=6, amount=10_000)
    big = _sc(swap_h=100, amount=45_000, new_payee=True, history=history)
    assert _signals(big)[0].unusual_amount
    assert not _signals(_sc(swap_h=None, amount=45_000, new_payee=True, history=history))[0].unusual_amount  # no swap
    assert not _signals(_sc(swap_h=100, amount=15_000, new_payee=True, history=history))[0].unusual_amount  # not large
    assert not _signals(_sc(swap_h=100, amount=45_000, new_payee=True, history=history[:3]))[0].unusual_amount  # too little history


def test_the_profile_never_overrides_the_critical_or_swap_plus_warning_rules():
    critical = Signals(port_out=True, new_payee=True, password_reset_age_hours=5, amount_lkr=500_000, familiar_payment=True)
    assert decide_policy(critical).decision == "block"
    correlated = Signals(sim_swap_age_hours=10, amount_lkr=60_000, new_device_login=True, familiar_payment=True)
    assert decide_policy(correlated).decision == "hold"


def test_score_never_goes_below_zero():
    assert compute_risk_score(Signals(familiar_payment=True))[0] == 0


def test_the_four_demo_scenarios_keep_their_outcomes(boot):
    from app.agents.runner import begin_case
    from app.demo import trigger
    from app.models import Case

    # A fresh customer for each scenario: a scenario leaves SIM events behind that would colour the next one.
    expected = {"sim_swap_transfer": "hold", "port_out_critical": "block", "large_payment": "release", "routine_payment": "release"}
    for scenario, decision in expected.items():
        customer = harness.seed_scenario(_sc())["customer_id"]
        case_id = trigger(customer, scenario)
        begin_case(case_id)
        with session_scope() as session:
            assert session.get(Case, case_id).policy_decision == decision, scenario


def test_end_to_end_a_new_phone_customer_is_no_longer_held(boot):
    rng = random.Random(2)
    decisions = [harness.run_case(legit_new_phone(rng))["decision"] for _ in range(8)]
    assert decisions.count("release") >= 7


def test_banking_reads_the_profile_and_says_so(boot):
    run = harness.run_case(routine_bill(random.Random(4)))
    assert any(e[0] == "banking" and e[1] == "get_customer_profile" for e in run["events"])


def test_reports_handle_a_negative_factor(boot):
    run = harness.run_case(legit_new_phone(random.Random(6)))
    from app.agents import tools

    tools.generate_report(run["case_id"])
    assert build_case_pdf(run["case_id"], "analyst").startswith(b"%PDF")
    assert build_case_pdf(run["case_id"], "customer").startswith(b"%PDF")


def test_profile_figures(boot):
    ctx = harness.seed_scenario(_sc(history=[("A", 1000, 30), ("A", 2000, 20), ("B", 3000, 10), ("B", 4000, 5), ("C", 50_000, 2)]))
    with session_scope() as session:
        profile = build_profile(session, ctx["customer_id"], utcnow(), exclude_transaction_id=ctx["transaction_id"])
    assert profile.payments == 5 and profile.median_lkr == 3000
    assert profile.established("A", utcnow()) and not profile.established("C", utcnow())
    assert judge(profile, "A", 1000, False, False, utcnow())["familiar_payment"] is True
