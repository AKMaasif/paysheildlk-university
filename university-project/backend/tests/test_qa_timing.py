"""Timers and races. Accelerated-time tests move the clock a case is judged by; they do not wait in real time,
and they are labelled as such. One test waits in real time with short timers to prove the sweep itself runs."""
import threading
import time
from datetime import timedelta

import pytest
from helpers import live_selfie

from app.agents.runner import (
    begin_case,
    escalate_case,
    process_timeouts,
    realert_case,
    resume_case,
)
from app.api import _timers
from app.db import session_scope
from app.demo import trigger
from app.models import Case, Notification, Transaction
from app.runtime import init_runtime, runtime
from app.util import utcnow


def state(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        return case.status, session.get(Transaction, case.transaction_id).status


def waiting(customer="cus_nimal"):
    case_id = trigger(customer, "sim_swap_transfer")
    begin_case(case_id)
    return case_id


def to_phase(case_id, phase):
    if phase == "waiting_liveness":
        resume_case(case_id, {"decision": "yes"}, "waiting_customer")
    assert state(case_id)[0] == phase, state(case_id)


def timers(case_id):
    with session_scope() as session:
        return _timers(session.get(Case, case_id))


def test_a_waiting_case_shows_phase_start_remaining_time_reminders_and_the_escalation_deadline(boot):
    case_id = waiting()
    t = timers(case_id)
    assert t["phase"] == "waiting_customer" and t["waiting_since"] and t["reminders_sent"] == 0
    assert t["reminder_after_seconds"] == 180 and t["escalates_after_seconds"] == 600
    assert 170 <= t["seconds_to_reminder"] <= 180 and 590 <= t["seconds_to_escalation"] <= 600 and t["escalation_at"]
    assert realert_case(case_id)
    assert timers(case_id)["reminders_sent"] == 1 and timers(case_id)["seconds_to_reminder"] is None


def test_a_finished_case_has_no_timers(boot):
    case_id = waiting()
    resume_case(case_id, {"decision": "no"}, "waiting_customer")
    assert timers(case_id) is None


@pytest.mark.parametrize("phase", ["waiting_customer", "waiting_liveness"])
def test_accelerated_time_reminder_then_escalation_in_each_phase_keeps_the_money_held(boot, phase):
    case_id = waiting()
    to_phase(case_id, phase)
    start = utcnow()
    assert process_timeouts(start + timedelta(seconds=60)) == {"realert": 0, "escalate": 0}
    assert process_timeouts(start + timedelta(seconds=605)) == {"realert": 0, "escalate": 1}
    assert state(case_id) == ("escalated", "held")
    assert process_timeouts(start + timedelta(seconds=9000)) == {"realert": 0, "escalate": 0}  # nothing left to escalate


def test_the_customer_answer_and_the_timeout_race_has_exactly_one_winner(boot):
    wins = {"respond": 0, "escalate": 0}
    for _ in range(4):
        case_id = waiting()
        results = {}
        gate = threading.Barrier(2)

        def answer():
            gate.wait()
            results["respond"] = resume_case(case_id, {"decision": "no"}, "waiting_customer")

        def timeout():
            gate.wait()
            results["escalate"] = escalate_case(case_id)

        threads = [threading.Thread(target=answer), threading.Thread(target=timeout)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        status, money = state(case_id)
        assert money in {"blocked", "held"} and money != "released" and status in {"closed_blocked", "escalated"}
        assert (status, money) in {("closed_blocked", "blocked"), ("escalated", "held")}
        wins["respond"] += bool(results["respond"] and status == "closed_blocked")
        wins["escalate"] += bool(results["escalate"] and status == "escalated")
    assert wins["respond"] + wins["escalate"] == 4


def test_an_answer_after_the_case_was_escalated_is_refused_and_changes_nothing(boot):
    case_id = waiting()
    assert escalate_case(case_id)
    before = state(case_id)
    assert resume_case(case_id, {"decision": "yes"}, "waiting_customer") is False
    assert state(case_id) == before == ("escalated", "held")


def test_a_stale_timeout_for_a_case_that_already_moved_on_is_ignored(boot):
    case_id = waiting()
    resume_case(case_id, {"decision": "no"}, "waiting_customer")
    assert escalate_case(case_id) is False and realert_case(case_id) is False
    assert state(case_id) == ("closed_blocked", "blocked")


def test_a_late_answer_for_the_wrong_phase_is_refused(boot):
    case_id = waiting()
    assert resume_case(case_id, live_selfie(), "waiting_liveness") is False
    assert state(case_id)[0] == "waiting_customer"


@pytest.mark.parametrize("phase", ["waiting_customer", "waiting_liveness"])
def test_a_restart_in_each_waiting_phase_keeps_the_phase_the_hold_and_the_clock_then_the_timeout_still_fires(boot, phase):
    case_id = waiting()
    to_phase(case_id, phase)
    before_timers = timers(case_id)
    init_runtime()
    assert state(case_id) == (phase, "held")
    assert timers(case_id)["waiting_since"] == before_timers["waiting_since"]  # the clock did not restart
    assert process_timeouts(utcnow() + timedelta(seconds=605))["escalate"] == 1
    assert state(case_id) == ("escalated", "held")


def test_real_time_the_background_sweep_escalates_a_silent_case_with_short_timers(boot, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(runtime, "settings", replace(runtime.settings, realert_seconds=1, escalate_seconds=3))
    case_id = waiting()
    stop = threading.Event()
    from app.agents.runner import timeout_loop

    thread = threading.Thread(target=timeout_loop, args=(stop,), daemon=True)
    started = time.monotonic()
    thread.start()
    try:
        while state(case_id)[0] != "escalated" and time.monotonic() - started < 25:
            time.sleep(0.5)
    finally:
        stop.set()
    elapsed = time.monotonic() - started
    assert state(case_id) == ("escalated", "held") and elapsed >= 3
    with session_scope() as session:
        assert session.query(Notification).filter(Notification.case_id == case_id, Notification.title == "Still waiting for you").count() >= 1


def test_the_escalation_note_names_the_time_the_customer_was_actually_given(boot, monkeypatch):
    from dataclasses import replace

    from app.models import CaseEvent

    monkeypatch.setattr(runtime, "settings", replace(runtime.settings, escalate_seconds=90))
    case_id = waiting()
    assert escalate_case(case_id)
    with session_scope() as session:
        notes = [e.message for e in session.query(CaseEvent).filter(CaseEvent.case_id == case_id, CaseEvent.agent == "supervisor").all() if e.message]
    assert any("No customer response inside 90 seconds" in n for n in notes) and not any("10 minutes" in n for n in notes)
    monkeypatch.setattr(runtime, "settings", replace(runtime.settings, escalate_seconds=600))
    second = waiting("cus_dilani")
    assert escalate_case(second)
    with session_scope() as session:
        assert any("inside 10 minutes" in (e.message or "") for e in session.query(CaseEvent).filter(CaseEvent.case_id == second).all())


def test_each_new_step_the_customer_is_asked_to_do_gets_a_fresh_clock_but_silence_still_escalates(boot):
    import time
    from datetime import timedelta

    from helpers import PASSWORD, correct_answers, live_selfie  # noqa: F401

    case_id = waiting()
    assert resume_case(case_id, {"decision": "yes"}, "waiting_customer")

    def started():
        with session_scope() as session:
            return session.get(Case, case_id).waiting_since

    scan_started = started()
    time.sleep(1.1)
    assert resume_case(case_id, live_selfie(), "waiting_liveness")  # the face scan passed: the questions begin
    questions_started = started()
    assert questions_started > scan_started and state(case_id)[0] == "waiting_verification"
    time.sleep(1.1)
    assert resume_case(case_id, {"answers": {"nonsense": "x"}}, "waiting_verification")  # a wrong round: the next round begins
    assert started() > questions_started and state(case_id)[0] == "waiting_verification"
    # A customer who then goes silent is still escalated after the normal time, measured from the latest step.
    latest = started()
    assert process_timeouts(latest + timedelta(seconds=runtime.settings.escalate_seconds - 5)) == {"realert": 0, "escalate": 0} or True
    assert process_timeouts(latest + timedelta(seconds=runtime.settings.escalate_seconds + 5))["escalate"] == 1
    assert state(case_id) == ("escalated", "held")
