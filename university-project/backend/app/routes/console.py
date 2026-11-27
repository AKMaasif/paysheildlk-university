"""console routes (split from app/api.py)."""
import logging
from datetime import timedelta

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.db import session_scope
from app.events import event_dict
from app.ledger import audit
from app.models import (
    Case,
    CaseEvent,
    Report,
    SimEvent,
    Transaction,
    User,
)
from app.routes.common import (
    _pdf_response,
    _public_user,
    _timers,
    require_operator,
    require_writer,
)
from app.routes.health import health
from app.runtime import runtime
from app.util import (
    COLOMBO,
    age_phrase,
    aware,
    iso,
    scoped_bank,
    severity,
    utcnow,
)

log = logging.getLogger("payshield.api")
router = APIRouter()


AGENT_CATALOG = [
    {"id": "supervisor", "name": "Supervisor", "task": "Assigns the telecom and banking agents. The route is fixed.", "kind": "rules"},
    {"id": "telecom", "name": "Telecom Agent", "task": "SIM swap, handset, location, and port-out. Looks further (SIM history) when the first reads call for it.", "kind": "model"},
    {"id": "banking", "name": "Banking Agent", "task": "Payments, new payees, and credential events. Compares the payment with what is usual for this customer.", "kind": "model"},
    {"id": "risk", "name": "Risk Agent", "task": "Turns the two feeds into a score.", "kind": "model"},
    {"id": "policy", "name": "Policy rules", "task": "Hold, block, or release. Not a model.", "kind": "rules"},
    {"id": "alert", "name": "Alert Agent", "task": "Push, email, and an SMS with no link and no code.", "kind": "model"},
    {"id": "verification", "name": "Verification Agent", "task": "Security questions, then an optional liveness placeholder.", "kind": "model"},
    {"id": "response", "name": "Response Agent", "task": "Block the transfer, request a SIM freeze, limit the account.", "kind": "model"},
    {"id": "analytics", "name": "Analytics Agent", "task": "Counts payments by district, province and day and writes the dashboard brief, and may propose a rule change that an analyst must approve.", "kind": "model"},
    {"id": "case", "name": "Case / Report Agent", "task": "Checks each agent note against its facts, then writes the incident report for the fraud desk.", "kind": "model"},
]


class OverrideIn(BaseModel):
    action: str
    reason: str = Field(min_length=4, max_length=500)


def _load_users(session, ids: set[str]) -> dict[str, User]:
    if not ids:
        return {}
    rows = session.query(User).filter(User.id.in_(ids)).all()
    return {row.id: row for row in rows}


def _case_passes(case: Case, user: User, bank: str | None, province: str | None, risk: str | None) -> bool:
    forced = scoped_bank(user.role, user.bank, bank)
    if forced and case.bank != forced:
        return False
    if province and province not in {"", "all"} and case.province != province:
        return False
    if risk and risk not in {"", "all"}:
        if severity(case.risk_score) != risk:
            return False
    return True


def _case_brief(case: Case, customer: User | None) -> dict:
    return {
        "timers": _timers(case),
        "id": case.id,
        "origin": getattr(case, "origin", "seed"),
        "customer_id": case.customer_id,
        "customer_name": customer.full_name if customer else "",
        "phone": customer.phone if customer else "",
        "account_number": customer.account_number if customer else "",
        "branch": customer.branch if customer else "",
        "bank": case.bank,
        "province": case.province,
        "district": case.district,
        "status": case.status,
        "risk_score": case.risk_score,
        "severity": severity(case.risk_score),
        "policy_decision": case.policy_decision,
        "policy_reason": case.policy_reason,
        "amount_lkr": case.amount_lkr,
        "payee": case.payee,
        "transaction_id": case.transaction_id,
        "created_at": iso(case.created_at),
        "updated_at": iso(case.updated_at),
        "waiting_since": iso(case.waiting_since),
        "alert_count": case.alert_count,
        "customer_decision": case.customer_decision,
        "verification_detail": case.verification_detail,
        "verification_passed": case.verification_passed,
        "origin": case.origin,
        "scenario": case.scenario,
        "sim_freeze_ref": case.sim_freeze_ref,
        "restricted": bool(customer.restricted) if customer else False,
    }


