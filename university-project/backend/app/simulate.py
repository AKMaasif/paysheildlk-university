"""One-button emergency and payment scenarios for demonstrations.

Each scenario drives the real code path. A drill turns on a switch in
``app.faults`` or puts a district into emergency mode, then starts an ordinary
case, so what the room sees is what the system would do. Every run, and every
switch an operator flips, is written to the audit log.
"""

import threading
import time
from datetime import timedelta

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app import faults, manual
from app.agents.runner import begin_case, escalate_case, realert_case, spawn_case
from app.db import session_scope
from app.demo import trigger
from app.desk import _writer
from app.emergency import SPIKE_COUNT, breaker_status, clear_emergency, force_emergency
from app.geo import DISTRICTS
from app.ledger import audit
from app.models import Transaction, User
from app.util import nid, utcnow

router = APIRouter()

DEFAULT_CUSTOMER = "cus_nimal"

SCENARIOS = [
    # Payment paths
    {"id": "sim_swap_transfer", "group": "Payment paths", "title": "SIM swap, then Rs. 200,000 to a new payee",
     "expect": "Held. The customer is asked on the registered device."},
    {"id": "port_out_critical", "group": "Payment paths", "title": "Port-out, password reset and Rs. 800,000",
     "expect": "Blocked at once, without waiting for the customer."},
    {"id": "routine_payment", "group": "Payment paths", "title": "Routine bill of Rs. 4,850",
     "expect": "Released by the rules. A short note is filed."},
    # Emergencies
    {"id": "customer_silent", "group": "Emergencies", "title": "The customer never answers",
     "expect": "Reminder, then escalation to an analyst. The payment stays held."},
    {"id": "models_down", "group": "Emergencies", "title": "Both AI models are down",
     "expect": "The rules engine writes the notes. The decision is the same and nothing is released automatically."},
    {"id": "channel_failure", "group": "Emergencies", "title": "SMS and email are both failing",
     "expect": "One retry, then push and the in-app inbox. The case is flagged for an analyst."},
    {"id": "district_spike", "group": "Emergencies", "title": "A fraud spike in the customer's district",
     "expect": "Emergency mode starts. A Rs. 75,000 payment the rules would release is held."},
    {"id": "agent_failure", "group": "Emergencies", "title": "The Risk agent crashes mid-case",
     "expect": "The supervisor holds the money and an analyst takes the case."},
    {"id": "manual_freeze", "group": "Emergencies", "title": "An analyst uses the manual block switch",
     "expect": "Blocked and the account limited, with a report and receipt. The use is audited."},
]
BY_ID = {item["id"]: item for item in SCENARIOS}


class RunIn(BaseModel):
    scenario: str
    customer_id: str = DEFAULT_CUSTOMER


class EmergencyIn(BaseModel):
    district: str
    on: bool
    reason: str = "analyst decision"


def _customer(customer_id: str) -> tuple[str, str]:
    with session_scope() as session:
        user = session.get(User, customer_id)
        if user is None or user.role != "customer":
            raise HTTPException(status_code=404, detail="Unknown customer")
        return user.id, user.district or ""


def _inject_spike(district: str) -> int:
    """Eight held or blocked payments in the district inside the window, under a placeholder customer."""
    now = utcnow()
    with session_scope() as session:
        for index in range(SPIKE_COUNT):
            session.add(
                Transaction(
                    id="DRILL-" + nid("d").split("_")[-1][:8].upper(),
                    customer_id="drill",
                    kind="transfer",
                    amount_lkr=90_000 + index * 5_000,
                    payee="Drill payee",
                    status="blocked",
                    occurred_at=now - timedelta(seconds=30 * index),
                    origin="demo",
                    detail={"district": district, "drill": True},
                )
            )
    return SPIKE_COUNT


def _later(work) -> None:
    threading.Thread(target=work, name="simulate", daemon=True).start()


