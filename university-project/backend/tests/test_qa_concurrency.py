"""Cases run side by side. A slow model or provider call on one case must not hold up another."""
import threading
import time
from types import SimpleNamespace

import pytest

from app import protocol
from app.agents.runner import begin_case, resume_case
from app.db import session_scope
from app.demo import trigger
from app.models import Case, Transaction, User
from app.runtime import runtime

DELAY = 0.3


def customers(n):
    with session_scope() as session:
        return [u.id for u in session.query(User).filter(User.role == "customer").order_by(User.id).all()][:n]


@pytest.fixture()
def slow_model(boot, monkeypatch):
    def slow(**_kw):
        time.sleep(DELAY)
        return SimpleNamespace(text="Note.", provider="mock", model="mock", fell_back=False, fallback_from=None, latency_ms=int(DELAY * 1000))

    monkeypatch.setattr(runtime.llm, "complete", slow)


def run_together(case_ids):
    started = time.time()
    finished = {}

    def go(case_id):
        begin_case(case_id)
        finished[case_id] = time.time() - started

    threads = [threading.Thread(target=go, args=(c,)) for c in case_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.time() - started, finished


def test_six_cases_with_a_slow_model_overlap_instead_of_queueing(slow_model):
    solo_id = trigger(customers(1)[0], "sim_swap_transfer")
    solo, _ = run_together([solo_id])
    ids = [trigger(c, "sim_swap_transfer") for c in customers(6)[1:6]] + [trigger(customers(7)[6], "sim_swap_transfer")]
    together, finished = run_together(ids)
    # Serialised, six cases take about six times as long as one. Overlapping, they take well under half of that.
    assert together < solo * 3.5, f"six cases took {together:.2f}s against {solo:.2f}s for one: they are queueing"
    assert len(finished) == 6


def test_cases_run_together_each_reach_their_own_correct_state(slow_model):
    ids = [trigger(c, "sim_swap_transfer") for c in customers(6)]
    run_together(ids)
    with session_scope() as session:
        rows = [(session.get(Case, i), session) for i in ids]
        for case, _ in rows:
            assert case.status == "waiting_customer" and case.policy_decision == "hold"
            assert session.get(Transaction, case.transaction_id).status == "held"
        assert len({c.customer_id for c, _ in rows}) == 6 and len({c.transaction_id for c, _ in rows}) == 6


def test_answering_one_case_does_not_wait_for_another_cases_slow_work(slow_model, monkeypatch):
    first, second = (trigger(c, "sim_swap_transfer") for c in customers(2))
    run_together([first, second])
    release = threading.Event()
    real = runtime.llm.complete

    def blocked(**kw):
        release.wait(5)
        return real(**kw)

    monkeypatch.setattr(runtime.llm, "complete", blocked)
    stuck = threading.Thread(target=resume_case, args=(first, {"decision": "no"}, "waiting_customer"))
    stuck.start()
    time.sleep(0.3)  # the first case is now inside a model call that will not return yet
    monkeypatch.setattr(runtime.llm, "complete", real)
    began = time.time()
    resume_case(second, {"decision": "no"}, "waiting_customer")
    quick = time.time() - began
    release.set()
    stuck.join(10)
    assert quick < 4 and DELAY > 0
    with session_scope() as session:
        assert session.get(Case, second).status == "closed_blocked" and session.get(Case, first).status == "closed_blocked"


def test_the_same_case_is_still_never_run_twice_at_once(boot):
    case_id = trigger(customers(1)[0], "sim_swap_transfer")
    begin_case(case_id)
    results = []
    threads = [threading.Thread(target=lambda: results.append(resume_case(case_id, {"decision": "no"}, "waiting_customer"))) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and results.count(False) == 4
    with session_scope() as session:
        assert session.get(Case, case_id).status == "closed_blocked"


def test_the_handoff_replay_guard_is_safe_when_many_cases_check_handoffs_at_once():
    key = "k" * 40
    errors, accepted = [], []

    def worker(n):
        try:
            for i in range(60):
                sealed = protocol.seal(key, "telecom", "risk", f"C{n}", {"i": i})
                protocol.open_sealed(key, sealed, "risk", f"C{n}")
                accepted.append(1)
        except Exception as exc:  # noqa: BLE001 - any error here is the failure
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(accepted) == 12 * 60