def _selected_cases(user: User, bank: str | None, province: str | None, risk: str | None):
    with session_scope() as session:
        cases = session.query(Case).all()
        chosen = [case for case in cases if _case_passes(case, user, bank, province, risk)]
        users = _load_users(session, {case.customer_id for case in chosen})
        return chosen, users


@router.get("/api/overview")
def overview(
    user: User = Depends(require_operator),
    bank: str | None = None,
    province: str | None = None,
    risk: str | None = None,
):
    cases, users = _selected_cases(user, bank, province, risk)
    # Open holds are transactions still held. Recompute from the ledger so a released case drops out.
    with session_scope() as session:
        tx_rows = session.query(Transaction).filter(Transaction.status == "held").all()
        tx_cases = {tx.case_id: tx for tx in tx_rows}
    held_cases = []
    for case in cases:
        tx = tx_cases.get(case.id)
        if tx is not None:
            held_cases.append(case)
    blocked = [case for case in cases if case.status == "closed_blocked"]
    waiting = [case for case in cases if case.status in {"waiting_customer", "waiting_verification"}]
    open_cases = [case for case in cases if case.status not in {"closed_blocked", "closed_released", "closed_cleared"}]
    now = utcnow()
    forced = scoped_bank(user.role, user.bank, bank)
    with session_scope() as session:
        sim_q = session.query(SimEvent).filter(SimEvent.event_type == "sim_swap", SimEvent.occurred_at >= now - timedelta(days=7))
        sim_rows = sim_q.all()
        if forced or (province and province not in {"", "all"}):
            phones = {
                customer.phone
                for customer in session.query(User).filter(User.role == "customer").all()
                if (not forced or customer.bank == forced)
                and (not province or province in {"", "all"} or customer.province == province)
            }
            sim_rows = [row for row in sim_rows if row.phone in phones]
    weekly = _weekly(cases)
    districts: dict[str, dict] = {}
    for case in cases:
        bucket = districts.setdefault(case.district or "Unknown", {"district": case.district or "Unknown", "cases": 0, "amount_lkr": 0})
        bucket["cases"] += 1
        bucket["amount_lkr"] += case.amount_lkr or 0
    today = now.astimezone(COLOMBO).date()
    cases_today = sum(1 for case in cases if aware(case.created_at).astimezone(COLOMBO).date() == today)
    bands = {"crit": 0, "high": 0, "med": 0, "low": 0}
    for case in cases:
        score = case.risk_score or 0
        if score >= 85:
            bands["crit"] += 1
        elif score >= 70:
            bands["high"] += 1
        elif score >= 40:
            bands["med"] += 1
        else:
            bands["low"] += 1
    reply_samples: list[float] = []
    with session_scope() as session:
        answers = session.query(CaseEvent).filter(CaseEvent.agent == "customer", CaseEvent.state == "done").all()
        by_case = {case.id: case for case in cases}
        for event in answers:
            case = by_case.get(event.case_id)
            if case is None:
                continue
            start = case.waiting_since or case.created_at
            delta = (aware(event.created_at) - aware(start)).total_seconds()
            if 0 <= delta <= 24 * 3600:
                reply_samples.append(delta)
    avg_reply = int(sum(reply_samples) / len(reply_samples)) if reply_samples else None
    queue = sorted(open_cases, key=lambda case: aware(case.updated_at), reverse=True)[:8]
    top = sorted(cases, key=lambda case: (case.amount_lkr or 0, case.risk_score or 0), reverse=True)[:8]
    by_bank: dict[str, dict] = {}
    for case in cases:
        bank_row = by_bank.setdefault(case.bank or "Unknown", {"bank": case.bank or "Unknown", "cases": 0, "amount_lkr": 0})
        bank_row["cases"] += 1
        bank_row["amount_lkr"] += case.amount_lkr or 0
    return {
        "kpis": {
            "cases": len(cases),
            "open_cases": len(open_cases),
            "waiting": len(waiting),
            "held_lkr": sum(case.amount_lkr or 0 for case in held_cases),
            "held_count": len(held_cases),
            "sim_swaps_7d": len(sim_rows),
            "blocked_lkr": sum(case.amount_lkr or 0 for case in blocked),
            "blocked_count": len(blocked),
            "cases_today": cases_today,
            "avg_reply_seconds": avg_reply,
            "severity": bands,
        },
        "weekly": weekly,
        "queue": [_case_brief(case, users.get(case.customer_id)) for case in queue],
        "districts": sorted(districts.values(), key=lambda item: item["amount_lkr"], reverse=True)[:6],
        "by_bank": sorted(by_bank.values(), key=lambda item: item["amount_lkr"], reverse=True),
        "top_cases": [_case_brief(case, users.get(case.customer_id)) for case in top],
    }


