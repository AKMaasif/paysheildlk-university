"""demo routes (split from app/api.py)."""
import logging
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel, Field

from app.agents.runner import (
    escalate_case,
    realert_case,
    spawn_case,
)
from app.ledger import audit
from app.models import (
    User,
)
from app.routes.common import require_drills, require_operator, require_writer
from app.util import (
    utcnow,
)

log = logging.getLogger("payshield.api")
router = APIRouter()


SCENARIOS = [
    {
        "id": "sim_swap_transfer",
        "title": "SIM swap and Rs. 200,000 to a new payee",
        "detail": "The rule layer holds the money and asks the customer on the registered device.",
    },
    {
        "id": "port_out_critical",
        "title": "Port-out, password reset, and Rs. 800,000",
        "detail": "The rule layer blocks the transfer without waiting for the customer.",
    },
    {
        "id": "large_payment",
        "title": "Rs. 75,000 payment with no SIM signal",
        "detail": "The normal rules release it. Switch on emergency mode for the district first and the same payment is held.",
    },
    {
        "id": "routine_payment",
        "title": "Routine bill of Rs. 4,850",
        "detail": "No new SIM event is added. The rule layer releases the payment.",
    },
]


class DemoIn(BaseModel):
    customer_id: str
    scenario: str


class NudgeIn(BaseModel):
    case_id: str
    action: str


@router.post("/api/demo/trigger", dependencies=[Depends(require_drills)])
def demo_trigger(body: DemoIn, user: User = Depends(require_writer)):
    from app.demo import trigger

    try:
        case_id = trigger(body.customer_id, body.scenario)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Unknown customer") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(user.email, "demo_trigger", case_id, body.scenario)
    spawn_case(case_id)
    return {"case_id": case_id}


class UnregisterIn(BaseModel):
    customer_id: str


class SimEventIn(BaseModel):
    phone: str
    event_type: str
    occurred_at: datetime | None = None
    cancelled: bool = False
    event_id: str | None = Field(default=None, max_length=60)
    source: str = Field(default="injected", max_length=20)


@router.post("/api/sim-events", dependencies=[Depends(require_drills)])
def inject_sim_event(body: SimEventIn, user: User = Depends(require_writer)):
    from app.ingest import ingest

    result = ingest(body.phone, body.event_type, body.occurred_at or utcnow(), {"cancelled": True} if body.cancelled else {}, body.source, body.event_id)
    if result["status"] == "rejected":
        raise HTTPException(status_code=422, detail=result["reason"])
    audit(user.email, "sim_event_injected", result.get("customer_id") or "-", f"{body.event_type} {result['status']}")
    return result


@router.post("/api/demo/unregister", dependencies=[Depends(require_drills)])
def demo_unregister(body: UnregisterIn, user: User = Depends(require_writer)):
    from app.demo import unregister

    if not unregister(body.customer_id):
        raise HTTPException(status_code=404, detail="Unknown customer")
    audit(user.email, "demo_unregister", body.customer_id, "returned to first-time state")
    return {"ok": True}


@router.post("/api/demo/reset", dependencies=[Depends(require_drills)])
def demo_reset(user: User = Depends(require_writer)):
    from app.demo import reset_demo

    result = reset_demo()
    audit(user.email, "demo_reset", "demo", str(result))
    return result


@router.get("/api/demo/assist", dependencies=[Depends(require_drills)])
def demo_assist(customer_id: str, user: User = Depends(require_operator)):
    from app.demo import assist

    try:
        return assist(customer_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Unknown customer") from exc


@router.get("/api/demo/customers", dependencies=[Depends(require_drills)])
def demo_customers(user: User = Depends(require_operator)):
    from app.demo import list_customers

    return {"customers": list_customers(), "scenarios": SCENARIOS}


@router.post("/api/demo/nudge", dependencies=[Depends(require_drills)])
def demo_nudge(body: NudgeIn, user: User = Depends(require_writer)):
    if body.action == "realert":
        ok = realert_case(body.case_id)
    elif body.action == "escalate":
        ok = escalate_case(body.case_id)
    else:
        raise HTTPException(status_code=400, detail="Action must be realert or escalate")
    if not ok:
        raise HTTPException(status_code=409, detail="That case is not waiting for the customer")
    audit(user.email, "demo_" + body.action, body.case_id, body.action)
    return {"ok": True}
