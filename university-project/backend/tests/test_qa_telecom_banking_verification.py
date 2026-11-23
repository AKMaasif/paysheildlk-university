"""Telecom reads (SIM facts), Banking reads (ledger facts) and the small Verification helpers."""
import random
from datetime import timedelta

import pytest

from app import answers, facts, geo, knowledge, liveness
from app.db import session_scope
from app.models import SimEvent, Transaction
from app.util import nid, utcnow
from evaluation import harness
from evaluation.archetypes import Scenario


def _scenario(**kw):
    base = dict(archetype="benign", group="benign", fraud=False, amount=10_000, payee="Payee", new_payee=False)
    base.update(kw)
    return harness.seed_scenario(Scenario(**base))


def _add_event(phone, kind, hours, detail=None):
    with session_scope() as session:
        session.add(SimEvent(id=nid("sim"), phone=phone, event_type=kind, occurred_at=utcnow() - timedelta(hours=hours), detail=detail or {}, origin="t"))


# ------------------------------------------------------------------ Telecom: the CAMARA-shaped reads

@pytest.mark.parametrize("hours,window,swapped", [(239, 240, True), (239.9, 240, True), (241, 240, False), (1, 1, False), (0.5, 1, True)])
def test_check_sim_swap_window_edges(boot, hours, window, swapped):
    ctx = _scenario(swap_h=hours)
    out = facts.check_sim_swap(ctx["phone"], window)
    assert out["swapped"] is swapped and out["maxAge"] == window and set(out) == {"phoneNumber", "swapped", "maxAge", "latestSimChange"}


def test_check_sim_swap_defaults_to_ten_days_and_reports_no_swap_as_null(boot):
    ctx = _scenario()
    out = facts.check_sim_swap(ctx["phone"], None)
    assert out["maxAge"] == 240 and out["swapped"] is False and out["latestSimChange"] is None


@pytest.mark.parametrize("window", [0, -5, 2401, 10_000])
def test_check_sim_swap_refuses_a_window_outside_one_to_2400_hours(boot, window):
    ctx = _scenario()
    with pytest.raises(ValueError):
        facts.check_sim_swap(ctx["phone"], window)


def test_the_latest_swap_wins_and_the_date_is_returned(boot):
    ctx = _scenario(swap_h=100)
    _add_event(ctx["phone"], "sim_swap", 5)
    assert facts.check_sim_swap(ctx["phone"], 24)["swapped"] is True
    assert facts.retrieve_sim_swap_date(ctx["phone"])["latestSimChange"] is not None


@pytest.mark.parametrize("phone", ["", "abc", "12", "+1", "07712"])
def test_a_malformed_number_is_invalid_not_unknown(boot, phone):
    with pytest.raises(facts.InvalidPhone):
        facts.check_sim_swap(phone, 240)


def test_a_valid_number_with_no_customer_is_unknown(boot):
    with pytest.raises(facts.UnknownPhone):
        facts.check_sim_swap("+94700000999", 240)
    with pytest.raises(facts.UnknownPhone):
        facts.retrieve_sim_swap_date("+94700000999")


def test_local_formats_resolve_to_the_same_customer(boot):
    ctx = _scenario(swap_h=3)
    local = "0" + ctx["phone"][3:]
    assert facts.check_sim_swap(local, 24)["phoneNumber"] == ctx["phone"]


# ------------------------------------------------------------------ signals the Telecom and Banking reads feed

def test_signals_read_every_sim_event_and_the_payment(boot):
    ctx = _scenario(swap_h=2, imei_h=3, loc_h=4, port_out=True, amount=70_000, new_payee=True, payee="Mule")
    signals, context = facts.collect_signals(ctx["customer_id"], ctx["transaction_id"], use_profile=False)
    assert 1.5 < signals.sim_swap_age_hours < 2.5 and 2.5 < signals.new_imei_age_hours < 3.5 and 3.5 < signals.location_change_age_hours < 4.5
    assert signals.port_out and signals.new_payee and signals.amount_lkr == 70_000 and signals.swapped
    assert signals.location_note == "Negombo" and context["swapped"] == "yes" and context["payee"] == "Mule"


def test_a_cancelled_or_old_port_out_is_not_open(boot):
    from types import SimpleNamespace

    from app.facts import _open_port
    now = utcnow()
    assert _open_port(None, now) is False
    assert _open_port(SimpleNamespace(occurred_at=now - timedelta(hours=1), detail={}), now) is True
    assert _open_port(SimpleNamespace(occurred_at=now - timedelta(hours=1), detail={"cancelled": True}), now) is False
    assert _open_port(SimpleNamespace(occurred_at=now - timedelta(hours=1), detail={"status": "cancelled"}), now) is False
    assert _open_port(SimpleNamespace(occurred_at=now - timedelta(hours=24 * 7 + 1), detail={}), now) is False
    assert _open_port(SimpleNamespace(occurred_at=now - timedelta(hours=24 * 7 - 1), detail={}), now) is True


