"""The signal contract: one snapshot per decision, honest provenance, an authoritative telecom source, and a last look before
money is released."""
import dataclasses
import time
from datetime import timedelta

import httpx

from app import signals as contract
from app.agents import tools
from app.agents.runner import begin_case, resume_case
from app.db import session_scope
from app.demo import trigger
from app.models import (
    Case,
    CaseEvent,
    DecisionSnapshot,
    Notification,
    SimEvent,
    Transaction,
    User,
)
from app.risk import Signals, compute_risk_score, decide_policy
from app.runtime import runtime
from app.util import nid, utcnow

REMOTE = "https://operator.example/camara"


def add_event(phone, kind, hours_ago=0.1, detail=None, origin="test"):
    with session_scope() as session:
        session.add(SimEvent(id=nid("sim"), phone=phone, event_type=kind, occurred_at=utcnow() - timedelta(hours=hours_ago), detail=detail or {}, origin=origin, received_at=utcnow()))


def phone_of(customer="cus_nimal"):
    with session_scope() as session:
        return session.get(User, customer).phone


def routine_case(customer="cus_nimal"):
    case_id = trigger(customer, "routine_payment")
    return case_id


def ids(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        return case.customer_id, case.transaction_id


# ------------------------------------------------------------------ one snapshot for the score and the decision

def test_a_case_stores_the_snapshot_its_decision_was_made_on(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    with session_scope() as session:
        case = session.get(Case, case_id)
        snap = session.get(DecisionSnapshot, case.snapshot_id)
        assert snap.case_id == case_id and snap.purpose == "score" and snap.version == 1 and len(snap.digest) == 64
        assert (snap.score, snap.decision) == (case.risk_score, case.policy_decision) == (snap.score, "hold")
        assert decide_policy(Signals(**snap.signals)).decision == "hold" and compute_risk_score(Signals(**snap.signals))[0] == snap.score


def test_the_score_and_the_decision_use_the_same_stored_reading_even_when_new_data_arrives_between_them(boot):
    case_id = trigger("cus_nimal", "routine_payment")
    customer, tx = ids(case_id)
    scored = tools.compute_risk_score(customer, tx, case_id=case_id)
    before = tools.apply_policy(customer, tx, scored["snapshot_id"])
    add_event(phone_of(), "port_out", 0.2)  # arrives after the score was taken
    add_event(phone_of(), "sim_swap", 0.1)
    on_snapshot = tools.apply_policy(customer, tx, scored["snapshot_id"])
    fresh = tools.apply_policy(customer, tx)  # an older call style, with no snapshot, sees the new events
    assert on_snapshot["decision"] == before["decision"] == "release" and on_snapshot["score"] == before["score"] == scored["score"]
    assert fresh["decision"] != "release" and fresh["score"] > scored["score"]


def test_the_final_decision_is_written_beside_its_snapshot(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    customer, tx = ids(case_id)
    scored = tools.compute_risk_score(customer, tx, case_id=case_id)
    decided = tools.apply_policy(customer, tx, scored["snapshot_id"])
    with session_scope() as session:
        row = session.get(DecisionSnapshot, scored["snapshot_id"])
        assert (row.decision, row.score) == (decided["decision"], decided["score"])


def test_an_emergency_hold_is_recorded_as_the_final_decision_while_the_rules_alone_said_release(boot):
    from app.emergency import force_emergency

    case_id = trigger("cus_nimal", "large_payment") if False else routine_case()
    customer, tx = ids(case_id)
    with session_scope() as session:
        session.get(Transaction, tx).amount_lkr = 120_000
        district = session.get(User, customer).district
    force_emergency(district, "fraud ring reported", "analyst@payshield.lk")
    scored = tools.compute_risk_score(customer, tx, case_id=case_id)
    decided = tools.apply_policy(customer, tx, scored["snapshot_id"])
    assert decide_policy(contract.load(scored["snapshot_id"]).as_signals()).decision == "release" and decided["decision"] == "hold" and decided["emergency"] == district


# ------------------------------------------------------------------ provenance and the digest

def test_each_observation_says_when_it_happened_when_it_was_received_and_how_old_it_was(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    customer, tx = ids(case_id)
    snap = contract.build(customer, tx, case_id=case_id, purpose="score")
    swap = snap.provenance["sim_swap"]
    assert swap["source"] == "local-db" and swap["origin"] == "demo" and swap["occurred_at"] and swap["received_at"]
    assert 1400 < swap["age_seconds"] < 1700  # the drill places the swap about 25 minutes before now
    assert swap["ingestion_lag_seconds"] is not None and swap["ingestion_lag_seconds"] >= 1400
    assert snap.provenance["fetched_at"] and snap.provenance["contract_version"] == contract.CONTRACT_VERSION
    assert snap.provenance["payment"]["transaction_id"] == tx


def test_the_digest_depends_on_the_facts_not_on_the_time(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    customer, tx = ids(case_id)
    first = contract.build(customer, tx, case_id=case_id, purpose="score")
    time.sleep(1.1)
    second = contract.build(customer, tx, case_id=case_id, purpose="pre_release")
    assert first.digest == second.digest and first.provenance["fetched_at"] != second.provenance["fetched_at"]
    add_event(phone_of(), "port_out", 0.05)
    assert contract.build(customer, tx, case_id=case_id, purpose="pre_release").digest != first.digest


def test_snapshots_of_one_case_are_numbered_in_order(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    customer, tx = ids(case_id)
    versions = [contract.build(customer, tx, case_id=case_id, purpose="score").version for _ in range(3)]
    assert versions == [1, 2, 3]


# ------------------------------------------------------------------ the operator gateway is authoritative

class Gateway:
    """Stands in for the operator's CAMARA service."""

    def __init__(self, swapped, latest=None, fail=None):
        self.swapped, self.latest, self.fail, self.calls = swapped, latest, fail, []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append(url)
        if self.fail:
            raise self.fail
        request = httpx.Request("POST", url)
        if url.endswith("/check"):
            return httpx.Response(200, json={"swapped": self.swapped}, request=request)
        return httpx.Response(200, json={"latestSimChange": self.latest}, request=request)


def external(monkeypatch, gateway):
    monkeypatch.setattr(httpx, "post", gateway)
    monkeypatch.setattr(runtime, "settings", dataclasses.replace(runtime.settings, telco_base_url=REMOTE, demo_mode="external"))


def hours_ago(hours):
    return (utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_when_the_operator_says_swapped_the_score_uses_that_even_if_the_local_record_has_no_swap(boot, monkeypatch):
    external(monkeypatch, Gateway(True, hours_ago(2)))
    case_id = routine_case()
    customer, tx = ids(case_id)
    snap = contract.build(customer, tx, case_id=case_id, purpose="score")
    assert snap.provenance["sim_swap"]["source"] == "camara-http" and snap.provenance["sim_swap"]["origin"] == "operator gateway"
    assert snap.as_signals().swapped is True and 1.9 < snap.as_signals().sim_swap_age_hours < 2.1
    assert [c["kind"] for c in snap.conflicts] == ["operator_and_local_record_disagree"] and snap.conflicts[0]["used"] == "operator gateway"
    assert any(f["code"] == "sim_swap_24h" for f in compute_risk_score(snap.as_signals())[1])


def test_when_the_operator_says_no_swap_a_local_swap_does_not_leak_into_the_decision_and_the_disagreement_is_shown(boot, monkeypatch):
    external(monkeypatch, Gateway(False))
    case_id = trigger("cus_nimal", "sim_swap_transfer")  # the local record has a swap 25 minutes ago
    customer, tx = ids(case_id)
    snap = contract.build(customer, tx, case_id=case_id, purpose="score")
    assert snap.as_signals().swapped is False and snap.as_signals().sim_swap_age_hours is None
    assert snap.conflicts and snap.conflicts[0]["operator_said_swapped"] is False and snap.conflicts[0]["local_record_said_swapped"] is True


def test_agreement_between_operator_and_local_record_records_no_conflict(boot, monkeypatch):
    external(monkeypatch, Gateway(False))
    case_id = routine_case()
    customer, tx = ids(case_id)
    assert contract.build(customer, tx, case_id=case_id, purpose="score").conflicts == []


def test_the_telecom_agents_own_observation_is_what_the_score_uses_with_no_second_call_to_the_operator(boot, monkeypatch):
    gateway = Gateway(True, hours_ago(1))
    external(monkeypatch, gateway)
    case_id = routine_case()
    customer, tx = ids(case_id)
    seen = {"source": "camara-http", "swapped": True, "latest_sim_change": hours_ago(1), "fetched_at": utcnow().isoformat()}
    gateway.calls.clear()
    scored = tools.compute_risk_score(customer, tx, case_id=case_id, telecom=seen)
    assert gateway.calls == [] and scored["signals"]["swapped"] == "yes"


def test_the_whole_workflow_acts_on_the_operators_finding_not_on_contradicting_local_data(boot, monkeypatch):
    external(monkeypatch, Gateway(True, hours_ago(1)))
    case_id = routine_case()  # a routine Rs. 4,850 bill, with no swap anywhere locally
    begin_case(case_id)
    with session_scope() as session:
        case = session.get(Case, case_id)
        labels = [f["label"] for f in case.risk_factors]
        snap = session.get(DecisionSnapshot, case.snapshot_id)
        conflict_events = [e.message for e in session.query(CaseEvent).filter(CaseEvent.case_id == case_id, CaseEvent.agent == "risk", CaseEvent.message.like("Two sources disagree%")).all()]
    assert any("SIM swap" in label for label in labels) and snap.provenance["sim_swap"]["source"] == "camara-http"
    assert conflict_events and "operator and local record disagree" in conflict_events[0]


def test_an_unreachable_operator_leaves_the_money_held_and_a_person_on_the_case_and_never_falls_back_to_local_data(boot, monkeypatch):
    external(monkeypatch, Gateway(False, fail=httpx.ConnectError("down")))
    case_id = routine_case()
    begin_case(case_id)
    with session_scope() as session:
        case = session.get(Case, case_id)
        tx = session.get(Transaction, case.transaction_id)
        assert case.status == "escalated" and tx.status in {"held", "pending"} and tx.status != "released"


def test_a_local_swap_is_marked_as_found_after_the_telecom_agent_looked(boot):
    case_id = routine_case()
    customer, tx = ids(case_id)
    saw = {"source": "camara-mock", "swapped": False, "latest_sim_change": None, "fetched_at": utcnow().isoformat()}
    add_event(phone_of(), "sim_swap", 0.2)  # lands between the telecom read and the scoring
    scored = tools.compute_risk_score(customer, tx, case_id=case_id, telecom=saw)
    assert scored["signals"]["swapped"] == "yes" and scored["conflicts"][0]["kind"] == "changed_since_the_telecom_agent_read"


# ------------------------------------------------------------------ what counts as new information

def snap(**over):
    base = dict(signals=Signals(**over))
    return contract.Snapshot(id="s", case_id="c", purpose="score", version=1, created_at="now", signals=dataclasses.asdict(base["signals"]), provenance={}, digest="d")


def test_a_stricter_rule_decision_is_material():
    prior = snap(sim_swap_age_hours=5, amount_lkr=60_000, new_payee=True)  # hold
    worse = snap(sim_swap_age_hours=5, amount_lkr=600_000, new_payee=True, port_out=True, password_reset_age_hours=1)  # block
    reasons = contract.material_changes(prior, worse)
    assert any("the rules now say block" in r for r in reasons)


def test_a_new_high_risk_warning_sign_is_material_even_when_the_decision_does_not_change():
    prior = snap(sim_swap_age_hours=5, amount_lkr=60_000, new_payee=True)
    worse = snap(sim_swap_age_hours=5, amount_lkr=60_000, new_payee=True, new_device_login=True)
    assert any("new warning sign" in r and "device" in r for r in contract.material_changes(prior, worse))


def test_a_big_rise_in_the_score_is_material():
    prior = snap(sim_swap_age_hours=100, amount_lkr=100_000)
    worse = snap(sim_swap_age_hours=100, amount_lkr=100_000, location_change_age_hours=1, new_payee=True)
    assert any("the score rose" in r for r in contract.material_changes(prior, worse))


def test_an_improvement_a_small_change_or_no_change_is_not_material():
    prior = snap(sim_swap_age_hours=5, amount_lkr=60_000, new_payee=True, port_out=True)
    assert contract.material_changes(prior, snap(sim_swap_age_hours=5, amount_lkr=60_000, new_payee=True)) == []
    assert contract.material_changes(prior, prior) == []
    assert contract.material_changes(snap(sim_swap_age_hours=5, amount_lkr=60_000, new_payee=True), snap(sim_swap_age_hours=5, amount_lkr=60_000, new_payee=True, location_change_age_hours=1)) == []


def test_with_no_earlier_snapshot_only_a_release_passes():
    assert contract.material_changes(None, snap()) == []
    assert contract.material_changes(None, snap(sim_swap_age_hours=5, amount_lkr=60_000, new_payee=True))


# ------------------------------------------------------------------ the last look before money moves

def settled(customer="cus_nimal", amount=None):
    case_id = routine_case(customer)
    customer_id, tx = ids(case_id)
    if amount:
        with session_scope() as session:
            session.get(Transaction, tx).amount_lkr = amount
    scored = tools.compute_risk_score(customer_id, tx, case_id=case_id)
    return case_id, customer_id, tx, scored["snapshot_id"]


def test_with_nothing_new_the_last_look_lets_a_routine_payment_go(boot):
    case_id, customer, tx, snapshot = settled()
    look = tools.revalidate_before_release(customer, tx, case_id, snapshot, customer_confirmed=False)
    assert look["ok"] is True and look["reasons"] == [] and look["compared_with"]["id"] == snapshot


def test_a_port_out_opened_after_the_decision_stops_the_release(boot):
    case_id, customer, tx, snapshot = settled()
    add_event(phone_of(), "port_out", 0.05)
    look = tools.revalidate_before_release(customer, tx, case_id, snapshot, customer_confirmed=False)
    assert look["ok"] is False and any("Port-out" in r or "port-out" in r for r in look["reasons"])


def test_a_new_handset_and_a_swap_after_the_decision_stop_the_release(boot):
    case_id, customer, tx, snapshot = settled()
    add_event(phone_of(), "sim_swap", 0.05)
    add_event(phone_of(), "new_imei", 0.04)
    assert tools.revalidate_before_release(customer, tx, case_id, snapshot, customer_confirmed=False)["ok"] is False


def test_a_cancelled_port_out_does_not_count(boot):
    case_id, customer, tx, snapshot = settled()
    add_event(phone_of(), "port_out", 0.05, detail={"cancelled": True})
    assert tools.revalidate_before_release(customer, tx, case_id, snapshot, customer_confirmed=False)["ok"] is True


def test_an_emergency_switched_on_after_a_routine_decision_stops_the_release_but_not_after_the_customer_confirmed(boot):
    from app.emergency import force_emergency

    case_id, customer, tx, snapshot = settled(amount=120_000)
    with session_scope() as session:
        district = session.get(User, customer).district
    force_emergency(district, "fraud ring reported", "analyst@payshield.lk")
    assert tools.revalidate_before_release(customer, tx, case_id, snapshot, customer_confirmed=False)["ok"] is False
    assert tools.revalidate_before_release(customer, tx, case_id, snapshot, customer_confirmed=True)["ok"] is True


def test_an_account_limit_always_needs_a_person_even_after_the_customer_confirmed(boot):
    from app.ledger import lock_account

    case_id, customer, tx, snapshot = settled()
    lock_account(customer, case_id)
    for confirmed in (False, True):
        look = tools.revalidate_before_release(customer, tx, case_id, snapshot, customer_confirmed=confirmed)
        assert look["ok"] is False and any("a person clears the limit" in r for r in look["reasons"])


def test_the_last_look_is_stored_as_a_second_snapshot_and_the_first_is_left_alone(boot):
    case_id, customer, tx, snapshot = settled()
    original = contract.load(snapshot)
    tools.revalidate_before_release(customer, tx, case_id, snapshot, customer_confirmed=False)
    with session_scope() as session:
        rows = session.query(DecisionSnapshot).filter(DecisionSnapshot.case_id == case_id).order_by(DecisionSnapshot.version).all()
        assert [(r.version, r.purpose) for r in rows] == [(1, "score"), (2, "pre_release")]
    assert contract.load(snapshot).digest == original.digest and contract.load(snapshot).signals == original.signals


# ------------------------------------------------------------------ through the real workflow

def intercept(monkeypatch, then):
    """Run `then` just before the last look, as if the world changed in that gap."""
    real = tools.revalidate_before_release

    def wrapper(*args, **kwargs):
        then()
        return real(*args, **kwargs)

    monkeypatch.setattr(tools, "revalidate_before_release", wrapper)


def state(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        tx = session.get(Transaction, case.transaction_id)
        user = session.get(User, case.customer_id)
        return {"case": case.status, "tx": tx.status, "balance": user.balance_lkr, "reason": case.policy_reason, "snapshot": case.snapshot_id, "score": case.risk_score, "decision": case.policy_decision}


def test_a_routine_payment_that_gets_a_port_out_before_release_stays_held_and_goes_to_a_person(boot, monkeypatch):
    case_id = routine_case()
    customer, tx = ids(case_id)
    with session_scope() as session:
        before_balance = session.get(User, customer).balance_lkr
    intercept(monkeypatch, lambda: add_event(phone_of(), "port_out", 0.02))
    begin_case(case_id)
    after = state(case_id)
    assert (after["case"], after["tx"], after["balance"]) == ("escalated", "held", before_balance)
    assert after["decision"] == "release", "the earlier decision is not rewritten"
    with session_scope() as session:
        stop = [e.message for e in session.query(CaseEvent).filter(CaseEvent.case_id == case_id, CaseEvent.message.like("Release stopped%")).all()]
        titles = {n.title for n in session.query(Notification).filter(Notification.case_id == case_id).all()}
        assert stop and "new warning sign" in stop[0] and "Payment still on hold" in titles
        assert session.query(DecisionSnapshot).filter(DecisionSnapshot.case_id == case_id).count() == 2


def test_after_the_customer_passes_verification_new_high_risk_information_still_stops_the_release(boot, monkeypatch):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    resume_case(case_id, {"decision": "yes"}, "waiting_customer")
    resume_case(case_id, {"liveness": "pass"}, "waiting_liveness")
    with session_scope() as session:
        prompts = session.get(Case, case_id).pending_prompt["questions"]
    given = {item["id"]: {"school": "Royal College", "city": "Galle"}[item["id"]] for item in prompts}
    intercept(monkeypatch, lambda: add_event(phone_of(), "port_out", 0.01))
    resume_case(case_id, {"answers": given}, "waiting_verification")
    after = state(case_id)
    assert (after["case"], after["tx"]) == ("escalated", "held")


def test_the_same_verification_with_nothing_new_still_releases_once(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    customer, tx = ids(case_id)
    begin_case(case_id)
    with session_scope() as session:
        start = session.get(User, customer).balance_lkr
        amount = session.get(Transaction, tx).amount_lkr
    resume_case(case_id, {"decision": "yes"}, "waiting_customer")
    resume_case(case_id, {"liveness": "pass"}, "waiting_liveness")
    with session_scope() as session:
        prompts = session.get(Case, case_id).pending_prompt["questions"]
    given = {item["id"]: {"school": "Royal College", "city": "Galle"}[item["id"]] for item in prompts}
    resume_case(case_id, {"answers": given}, "waiting_verification")
    after = state(case_id)
    assert (after["case"], after["tx"], after["balance"]) == ("closed_released", "released", start - amount)


def test_a_limited_account_that_the_customer_confirms_still_waits_for_a_person(boot):
    from app.ledger import lock_account

    first = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(first)
    lock_account("cus_nimal", first)
    second = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(second)
    resume_case(second, {"decision": "yes"}, "waiting_customer")
    resume_case(second, {"liveness": "pass"}, "waiting_liveness")
    with session_scope() as session:
        prompts = session.get(Case, second).pending_prompt["questions"]
    given = {item["id"]: {"school": "Royal College", "city": "Galle"}[item["id"]] for item in prompts}
    resume_case(second, {"answers": given}, "waiting_verification")
    assert state(second)["case"] == "escalated" and state(second)["tx"] == "held"
