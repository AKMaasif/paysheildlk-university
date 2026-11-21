"""Telecom events that arrive while cases are open: duplicates, stale, future, unknown, out of order, and no leakage."""
from datetime import timedelta

from app.agents.runner import begin_case
from app.db import session_scope
from app.demo import trigger
from app.ingest import ingest
from app.models import Case, CaseEvent, SimEvent, Transaction, User
from app.util import utcnow


def phone(customer):
    with session_scope() as session:
        return session.get(User, customer).phone


def waiting(customer="cus_nimal", scenario="sim_swap_transfer"):
    case_id = trigger(customer, scenario)
    begin_case(case_id)
    return case_id


def status(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        return case.status, session.get(Transaction, case.transaction_id).status


def when(hours_ago):
    return utcnow() - timedelta(hours=hours_ago)


def test_unknown_future_and_unknown_type_are_refused_and_store_nothing(boot):
    with session_scope() as session:
        before = session.query(SimEvent).count()
    assert ingest("+94000000000", "sim_swap", when(1), {}, "t")["status"] == "rejected"
    assert ingest(phone("cus_nimal"), "sim_swap", utcnow() + timedelta(hours=3), {}, "t")["reason"].startswith("the event is dated in the future")
    assert ingest(phone("cus_nimal"), "teleport", when(1), {}, "t")["status"] == "rejected"
    with session_scope() as session:
        assert session.query(SimEvent).count() == before


def test_a_repeat_is_recognised_by_id_or_by_time_and_changes_nothing(boot):
    p = phone("cus_nimal")
    t = when(2)
    first = ingest(p, "new_imei", t, {}, "t", event_id="e-1")
    assert first["status"] == "recorded"
    assert ingest(p, "new_imei", t + timedelta(hours=1), {}, "t", event_id="e-1")["status"] == "duplicate"
    assert ingest(p, "new_imei", t, {}, "t")["status"] == "duplicate"
    with session_scope() as session:
        assert session.query(SimEvent).filter(SimEvent.phone == p, SimEvent.origin == "t").count() == 1


def test_events_are_stored_with_their_source_and_the_time_they_were_received(boot):
    p = phone("cus_nimal")
    result = ingest(p, "location_change", when(5), {}, "operator")
    with session_scope() as session:
        row = session.get(SimEvent, result["event_id"])
        assert row.origin == "operator" and row.received_at is not None and row.received_at > row.occurred_at


def test_a_stale_event_is_recorded_but_does_not_touch_an_open_case(boot):
    case_id = waiting()
    result = ingest(phone("cus_nimal"), "location_change", when(100), {}, "t")
    assert result["status"] == "stale" and result["cases"] == []
    assert status(case_id) == ("waiting_customer", "held")


def test_a_late_event_that_changes_nothing_leaves_the_case_waiting(boot):
    case_id = waiting()
    result = ingest(phone("cus_nimal"), "sim_swap", when(30), {}, "t")  # older and weaker than the one already known
    assert result["cases"][0]["reaction"] == "no material change"
    assert status(case_id) == ("waiting_customer", "held")


def test_a_port_out_during_the_wait_hands_the_case_to_a_person_and_keeps_the_money_held(boot):
    case_id = waiting()
    with session_scope() as session:
        before = session.get(User, "cus_nimal").balance_lkr
    result = ingest(phone("cus_nimal"), "port_out", when(0.01), {}, "t")
    assert result["cases"][0]["reaction"] == "escalated"
    assert status(case_id)[0] in {"escalated", "closed_blocked"} and status(case_id)[1] in {"held", "blocked"}
    with session_scope() as session:
        assert session.get(User, "cus_nimal").balance_lkr == before
        assert session.query(CaseEvent).filter(CaseEvent.case_id == case_id, CaseEvent.message.like("A port out event arrived%")).count() == 1


def test_a_cancelled_port_out_does_not_escalate(boot):
    case_id = waiting()
    ingest(phone("cus_nimal"), "port_out", when(0.01), {"cancelled": True}, "t")
    assert status(case_id)[0] == "waiting_customer"


def test_another_customers_event_never_touches_this_case(boot):
    case_id = waiting("cus_nimal")
    with session_scope() as session:
        other = session.query(User).filter(User.id != "cus_nimal", User.phone.isnot(None)).first().id
    result = ingest(phone(other), "port_out", when(0.01), {}, "t")
    assert result["customer_id"] == other and all(c["case_id"] != case_id for c in result["cases"])
    assert status(case_id) == ("waiting_customer", "held")


def test_an_event_during_a_running_decision_is_left_for_that_decision_to_read(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")  # created but not started
    with session_scope() as session:
        session.get(Case, case_id).status = "running"
    result = ingest(phone("cus_nimal"), "new_imei", when(0.01), {}, "t")
    assert result["cases"][0]["reaction"] == "will be read by the running decision"


def test_the_event_never_releases_money(boot):
    case_id = waiting()
    for kind in ("sim_swap", "new_imei", "port_out", "location_change"):
        ingest(phone("cus_nimal"), kind, when(0.01), {}, "t")
    assert status(case_id)[1] != "released"