def test_a_swap_older_than_ten_days_is_not_flagged_as_swapped(boot):
    ctx = _scenario(swap_h=300)
    signals, context = facts.collect_signals(ctx["customer_id"], ctx["transaction_id"], use_profile=False)
    assert signals.swapped is False and context["swapped"] == "no"


def test_password_reset_and_new_device_login_come_from_the_ledger(boot):
    ctx = _scenario(reset_h=2)
    with session_scope() as session:
        session.add(Transaction(id=nid("tx"), customer_id=ctx["customer_id"], kind="login", amount_lkr=0, payee="", new_payee=False,
                                status="completed", occurred_at=utcnow() - timedelta(hours=1), origin="t", detail={"new_device": True}))
        session.add(Transaction(id=nid("tx"), customer_id=ctx["customer_id"], kind="login", amount_lkr=0, payee="", new_payee=False,
                                status="completed", occurred_at=utcnow() - timedelta(hours=40), origin="t", detail={"new_device": True}))
    signals, _ = facts.collect_signals(ctx["customer_id"], ctx["transaction_id"], use_profile=False)
    assert 1.5 < signals.password_reset_age_hours < 2.5 and signals.new_device_login is True


def test_an_old_new_device_login_does_not_count(boot):
    ctx = _scenario()
    with session_scope() as session:
        session.add(Transaction(id=nid("tx"), customer_id=ctx["customer_id"], kind="login", amount_lkr=0, payee="", new_payee=False,
                                status="completed", occurred_at=utcnow() - timedelta(hours=40), origin="t", detail={"new_device": True}))
    assert facts.collect_signals(ctx["customer_id"], ctx["transaction_id"], use_profile=False)[0].new_device_login is False


def test_missing_customer_or_payment_is_an_error_not_a_guess(boot):
    ctx = _scenario()
    with pytest.raises(LookupError):
        facts.collect_signals("nobody", ctx["transaction_id"])
    with pytest.raises(LookupError):
        facts.collect_signals(ctx["customer_id"], "TX-none")


def test_recent_transactions_are_newest_first_and_limited(boot):
    ctx = _scenario(history=[("A", 1000, 30), ("B", 2000, 20), ("C", 3000, 10)])
    rows = facts.recent_transactions(ctx["customer_id"], limit=3)
    assert len(rows) == 3
    times = [r["occurred_at"] for r in rows]
    assert times == sorted(times, reverse=True)
    assert set(rows[0]) == {"id", "kind", "amount_lkr", "payee", "payee_bank", "new_payee", "status", "occurred_at", "case_id"}
    assert facts.recent_transactions(ctx["customer_id"], limit=1).__len__() == 1


def test_one_customers_facts_never_include_another_customers_payments(boot):
    a = _scenario(payee="OnlyA")
    _scenario(payee="OnlyB")
    assert all(r["payee"] != "OnlyB" for r in facts.recent_transactions(a["customer_id"]))
    _, context = facts.collect_signals(a["customer_id"], a["transaction_id"], use_profile=False)
    assert all(t["payee"] != "OnlyB" for t in context["transactions"])


# ------------------------------------------------------------------ Banking: customer profile through the signals

def _history(n, amount=5_000):
    return [("Regular Payee", amount, 20 + i) for i in range(n)]


@pytest.mark.parametrize("count,familiar", [(1, False), (2, False), (3, True), (5, True)])
def test_a_payee_is_familiar_only_after_enough_history(boot, count, familiar):
    ctx = _scenario(swap_h=2, history=_history(count), payee="Regular Payee", amount=5_000)
    signals, _ = facts.collect_signals(ctx["customer_id"], ctx["transaction_id"], use_profile=True)
    assert signals.familiar_payment is familiar


def test_the_profile_can_be_switched_off(boot):
    ctx = _scenario(swap_h=2, history=_history(6), payee="Regular Payee", amount=5_000)
    on = facts.collect_signals(ctx["customer_id"], ctx["transaction_id"], use_profile=True)[0]
    off = facts.collect_signals(ctx["customer_id"], ctx["transaction_id"], use_profile=False)[0]
    assert off.familiar_payment is False and off.unusual_amount is False
    assert on.familiar_payment is True


def test_a_huge_amount_to_a_known_payee_is_not_called_familiar(boot):
    ctx = _scenario(swap_h=2, history=_history(6), payee="Regular Payee", amount=900_000)
    s = facts.collect_signals(ctx["customer_id"], ctx["transaction_id"], use_profile=True)[0]
    assert s.familiar_payment is False


# ------------------------------------------------------------------ Verification helpers: liveness (legacy), questions, answers

FRAME_A = random.Random(1).randbytes(2000)
FRAME_B = random.Random(2).randbytes(2000)


def test_legacy_liveness_passes_two_different_detailed_frames():
    out = liveness.analyse([FRAME_A, FRAME_B])
    assert out["verdict"] == "live" and out["image_sha256"]


