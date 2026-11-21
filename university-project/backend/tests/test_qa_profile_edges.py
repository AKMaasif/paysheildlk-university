"""The customer profile's exact thresholds, and how it is built from the ledger."""
from datetime import timedelta

import pytest

from app import profile as p
from app.db import session_scope
from app.models import Transaction
from app.profile import Profile, build_profile, judge
from app.util import nid, utcnow


def profile(payments=10, high=10_000, payee="Shop", count=2, days=20):
    return Profile(payments=payments, usual_high_lkr=high, median_lkr=high // 2, payees={payee: {"count": count, "first_days_ago": days}} if payee else {})


NOW = utcnow()


# ------------------------------------------------------------------ when a payee counts as established

@pytest.mark.parametrize("count,days,expected", [(2, 14, True), (2, 13.99, False), (1, 60, False), (3, 14, True), (0, 99, False)])
def test_a_payee_is_established_after_14_days_and_two_payments(count, days, expected):
    assert profile(count=count, days=days).established("Shop", NOW) is expected


def test_an_unknown_blank_or_missing_payee_is_not_established():
    assert not profile().established("Someone else", NOW) and not profile().established("", NOW) and not profile().established(None, NOW)


# ------------------------------------------------------------------ the discount for a familiar payment

@pytest.mark.parametrize("payments,expected", [(2, False), (3, True), (4, True)])
def test_the_discount_needs_at_least_three_earlier_payments(payments, expected):
    assert judge(profile(payments=payments), "Shop", 5_000, False, False, NOW)["familiar_payment"] is expected


@pytest.mark.parametrize("amount,expected", [(12_500, True), (12_501, False), (1, True)])
def test_the_discount_holds_up_to_one_and_a_quarter_times_the_usual_high(amount, expected):
    assert judge(profile(high=10_000), "Shop", amount, False, False, NOW)["familiar_payment"] is expected


def test_a_payment_flagged_as_a_new_payee_never_gets_the_discount_even_to_a_known_name():
    assert judge(profile(), "Shop", 5_000, True, False, NOW)["familiar_payment"] is False


def test_the_discount_for_a_zero_usual_high_is_treated_as_one_rupee():
    assert judge(profile(high=0), "Shop", 1, False, False, NOW)["familiar_payment"] is True
    assert judge(profile(high=0), "Shop", 2, False, False, NOW)["familiar_payment"] is False


# ------------------------------------------------------------------ the extra points for an unusual amount

@pytest.mark.parametrize("payments,expected", [(4, False), (5, True), (6, True)])
def test_the_extra_points_need_at_least_five_earlier_payments(payments, expected):
    assert judge(profile(payments=payments, high=10_000), None, 25_000, False, True, NOW)["unusual_amount"] is expected


@pytest.mark.parametrize("amount,expected", [(20_000, False), (20_001, True), (30_000, True)])
def test_an_amount_is_unusual_only_above_twice_the_usual_high(amount, expected):
    assert judge(profile(high=10_000), None, amount, False, True, NOW)["unusual_amount"] is expected


def test_a_small_amount_is_never_unusual_however_small_the_usual_high_is():
    assert judge(profile(high=1_000), None, 19_999, False, True, NOW)["unusual_amount"] is False
    assert judge(profile(high=1_000), None, 20_000, False, True, NOW)["unusual_amount"] is True


def test_the_extra_points_apply_only_shortly_after_a_sim_change():
    assert judge(profile(), None, 90_000, False, False, NOW)["unusual_amount"] is False


# ------------------------------------------------------------------ building it from the ledger

def pay(session, customer, amount, days_ago, payee="Shop", kind="transfer", status="completed"):
    session.add(Transaction(id=nid("tx"), customer_id=customer, kind=kind, amount_lkr=amount, payee=payee, new_payee=False, status=status, occurred_at=NOW - timedelta(days=days_ago), origin="test"))


def fresh_customer():
    from evaluation import harness
    from evaluation.archetypes import Scenario

    return harness.seed_scenario(Scenario("t", "benign", False, 1_000, "Nobody", False))["customer_id"]


def test_only_completed_or_released_spending_in_the_last_180_days_counts(boot):
    customer = fresh_customer()
    with session_scope() as session:
        pay(session, customer, 1_000, 5, status="completed")
        pay(session, customer, 3_000, 6, status="released")
        for status in ("held", "blocked", "pending"):
            pay(session, customer, 90_000, 5, status=status)
        pay(session, customer, 90_000, 5, kind="login")
        pay(session, customer, 90_000, 5, kind="password_reset")
        pay(session, customer, 90_000, 181)
        session.flush()
        built = build_profile(session, customer, NOW)
    assert built.payments == 2 and built.usual_high_lkr == 3_000


def test_the_payment_being_judged_is_left_out_of_its_own_history(boot):
    customer = fresh_customer()
    with session_scope() as session:
        pay(session, customer, 500, 3)
        pay(session, customer, 7_000, 0.001, payee="New")
        session.flush()
        row_id = session.query(Transaction).filter(Transaction.customer_id == customer, Transaction.payee == "New").one().id
        with_it = build_profile(session, customer, NOW)
        without = build_profile(session, customer, NOW, exclude_transaction_id=row_id)
    assert (with_it.payments, without.payments) == (2, 1) and "New" not in without.payees and without.usual_high_lkr == 500


def test_a_payment_in_the_future_is_not_history(boot):
    customer = fresh_customer()
    with session_scope() as session:
        pay(session, customer, 9_999, -2)
        session.flush()
        assert build_profile(session, customer, NOW).payments == 0


def test_a_payee_s_first_payment_age_and_count_are_taken_from_the_ledger(boot):
    customer = fresh_customer()
    with session_scope() as session:
        for days in (40, 20, 3):
            pay(session, customer, 2_000, days, payee="Pharmacy")
        session.flush()
        info = build_profile(session, customer, NOW).payees["Pharmacy"]
    assert info["count"] == 3 and 39.9 < info["first_days_ago"] < 40.1


def test_the_usual_high_is_the_90th_percentile_and_the_median_the_50th():
    assert p._percentile([], 0.9) == 0
    values = list(range(1, 11))
    assert p._percentile(values, 0.9) == 9 and p._percentile(values, 0.5) in (5, 6)
    assert p._percentile([7], 0.9) == 7 and p._percentile([3, 1, 2], 0.5) == 2


def test_a_customer_with_no_history_has_an_empty_profile(boot):
    with session_scope() as session:
        empty = build_profile(session, "no-such-customer", NOW)
    assert (empty.payments, empty.usual_high_lkr, empty.payees) == (0, 0, {})
