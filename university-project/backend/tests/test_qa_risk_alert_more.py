"""Risk: the score's contract with the screens and reports. Alert: what the reminder and the hand-over to a person say."""
import random
import re

import pytest

from app.agents.runner import begin_case, escalate_case, realert_case, resume_case
from app.db import session_scope
from app.demo import trigger
from app.facts import collect_signals
from app.models import Case, Notification, Transaction
from app.risk import Signals, compute_risk_score

ALL_CODES = {"sim_swap_24h", "sim_swap_7d", "sim_swap_10d", "new_imei", "location", "port_out", "new_payee", "amount_500k", "amount_100k", "amount_50k",
             "password_reset", "new_device_login", "unusual_amount", "familiar_payment"}


def everything(**over):
    base = dict(sim_swap_age_hours=1, new_imei_age_hours=1, location_change_age_hours=1, port_out=True, new_payee=True, amount_lkr=600_000, password_reset_age_hours=1,
                new_device_login=True, unusual_amount=True, familiar_payment=True)
    base.update(over)
    return Signals(**base)


# ------------------------------------------------------------------ the score's contract

def test_the_set_of_factor_codes_is_the_one_the_screens_and_reports_know():
    seen = set()
    for swap in (1, 100, 200):
        for amount in (50_000, 100_000, 500_000):
            seen |= {f["code"] for f in compute_risk_score(everything(sim_swap_age_hours=swap, amount_lkr=amount))[1]}
    assert seen == ALL_CODES


def test_the_factor_points_add_up_to_the_score_before_the_floor_and_the_cap():
    rng = random.Random(4)
    for _ in range(300):
        s = Signals(sim_swap_age_hours=rng.choice([None, rng.uniform(0, 300)]), new_imei_age_hours=rng.choice([None, rng.uniform(0, 100)]), amount_lkr=rng.randint(0, 700_000),
                    port_out=rng.random() < 0.3, new_payee=rng.random() < 0.5, familiar_payment=rng.random() < 0.3, unusual_amount=rng.random() < 0.3,
                    password_reset_age_hours=rng.choice([None, rng.uniform(0, 30)]), new_device_login=rng.random() < 0.3)
        score, factors = compute_risk_score(s)
        assert score == max(0, min(100, sum(f["points"] for f in factors)))


def test_fields_that_only_describe_the_case_never_change_the_score():
    a = compute_risk_score(Signals(sim_swap_age_hours=5, amount_lkr=60_000))[0]
    b = compute_risk_score(Signals(sim_swap_age_hours=5, amount_lkr=60_000, latest_sim_change="2026-01-01T00:00:00Z", swapped=True))[0]
    assert a == b


def test_every_factor_label_is_a_plain_sentence_and_unique_per_signal():
    labels = [f["label"] for f in compute_risk_score(everything())[1]]
    assert len(labels) == len(set(labels)) and all(len(label) > 10 and label[0].isupper() and not label.endswith(".") for label in labels)


@pytest.mark.parametrize("scenario", ["sim_swap_transfer", "port_out_critical", "routine_payment"])
def test_the_score_the_case_records_is_the_score_of_the_signals_it_read(boot, scenario):
    case_id = trigger("cus_nimal", scenario)
    begin_case(case_id)
    with session_scope() as session:
        case = session.get(Case, case_id)
        customer_id, tx_id, recorded, factors = case.customer_id, case.transaction_id, case.risk_score, case.risk_factors
    signals, _context = collect_signals(customer_id, tx_id)
    score, expected = compute_risk_score(signals)
    assert recorded == score and [f["label"] for f in factors] == [f["label"] for f in expected]


# ------------------------------------------------------------------ the reminder

def notes(case_id, title):
    with session_scope() as session:
        return [(n.channel, n.body, n.user_id) for n in session.query(Notification).filter(Notification.case_id == case_id, Notification.title == title).all()]


@pytest.fixture()
def waiting(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    return case_id


def test_a_reminder_counts_itself_and_goes_only_to_that_customer(waiting):
    with session_scope() as session:
        before = session.get(Case, waiting).alert_count
    assert realert_case(waiting) is True
    with session_scope() as session:
        case = session.get(Case, waiting)
        assert case.alert_count == before + 1 and case.last_alert_at is not None and case.status == "waiting_customer"
    sent = notes(waiting, "Still waiting for you")
    assert {c for c, _b, _u in sent} >= {"push", "email"} and {u for _c, _b, u in sent} == {"cus_nimal"}


def test_a_reminder_names_the_amount_and_payee_and_never_carries_a_link_or_a_code(waiting):
    realert_case(waiting)
    for _channel, body, _user in notes(waiting, "Still waiting for you"):
        assert "Rs. 200,000" in body and "K.D. Enterprises" in body
        assert not re.search(r"https?://|www\.|\b\d{4,8}\b", body.replace("200,000", ""))


def test_the_reminder_during_the_questions_asks_for_the_answers_not_a_yes_or_no(waiting):
    resume_case(waiting, {"decision": "yes"}, "waiting_customer")
    with session_scope() as session:
        status = session.get(Case, waiting).status
    assert status in {"waiting_liveness", "waiting_verification"}
    realert_case(waiting)
    body = notes(waiting, "Still waiting for you")[0][1]
    assert "security answers" in body or "yes or no" in body


def test_a_reminder_is_refused_for_a_case_that_is_not_waiting(boot):
    case_id = trigger("cus_nimal", "routine_payment")
    begin_case(case_id)
    assert realert_case(case_id) is False and escalate_case(case_id) is False


# ------------------------------------------------------------------ the hand-over to a person

def test_when_the_customer_stays_silent_the_money_stays_held_and_they_are_told_a_person_has_it(waiting):
    assert escalate_case(waiting) is True
    with session_scope() as session:
        case = session.get(Case, waiting)
        assert case.status == "escalated" and session.get(Transaction, case.transaction_id).status == "held" and case.pending_prompt is None
    bodies = " ".join(b for _c, b, _u in notes(waiting, "Payment still on hold")).lower()
    assert "still held" in bodies and "a person on the fraud desk has the case" in bodies
    assert "http" not in bodies


def test_a_case_cannot_be_escalated_twice(waiting):
    assert escalate_case(waiting) is True and escalate_case(waiting) is False