def _weekly(cases: list[Case]) -> list[dict]:
    from app.util import COLOMBO

    today = utcnow().astimezone(COLOMBO).date()
    monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=offset) for offset in range(11, -1, -1)]
    buckets = {start: {"label": start.strftime("%d %b"), "week": start.isoformat(), "cases": 0, "blocked_lkr": 0, "held_lkr": 0} for start in starts}
    for case in cases:
        local = aware(case.created_at).astimezone(COLOMBO).date()
        start = local - timedelta(days=local.weekday())
        if start not in buckets:
            continue
        buckets[start]["cases"] += 1
        if case.status == "closed_blocked":
            buckets[start]["blocked_lkr"] += case.amount_lkr or 0
        if case.policy_decision == "hold":
            buckets[start]["held_lkr"] += case.amount_lkr or 0
    return [buckets[start] for start in starts]


@router.get("/api/cases")
def list_cases(
    user: User = Depends(require_operator),
    bank: str | None = None,
    province: str | None = None,
    risk: str | None = None,
    status: str | None = None,
):
    cases, users = _selected_cases(user, bank, province, risk)
    if status and status not in {"", "all"}:
        cases = [case for case in cases if case.status == status]
    cases.sort(key=lambda case: aware(case.updated_at), reverse=True)
    return {"cases": [_case_brief(case, users.get(case.customer_id)) for case in cases]}


@router.get("/api/cases/{case_id}")
def get_case(case_id: str, user: User = Depends(require_operator)):
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None or not _case_passes(case, user, None, None, None):
            raise HTTPException(status_code=404, detail="Case not found")
        customer = session.get(User, case.customer_id)
        tx = session.get(Transaction, case.transaction_id) if case.transaction_id else None
        events = (
            session.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id)
            .order_by(CaseEvent.created_at.asc())
            .all()
        )
        report = session.query(Report).filter(Report.case_id == case_id).one_or_none()
        return {
            "case": {
                **_case_brief(case, customer),
                "telecom_summary": case.telecom_summary,
                "banking_summary": case.banking_summary,
                "risk_summary": case.risk_summary,
                "risk_factors": case.risk_factors or [],
                "pending_prompt": case.pending_prompt,
                "provider_counts": case.provider_counts or {},
            },
            "customer": {
                "id": customer.id,
                "full_name": customer.full_name,
                "email": customer.email,
                "phone": customer.phone,
                "bank": customer.bank,
                "account_number": customer.account_number,
                "branch": customer.branch,
                "province": customer.province,
                "district": customer.district,
                "restricted": customer.restricted,
                "restricted_actions": customer.restricted_actions or [],
            },
            "transaction": None
            if tx is None
            else {
                "id": tx.id,
                "kind": tx.kind,
                "amount_lkr": tx.amount_lkr,
                "payee": tx.payee,
                "payee_bank": tx.payee_bank,
                "payee_account": tx.payee_account,
                "new_payee": tx.new_payee,
                "status": tx.status,
                "occurred_at": iso(tx.occurred_at),
            },
            "events": [event_dict(event, customer.id, case.bank) for event in events],
            "report": None if report is None else {"id": report.id, "title": report.title, "created_at": iso(report.created_at)},
        }


@router.get("/api/events")
def list_events(
    user: User = Depends(require_operator),
    case_id: str | None = None,
    limit: int = Query(default=200, le=500),
):
    with session_scope() as session:
        query = session.query(CaseEvent, Case).join(Case, Case.id == CaseEvent.case_id)
        if case_id:
            query = query.filter(CaseEvent.case_id == case_id)
        rows = query.order_by(CaseEvent.created_at.desc()).limit(limit).all()
        events = []
        for event, case in rows:
            if not _case_passes(case, user, None, None, None):
                continue
            events.append(event_dict(event, case.customer_id, case.bank))
        events.reverse()
        return {"events": events}