@pytest.mark.parametrize("frames,why", [
    ([FRAME_A, FRAME_A], "identical"), ([FRAME_A], "two frames"), ([], "two frames"), (None, "two frames"),
    ([b"\x00" * 2000, FRAME_B], "flat"), ([FRAME_A[:500], FRAME_B[:500]], "too small"),
])
def test_legacy_liveness_rejects_stills_flat_small_and_missing(frames, why):
    out = liveness.analyse(frames)
    assert out["verdict"] == "fake" and any(why in r for r in out["reasons"])


def test_there_is_no_way_to_pass_a_verdict_in_only_frames_decide_and_empty_frames_are_ignored():
    import inspect

    assert list(inspect.signature(liveness.analyse).parameters) == ["frames"]
    with pytest.raises(TypeError):
        liveness.analyse(verdict="pass")
    assert liveness.analyse([]) ["verdict"] == "fake" and liveness.analyse([FRAME_A, FRAME_A])["verdict"] == "fake"
    assert liveness.analyse([b"", FRAME_A, b"", FRAME_B])["verdict"] == "live"


def test_the_liveness_digest_is_stable_for_the_same_frames():
    assert liveness.analyse([FRAME_A, FRAME_B])["image_sha256"] == liveness.analyse([FRAME_A, FRAME_B])["image_sha256"]
    assert liveness.analyse([FRAME_A, FRAME_B])["image_sha256"] != liveness.analyse([FRAME_B, FRAME_A])["image_sha256"]


def _questions(pepper="p"):
    return [{"id": "colour", "prompt": "c?", "hash": answers.hash_secret("Blue", "colour")},
            {"id": "dob", "prompt": "d?", "hash": answers.hash_secret("1985-03-04", "dob")},
            {"id": "pet", "prompt": "p?", "hash": answers.hash_secret("Rex", "pet")}]


def test_a_round_needs_every_answer_right_and_normalises_case_and_spaces():
    qs = _questions()
    assert knowledge.grade_round(qs, {"colour": "  BLUE ", "dob": "04/03/1985", "pet": "rex"}, "p") is True
    for wrong in ({"colour": "red", "dob": "04/03/1985", "pet": "rex"}, {"colour": "blue", "dob": "1990-01-01", "pet": "rex"}, {"colour": "blue", "dob": "04/03/1985"}):
        assert knowledge.grade_round(qs, wrong, "p") is False


@pytest.mark.parametrize("given", [None, {}, {"colour": ""}])
def test_missing_or_blank_answers_fail(given):
    assert knowledge.grade_round(_questions(), given, "p") is False


def test_a_round_with_no_questions_never_passes():
    assert knowledge.grade_round([], {"a": "b"}, "p") is False


def test_a_round_draws_no_more_than_asked_and_never_leaks_the_answer():
    qs = _questions()
    drawn = knowledge.draw_round(qs, 2, random.Random(1))
    assert len(drawn) == 2 and all(set(d) == {"id", "prompt"} for d in drawn)
    assert len(knowledge.draw_round(qs, 10)) == 3 and knowledge.draw_round([], 3) == []


@pytest.mark.parametrize("raw,expected", [("1985-03-04", "1985-03-04"), ("04/03/1985", "1985-03-04"), ("04-03-1985", "1985-03-04"),
                                          ("4 Mar 1985", "1985-03-04"), ("4 March 1985", "1985-03-04"), ("04031985", "1985-03-04"),
                                          ("not a date", "not a date"), ("", ""), (None, "")])
def test_birth_dates_in_common_formats_compare_equal(raw, expected):
    assert answers.normalise_dob(raw) == expected


def test_answers_are_salted_so_equal_answers_have_different_hashes():
    a, b = answers.hash_secret("blue", "colour"), answers.hash_secret("blue", "colour")
    assert a != b and a.startswith("$argon2id$") and answers.verify_secret("Blue ", a, "colour") and answers.verify_secret("blue", b, "colour")
    assert not answers.verify_secret("green", a, "colour")


@pytest.mark.parametrize("stored", ["", "plain", "a$b", "scrypt$00$00", "pbkdf2$zz$00", "pbkdf2$00$00$00"])
def test_a_malformed_stored_answer_never_verifies_and_never_crashes(stored):
    try:
        assert answers.verify_secret("x", stored, "colour") is False
    except ValueError:
        pytest.fail("malformed hash must not raise")


# ------------------------------------------------------------------ geo

def test_districts_map_to_provinces_and_unknowns_are_labelled():
    names = geo.district_names()
    assert len(names) == 25 and len(set(names)) == 25
    assert geo.province_of("Colombo") == "Western" and geo.province_of("Jaffna") == "Northern"
    assert geo.province_of("Atlantis") == "Unknown" and geo.province_of(None) == "Unknown"
    assert all(geo.province_of(n) != "Unknown" for n in names)
