"""The Analytics agent's edges: exactly when a district counts as a hot spot or a riser, which day a payment belongs to,
and when a model's wording of the brief is used."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from test_analytics import COLOMBO, _empty_ledger, _pay

from app import analytics
from app.analytics import compute
from app.db import session_scope
from app.models import Transaction
from app.runtime import runtime
from app.simbook import BOOK_CUSTOMER, BOOK_VERSION
from app.util import nid, utcnow


@pytest.fixture(autouse=True)
def _clean_cache():
    analytics._brief_cache.clear()
    yield
    analytics._brief_cache.clear()


def fill(session, district, payments, flagged, age=15):
    for i in range(payments):
        _pay(session, district, age, "blocked" if i < flagged else "normal")


def hotspots(per_district):
    """Quiet Colombo as the national baseline, then each district in `per_district` as (payments, flagged)."""
    _empty_ledger()
    with session_scope() as session:
        fill(session, "Colombo", 400, 0)  # a quiet base, so only the districts below move the national rate
        for name, (payments, flagged) in per_district.items():
            fill(session, name, payments, flagged)
    return [h["district"] for h in compute()["hotspots"]]


# ------------------------------------------------------------------ hot spots

def test_a_district_needs_at_least_40_payments_to_be_a_hot_spot(boot):
    assert hotspots({"Kandy": (39, 20)}) == []
    assert hotspots({"Kandy": (40, 20)}) == ["Kandy"]


def test_a_district_needs_at_least_6_flagged_payments_to_be_a_hot_spot(boot):
    assert hotspots({"Kandy": (40, 5)}) == []
    assert hotspots({"Kandy": (40, 6)}) == ["Kandy"]


def test_a_district_must_run_at_least_one_and_a_half_times_the_national_rate(boot):
    # Kandy runs at 30%. With another district making the national rate exactly 20%, 30% is exactly 1.5 times it.
    _empty_ledger()
    with session_scope() as session:
        fill(session, "Kandy", 40, 12)
        fill(session, "Galle", 100, 16)
    out = compute()
    assert out["totals"]["flag_rate"] == 20.0
    assert [h["district"] for h in out["hotspots"]] == ["Kandy"]
    _empty_ledger()
    with session_scope() as session:
        fill(session, "Kandy", 40, 12)
        fill(session, "Galle", 100, 17)  # the national rate is now 20.7, and 1.5 times that is more than 30
    assert compute()["hotspots"] == []


def test_a_hot_spot_is_not_reported_when_the_national_rate_is_zero(boot):
    _empty_ledger()
    with session_scope() as session:
        fill(session, "Kandy", 80, 0)
    assert compute()["hotspots"] == []


def test_hot_spots_are_listed_worst_first_and_at_most_five(boot):
    _empty_ledger()
    names = ["Kandy", "Galle", "Matara", "Jaffna", "Kurunegala", "Badulla", "Ratnapura"]
    with session_scope() as session:
        fill(session, "Colombo", 2000, 0)
        for i, name in enumerate(names):
            fill(session, name, 50, 10 + i)
    spots = compute()["hotspots"]
    assert len(spots) == 5
    assert [s["multiple"] for s in spots] == sorted((s["multiple"] for s in spots), reverse=True)


# ------------------------------------------------------------------ risers

def riser(this_week, usual_payments=60, usual_flagged=0, week_payments=10):
    _empty_ledger()
    with session_scope() as session:
        fill(session, "Colombo", 200, 0)
        fill(session, "Kandy", usual_payments, usual_flagged, age=15)
        for i in range(week_payments):
            _pay(session, "Kandy", i % 7, "blocked" if i < this_week else "normal")
    return [r["district"] for r in compute()["rising"]]


def test_a_district_needs_at_least_4_flagged_payments_this_week_to_be_a_riser(boot):
    assert riser(3) == []
    assert riser(4) == ["Kandy"]


def test_a_noisy_district_is_not_a_riser_for_doing_what_it_usually_does(boot):
    # Usually 20% flagged. This week 4 of 10 flagged is 40%, expected 2.0; 2.5 times that is 5.0, so 4 is not enough.
    assert riser(4, usual_payments=50, usual_flagged=10) == []
    assert riser(5, usual_payments=50, usual_flagged=10) == ["Kandy"]


# ------------------------------------------------------------------ which day a payment belongs to

def test_a_payment_belongs_to_the_day_in_sri_lanka_not_the_day_in_utc(boot):
    _empty_ledger()
    local_noon_today = datetime.combine(utcnow().astimezone(COLOMBO).date(), datetime.min.time(), tzinfo=COLOMBO).replace(hour=12)
    # 00:10 in Colombo is 18:40 UTC the day before. It must count for the Colombo date, not the UTC date.
    just_after_local_midnight = datetime.combine(local_noon_today.date() - timedelta(days=1), datetime.min.time(), tzinfo=COLOMBO).replace(minute=10)
    assert just_after_local_midnight.astimezone(timezone.utc).date() != just_after_local_midnight.date()
    with session_scope() as session:
        session.add(Transaction(id=nid("t"), customer_id=BOOK_CUSTOMER, kind="transfer", amount_lkr=1000, payee="x", status="completed", occurred_at=just_after_local_midnight,
                                origin="simulated", detail={"district": "Kandy", "flagged": False, "v": BOOK_VERSION}))
    days = {d["date"]: d["payments"] for d in compute()["daily"]}
    assert days[just_after_local_midnight.date().isoformat()] == 1


def test_today_is_marked_partial_and_left_out_of_the_week_on_week_trend(boot):
    _empty_ledger()
    with session_scope() as session:
        for _ in range(5):
            _pay(session, "Kandy", 0, "blocked")  # today
        for _ in range(5):
            _pay(session, "Kandy", 3, "blocked")
        for _ in range(5):
            _pay(session, "Kandy", 10, "blocked")
    out = compute()
    assert out["daily"][-1]["partial"] is True and all(not d["partial"] for d in out["daily"][:-1])
    assert out["trend"] == {"last7_flagged": 5, "prev7_flagged": 5, "change_pct": 0}


def test_the_trend_is_a_percentage_change_and_unknown_when_there_was_nothing_before(boot):
    _empty_ledger()
    with session_scope() as session:
        for _ in range(3):
            _pay(session, "Kandy", 10, "blocked")
        for _ in range(6):
            _pay(session, "Kandy", 3, "blocked")
    assert compute()["trend"]["change_pct"] == 100
    _empty_ledger()
    with session_scope() as session:
        _pay(session, "Kandy", 3, "blocked")
    assert compute()["trend"]["change_pct"] is None


def test_the_figures_are_labelled_simulated_only_when_simulated_payments_are_in_them(boot):
    _empty_ledger()
    with session_scope() as session:
        _pay(session, "Kandy", 3, origin="demo", customer="cus_nimal")
    assert compute()["label"] == "recorded"
    with session_scope() as session:
        _pay(session, "Kandy", 3)
    assert compute()["label"] == "simulated"


# ------------------------------------------------------------------ the written brief

def model(monkeypatch, text, provider="meta", calls=None):
    import dataclasses

    monkeypatch.setattr(runtime, "settings", dataclasses.replace(runtime.settings, meta_api_key="test-key"))

    def complete(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        if isinstance(text, Exception):
            raise text
        return SimpleNamespace(text=text, provider=provider)

    monkeypatch.setattr(runtime.llm, "complete", complete)


def one_case_book():
    _empty_ledger()
    with session_scope() as session:
        fill(session, "Kandy", 10, 2)


def test_a_models_wording_is_used_when_every_number_in_it_is_in_the_facts(boot, monkeypatch):
    one_case_book()
    model(monkeypatch, "Of 10 payments in 30 days, 2 looked abnormal.")
    out = compute()
    assert out["summary_source"] == "meta" and out["summary"] == "Of 10 payments in 30 days, 2 looked abnormal."


@pytest.mark.parametrize("reply,why", [
    ("There were 11 abnormal payments.", "an invented number"), ("", "blank"), ("   \n ", "blank"),
])
def test_a_models_wording_is_dropped_for_an_invented_number_or_blank_text(boot, monkeypatch, reply, why):
    one_case_book()
    model(monkeypatch, reply)
    assert compute()["summary_source"] == "rules", why


def test_a_model_that_fails_or_is_the_built_in_stand_in_leaves_the_rules_text(boot, monkeypatch):
    one_case_book()
    model(monkeypatch, RuntimeError("down"))
    assert compute()["summary_source"] == "rules"
    analytics._brief_cache.clear()
    model(monkeypatch, "Of 10 payments, 2 looked abnormal.", provider="mock")
    assert compute()["summary_source"] == "rules"


def test_the_same_facts_are_not_sent_to_the_model_twice(boot, monkeypatch):
    one_case_book()
    calls = []
    model(monkeypatch, "Of 10 payments in 30 days, 2 looked abnormal.", calls=calls)
    compute()
    compute()
    assert len(calls) == 1


def test_the_model_is_only_given_counts_never_a_name_phone_or_account(boot, monkeypatch):
    one_case_book()
    calls = []
    model(monkeypatch, "Of 10 payments in 30 days, 2 looked abnormal.", calls=calls)
    compute()
    sent = calls[0]["user"] + calls[0]["system"]
    for forbidden in ("Nimal", "+94", "@", "8200", "cus_", "payee"):
        assert forbidden not in sent
    assert calls[0]["purpose"] == "analytics"