@router.get("/api/sim-events")
def sim_events(
    user: User = Depends(require_operator),
    bank: str | None = None,
    province: str | None = None,
):
    forced = scoped_bank(user.role, user.bank, bank)
    with session_scope() as session:
        customers = session.query(User).filter(User.role == "customer").all()
        by_phone = {}
        for customer in customers:
            if forced and customer.bank != forced:
                continue
            if province and province not in {"", "all"} and customer.province != province:
                continue
            by_phone[customer.phone] = customer
        rows = session.query(SimEvent).order_by(SimEvent.occurred_at.desc()).limit(400).all()
        events = []
        for row in rows:
            customer = by_phone.get(row.phone)
            if customer is None:
                continue
            events.append(
                {
                    "id": row.id,
                    "phone": row.phone,
                    "event_type": row.event_type,
                    "occurred_at": iso(row.occurred_at),
                    "age": age_phrase(row.occurred_at),
                    "detail": row.detail or {},
                    "origin": row.origin,
                    "case_id": row.case_id,
                    "customer_id": customer.id,
                    "customer_name": customer.full_name,
                    "bank": customer.bank,
                    "district": customer.district,
                    "province": customer.province,
                }
            )
        newest = {}
        for event in events:
            current = newest.get(event["event_type"])
            if current is None or (event["occurred_at"] or "") > (current["occurred_at"] or ""):
                newest[event["event_type"]] = event
    def source(label, kind, basis):
        event = newest.get(kind)
        return {
            "source": label,
            "granularity": "event",
            "newest": None if event is None else event["occurred_at"],
            "age": "never" if event is None else event["age"],
            "status": "Mock feed" if event else "No rows",
            "basis": basis,
        }
    sources = [
        source("CAMARA SIM swap", "sim_swap", "Mock operator feed shaped like checkSimSwap and retrieveSimSwapDate."),
        source("Handset / IMEI", "new_imei", "Mock device-change feed. Not part of the CAMARA SIM swap schema."),
        source("Location change", "location_change", "Mock serving-location feed."),
        source("Port-out", "port_out", "Mock port-out requests."),
        source("SIM freeze requests", "sim_freeze", "Raised by the response agent. The operator mock records the reference."),
    ]
    return {"sources": sources, "events": events}


@router.get("/api/holds")
def holds(
    user: User = Depends(require_operator),
    bank: str | None = None,
    province: str | None = None,
):
    forced = scoped_bank(user.role, user.bank, bank)
    with session_scope() as session:
        rows = (
            session.query(Transaction, User, Case)
            .join(User, User.id == Transaction.customer_id)
            .outerjoin(Case, Case.id == Transaction.case_id)
            .filter(Transaction.status == "held")
            .all()
        )
        items = []
        for tx, customer, case in rows:
            if forced and customer.bank != forced:
                continue
            if province and province not in {"", "all"} and customer.province != province:
                continue
            items.append(
                {
                    "transaction_id": tx.id,
                    "case_id": tx.case_id,
                    "customer_id": customer.id,
                    "customer_name": customer.full_name,
                    "bank": customer.bank,
                    "province": customer.province,
                    "district": customer.district,
                    "amount_lkr": tx.amount_lkr,
                    "payee": tx.payee,
                    "payee_bank": tx.payee_bank,
                    "new_payee": tx.new_payee,
                    "occurred_at": iso(tx.occurred_at),
                    "case_status": None if case is None else case.status,
                    "risk_score": None if case is None else case.risk_score,
                    "severity": severity(None if case is None else case.risk_score),
                    "policy_decision": None if case is None else case.policy_decision,
                    "policy_reason": None if case is None else case.policy_reason,
                    "can_override": user.role in {"bank_supervisor", "super_admin"},
                }
            )
        items.sort(key=lambda item: item["amount_lkr"], reverse=True)
        return {"holds": items}