def run(scenario: str, customer_id: str, actor: str) -> dict:
    if scenario not in BY_ID:
        raise HTTPException(status_code=404, detail="Unknown scenario")
    customer, district = _customer(customer_id)
    note = ""

    if scenario in {"sim_swap_transfer", "port_out_critical", "routine_payment"}:
        case_id = trigger(customer, scenario)
        spawn_case(case_id)
    elif scenario == "customer_silent":
        case_id = trigger(customer, "sim_swap_transfer")

        def silent() -> None:
            begin_case(case_id)
            time.sleep(2.5)
            realert_case(case_id)
            time.sleep(2.5)
            escalate_case(case_id)

        _later(silent)
        note = "The 3-minute reminder and the 10-minute escalation run now instead of waiting."
    elif scenario == "models_down":
        faults.turn_on("llm_down")
        audit(actor, "simulate_fault_on", "llm_down", "Both models treated as down")
        case_id = trigger(customer, "sim_swap_transfer")
        spawn_case(case_id)
        note = "The switch stays on until you clear drills, so further cases also run without models."
    elif scenario == "channel_failure":
        faults.turn_on("sms_down")
        faults.turn_on("email_down")
        audit(actor, "simulate_fault_on", "sms_down,email_down", "SMS gateway and email server treated as down")
        case_id = trigger(customer, "sim_swap_transfer")
        spawn_case(case_id)
        note = "The switches stay on until you clear drills."
    elif scenario == "district_spike":
        if district not in DISTRICTS:
            raise HTTPException(status_code=409, detail="This customer has no district on file")
        count = _inject_spike(district)
        audit(actor, "simulate_spike", district, f"{count} held or blocked payments injected in {district}")
        case_id = trigger(customer, "large_payment")
        spawn_case(case_id)
        note = f"{count} blocked drill payments were added in {district}. The payment that follows is Rs. 75,000 with no SIM signal."
    elif scenario == "agent_failure":
        faults.turn_on("fail_agent", "risk")
        audit(actor, "simulate_fault_on", "fail_agent", "The Risk agent will crash once")
        case_id = trigger(customer, "sim_swap_transfer")
        spawn_case(case_id)
        note = "The Risk agent crashes the next time it runs, once."
    else:  # manual_freeze
        case_id = trigger(customer, "sim_swap_transfer")

        def freeze() -> None:
            begin_case(case_id)
            time.sleep(2.5)
            manual.apply(case_id, "block", "Analyst blocked the payment from the desk (drill)", actor)

        _later(freeze)
        note = "The payment is held, then the analyst's block switch is used."

    audit(actor, "simulate_" + scenario, case_id, BY_ID[scenario]["title"])
    return {"case_id": case_id, "scenario": scenario, "note": note, "faults": faults.snapshot(), "emergency": breaker_status(district)}


@router.get("/api/simulate")
def simulate_list(authorization: str | None = Header(default=None)):
    from app.api import current_user

    user = current_user(authorization)
    if user.role == "customer":
        raise HTTPException(status_code=403, detail="Operator sign-in required")
    with session_scope() as session:
        customers = [
            {"id": row.id, "name": row.full_name, "district": row.district}
            for row in session.query(User).filter(User.role == "customer").order_by(User.full_name)
        ]
    return {"scenarios": SCENARIOS, "faults": faults.snapshot(), "emergency": breaker_status(), "customers": customers, "default_customer": DEFAULT_CUSTOMER}


@router.post("/api/simulate/run")
def simulate_run(body: RunIn, authorization: str | None = Header(default=None)):
    actor = _writer(authorization)
    return run(body.scenario, body.customer_id, actor.email)


@router.post("/api/simulate/clear")
def simulate_clear(authorization: str | None = Header(default=None)):
    """Switch every drill off and remove the placeholder payments a spike drill added.
    An analyst's own emergency switch is a real control and is left alone."""
    actor = _writer(authorization)
    switched_off = faults.clear_all()
    with session_scope() as session:
        removed = session.query(Transaction).filter(Transaction.customer_id == "drill").delete(synchronize_session=False)
    audit(actor.email, "simulate_clear", "drills", f"faults off: {', '.join(switched_off) or 'none'}; drill payments removed: {removed}")
    return {"faults_off": switched_off, "drill_payments_removed": removed, "emergency": breaker_status()}


@router.get("/api/emergency")
def emergency_status(authorization: str | None = Header(default=None)):
    from app.api import current_user

    user = current_user(authorization)
    if user.role == "customer":
        raise HTTPException(status_code=403, detail="Operator sign-in required")
    return {**breaker_status(), "faults": faults.snapshot()}


@router.post("/api/emergency")
def emergency_switch(body: EmergencyIn, authorization: str | None = Header(default=None)):
    """An analyst puts a district into emergency mode, or takes it out. Both are audited."""
    actor = _writer(authorization)
    if body.district not in DISTRICTS:
        raise HTTPException(status_code=400, detail="Unknown district")
    if body.on:
        force_emergency(body.district, body.reason, by=actor.email)
        audit(actor.email, "emergency_on", body.district, body.reason)
    else:
        clear_emergency(body.district)
        audit(actor.email, "emergency_off", body.district, body.reason)
    return breaker_status(body.district)
