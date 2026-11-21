"""Taking in a telecom event that arrives while cases are open.

Real operators send events late, twice, out of order, or wrong. This is the one door they come through. It records
each event with where it came from and when it was received, refuses what cannot be true, and then looks at the
customer's open cases. A case whose picture just got materially worse is stopped and handed to a person. Nothing that
already happened is rewritten, and nothing here can release money."""

from datetime import timedelta

from app.db import session_scope
from app.events import emit
from app.models import Case, SimEvent, User
from app.util import aware, nid, utcnow

KINDS = {"sim_swap", "new_imei", "location_change", "port_out"}
STALE_HOURS = 72
FUTURE_SLACK_SECONDS = 120
OPEN = ("running", "waiting_customer", "waiting_liveness", "waiting_verification")


def ingest(phone: str, kind: str, occurred_at, detail: dict | None, source: str, event_id: str | None = None) -> dict:
    if kind not in KINDS:
        return {"status": "rejected", "reason": f"unknown event type '{kind}'"}
    now = utcnow()
    moment = aware(occurred_at)
    if (moment - now).total_seconds() > FUTURE_SLACK_SECONDS:
        return {"status": "rejected", "reason": "the event is dated in the future"}
    with session_scope() as session:
        user = session.query(User).filter(User.phone == phone).first()
        if user is None:
            return {"status": "rejected", "reason": "no customer has this phone number"}
        customer_id = user.id
        rows = session.query(SimEvent).filter(SimEvent.phone == phone, SimEvent.event_type == kind).all()
        for row in rows:
            same_id = event_id and (row.detail or {}).get("event_id") == event_id
            same_time = abs((aware(row.occurred_at) - moment).total_seconds()) < 1 and (row.detail or {}).get("cancelled") == (detail or {}).get("cancelled")
            if same_id or same_time:
                return {"status": "duplicate", "event_id": row.id, "reason": "already recorded, nothing changed"}
        stale = (now - moment) > timedelta(hours=STALE_HOURS)
        row = SimEvent(
            id=nid("sim"), phone=phone, event_type=kind, occurred_at=moment,
            detail={**(detail or {}), **({"event_id": event_id} if event_id else {})},
            origin=source[:20], received_at=now,
        )
        session.add(row)
        row_id = row.id
        open_cases = [c.id for c in session.query(Case).filter(Case.customer_id == customer_id, Case.status.in_(OPEN)).all()]
    reacted = [] if stale else [_react(case_id, kind) for case_id in open_cases]
    return {"status": "stale" if stale else "recorded", "event_id": row_id, "customer_id": customer_id, "cases": reacted,
            "reason": f"older than {STALE_HOURS} hours: recorded, but it does not reopen any case" if stale else None}


def _react(case_id: str, kind: str) -> dict:
    """Compare the world now with what the case decided on. Only a materially worse picture stops it."""
    from app import signals
    from app.agents.runner import escalate_case
    from app.models import DecisionSnapshot

    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None or case.transaction_id is None:
            return {"case_id": case_id, "reaction": "none"}
        customer_id, tx_id, prior_id, status = case.customer_id, case.transaction_id, case.snapshot_id, case.status
        prior_exists = prior_id and session.get(DecisionSnapshot, prior_id) is not None
    if status == "running" or not prior_exists:
        emit(case_id, agent="supervisor", state="done", provider="rules", model="deterministic",
             message=f"A {kind.replace('_', ' ')} event arrived. The case is still being decided and will read it with the rest.")
        return {"case_id": case_id, "reaction": "will be read by the running decision"}
    fresh = signals.build(customer_id, tx_id, case_id=case_id, purpose="event")
    reasons = signals.material_changes(signals.load(prior_id), fresh)
    if not reasons:
        emit(case_id, agent="supervisor", state="done", provider="rules", model="deterministic",
             message=f"A {kind.replace('_', ' ')} event arrived. It does not change the decision. The case keeps waiting.")
        return {"case_id": case_id, "reaction": "no material change"}
    emit(case_id, agent="supervisor", state="done", provider="rules", model="deterministic",
         message=f"A {kind.replace('_', ' ')} event arrived and the picture is worse: {'; '.join(reasons)}. The case goes to a person and the payment stays held.",
         handoff_from="supervisor", handoff_to="analyst")
    return {"case_id": case_id, "reaction": "escalated" if escalate_case(case_id) else "already moving", "reasons": reasons}