@router.post("/api/transactions/{transaction_id}/override")
def override(transaction_id: str, body: OverrideIn, user: User = Depends(require_writer)):
    if user.role not in {"bank_supervisor", "super_admin"}:
        raise HTTPException(status_code=403, detail="Only a bank supervisor can release or block a held payment")
    if body.action not in {"release", "block"}:
        raise HTTPException(status_code=400, detail="Action must be release or block")
    with session_scope() as session:
        tx = session.get(Transaction, transaction_id)
        if tx is None or tx.status != "held":
            raise HTTPException(status_code=409, detail="Only a held payment can be overridden")
        customer = session.get(User, tx.customer_id)
        if user.role == "bank_supervisor" and user.bank and customer.bank != user.bank:
            raise HTTPException(status_code=403, detail="That payment is outside your bank")
        case = session.get(Case, tx.case_id) if tx.case_id else None
        tx_customer = tx.customer_id
        detail = dict(tx.detail or {})
        detail["override"] = {"by": user.email, "action": body.action, "reason": body.reason.strip()}
        tx.detail = detail
        if body.action == "release" and tx.kind == "transfer":
            # The same rule as ledger.release_transaction: the money leaves the account when the payment is released, once.
            customer.balance_lkr = max(0, int(customer.balance_lkr or 0) - int(tx.amount_lkr or 0))
        tx.status = "released" if body.action == "release" else "blocked"
        case_id = case.id if case else None
        if case is not None:
            case.status = "closed_released" if body.action == "release" else "closed_blocked"
            case.customer_decision = "supervisor_" + body.action
            case.pending_prompt = None
            case.updated_at = utcnow()
            note = (case.policy_reason or "") + f" Supervisor {body.action}: {body.reason.strip()}"
            case.policy_reason = note.strip()
    audit(user.email, "supervisor_override", transaction_id, f"{body.action}: {body.reason.strip()}")
    if case_id:
        from app.events import emit

        emit(
            case_id,
            agent="supervisor",
            state="done",
            tool_name="supervisor_override",
            tool_args={"action": body.action, "transaction_id": transaction_id},
            result_summary=f"Supervisor {body.action}. {body.reason.strip()}",
            provider="rules",
            model="deterministic",
        )
        # The case is closed, so the customer is told and the same record is left as for any other outcome.
        from app.agents import tools

        released = body.action == "release"
        tools.outcome_notice(
            tx_customer,
            case_id,
            "Payment released" if released else "Payment blocked",
            "After a check by the bank's fraud desk your payment was released." if released
            else "The bank's fraud desk blocked this transfer and no money left your account. Call your bank on the number printed on your card.",
        )
        tools.review_notes(case_id)
        tools.generate_report(case_id)
        tools.deliver_report(case_id)
    return {"ok": True, "status": "released" if body.action == "release" else "blocked"}


@router.get("/api/map")
def map_data(
    user: User = Depends(require_operator),
    bank: str | None = None,
    province: str | None = None,
    risk: str | None = None,
):
    forced = scoped_bank(user.role, user.bank, bank)
    with session_scope() as session:
        customers = session.query(User).filter(User.role == "customer").all()
        cases = session.query(Case).all()
        events = session.query(SimEvent).order_by(SimEvent.occurred_at.desc()).limit(200).all()
    by_customer: dict[str, list[Case]] = {}
    for case in cases:
        by_customer.setdefault(case.customer_id, []).append(case)
    points = []
    for customer in customers:
        if customer.lat is None or customer.lng is None:
            continue  # a customer with no known position cannot be drawn
        if forced and customer.bank != forced:
            continue
        if province and province not in {"", "all"} and customer.province != province:
            continue
        related = by_customer.get(customer.id, [])
        if risk and risk not in {"", "all"}:
            if not any(severity(case.risk_score) == risk for case in related):
                continue
        open_cases = [case for case in related if case.status not in {"closed_blocked", "closed_released", "closed_cleared"}]
        score = max((case.risk_score or 0) for case in related) if related else 0
        points.append(
            {
                "id": customer.id,
                "name": customer.full_name,
                "bank": customer.bank,
                "province": customer.province,
                "district": customer.district,
                "lat": customer.lat,
                "lng": customer.lng,
                "risk_score": score,
                "severity": severity(score),
                "open_cases": len(open_cases),
                "held_lkr": sum(case.amount_lkr or 0 for case in open_cases if case.policy_decision == "hold"),
            }
        )
    phones = {customer.phone: customer for customer in customers}
    sim_points = []
    for event in events:
        customer = phones.get(event.phone)
        if customer is None or customer.lat is None:
            continue
        if forced and customer.bank != forced:
            continue
        sim_points.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "lat": customer.lat,
                "lng": customer.lng,
                "district": customer.district,
                "occurred_at": iso(event.occurred_at),
                "customer_name": customer.full_name,
            }
        )
    return {"customers": points, "sim_events": sim_points}


@router.get("/api/agents")
def agents(user: User = Depends(require_operator)):
    settings = runtime.settings
    configured = "meta:" + settings.meta_model if settings.meta_api_key else ("grok:" + settings.xai_model if settings.xai_api_key else "mock-deterministic")
    fallback = "grok:" + settings.xai_model if settings.meta_api_key and settings.xai_api_key else "mock-deterministic"
    with session_scope() as session:
        rows = session.query(CaseEvent).filter(CaseEvent.phase.is_distinct_from("started")).all()  # a start marker is not a finished call
    grouped: dict[str, list[CaseEvent]] = {}
    for row in rows:
        grouped.setdefault(row.agent, []).append(row)
    payload = []
    for spec in AGENT_CATALOG:
        events = grouped.get(spec["id"], [])
        errors = [event for event in events if event.state == "error"]
        calls = [event for event in events if event.state in {"done", "calling_tool", "error"}]
        latencies = [event.latency_ms for event in events if event.latency_ms is not None]
        fallbacks = [event for event in events if event.fell_back]
        provided = [event for event in events if event.provider]
        provided.sort(key=lambda event: aware(event.created_at))
        last = provided[-1] if provided else None
        success = None
        if calls:
            success = round(100 * (len(calls) - len(errors)) / len(calls), 1)
        payload.append(
            {
                **spec,
                "configured_model": "deterministic" if spec["kind"] == "rules" else configured,
                "fallback_model": None if spec["kind"] == "rules" else fallback,
                "last_provider": None if last is None else last.provider,
                "last_model": None if last is None else last.model,
                "calls": len(calls),
                "errors": len(errors),
                "success_rate": success,
                "latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
                "fallbacks": len(fallbacks),
            }
        )
    return {"agents": payload, "primary": health()["llm"]}


@router.get("/api/reports")
def reports(user: User = Depends(require_operator)):
    with session_scope() as session:
        rows = session.query(Report, Case).join(Case, Case.id == Report.case_id).all()
        items = []
        for report, case in rows:
            if not _case_passes(case, user, None, None, None):
                continue
            items.append(
                {
                    "id": report.id,
                    "case_id": case.id,
                    "title": report.title,
                    "created_at": iso(report.created_at),
                    "bank": case.bank,
                    "status": case.status,
                    "amount_lkr": case.amount_lkr,
                    "customer_id": case.customer_id,
                }
            )
        items.sort(key=lambda item: item["created_at"] or "", reverse=True)
        return {"reports": items}


def _report_for(user: User, report_id: str) -> tuple[Report, Case]:
    with session_scope() as session:
        report = session.get(Report, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        case = session.get(Case, report.case_id)
        if case is None or not _case_passes(case, user, None, None, None):
            raise HTTPException(status_code=404, detail="Report not found")
        session.expunge(report)
        session.expunge(case)
        return report, case


@router.get("/api/reports/{report_id}")
def report_detail(report_id: str, user: User = Depends(require_operator)):
    report, case = _report_for(user, report_id)
    return {
        "id": report.id,
        "case_id": case.id,
        "title": report.title,
        "markdown": report.markdown,
        "created_at": iso(report.created_at),
    }


@router.get("/api/reports/{report_id}/markdown")
def report_markdown(report_id: str, user: User = Depends(require_operator)):
    report, _case = _report_for(user, report_id)
    audit(user.email, "report_download", report.case_id, "markdown")
    return PlainTextResponse(report.markdown, media_type="text/markdown")


@router.get("/api/reports/{report_id}/pdf")
def report_pdf(report_id: str, user: User = Depends(require_operator)):
    report, _case = _report_for(user, report_id)
    audit(user.email, "report_download", report.case_id, "pdf")
    return _pdf_response(report.case_id, "analyst", report.sha256)


@router.get("/api/settings")
def settings_view(user: User = Depends(require_operator)):
    body = health()
    body["step_delay_ms"] = runtime.settings.step_delay_ms
    body["demo_assist"] = runtime.settings.demo_assist
    body["telco"] = "camara-http" if runtime.settings.telco_base_url else "camara-mock"
    body["actor"] = _public_user(user)
    return body
