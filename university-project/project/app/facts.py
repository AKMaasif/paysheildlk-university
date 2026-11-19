from datetime import timedelta

from app.db import session_scope
from app.models import SimEvent, Transaction, User
from app.risk import Signals
from app.util import age_hours, canonical_phone, iso, utcnow, valid_e164


class UnknownPhone(LookupError):
    pass


class InvalidPhone(ValueError):
    pass


def _customer_for_phone(session, phone: str) -> User:
    canonical = canonical_phone(phone)
    if not valid_e164(canonical):
        raise InvalidPhone(phone)
    user = (
        session.query(User)
        .filter(User.phone == canonical, User.role == "customer")
        .one_or_none()
    )
    if user is None:
        raise UnknownPhone(canonical)
    return user


def _latest(events: list[SimEvent], kind: str) -> SimEvent | None:
    rows = [event for event in events if event.event_type == kind]
    if not rows:
        return None
    return max(rows, key=lambda event: event.occurred_at)


def check_sim_swap(phone: str, max_age: int | None) -> dict:
    window = 240 if max_age is None else max_age
    if window < 1 or window > 2400:
        raise ValueError("maxAge must be between 1 and 2400 hours")
    now = utcnow()
    with session_scope() as session:
        user = _customer_for_phone(session, phone)
        events = session.query(SimEvent).filter(SimEvent.phone == user.phone).all()
        latest = _latest(events, "sim_swap")
        changed = None if latest is None else latest.occurred_at
        swapped = changed is not None and (age_hours(changed, now) or 0) <= window
        return {
            "phoneNumber": user.phone,
            "swapped": swapped,
            "maxAge": window,
            "latestSimChange": iso(changed),
        }


def retrieve_sim_swap_date(phone: str) -> dict:
    with session_scope() as session:
        user = _customer_for_phone(session, phone)
        events = session.query(SimEvent).filter(SimEvent.phone == user.phone, SimEvent.event_type == "sim_swap").all()
        latest = _latest(events, "sim_swap")
        return {"phoneNumber": user.phone, "latestSimChange": iso(latest.occurred_at) if latest else None}


def recent_transactions(customer_id: str, limit: int = 12) -> list[dict]:
    with session_scope() as session:
        rows = (
            session.query(Transaction)
            .filter(Transaction.customer_id == customer_id)
            .order_by(Transaction.occurred_at.desc())
            .limit(limit)
            .all()
        )
        return [_tx_dict(row) for row in rows]


def _tx_dict(row: Transaction) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "amount_lkr": row.amount_lkr,
        "payee": row.payee,
        "payee_bank": row.payee_bank,
        "new_payee": row.new_payee,
        "status": row.status,
        "occurred_at": iso(row.occurred_at),
        "case_id": row.case_id,
    }


def _open_port(event: SimEvent | None, now) -> bool:
    if event is None:
        return False
    if (age_hours(event.occurred_at, now) or 0) > 24 * 7:
        return False
    detail = event.detail or {}
    if detail.get("cancelled") or detail.get("status") == "cancelled":
        return False
    return True


def collect_signals(customer_id: str, transaction_id: str) -> tuple[Signals, dict]:
    now = utcnow()
    with session_scope() as session:
        user = session.get(User, customer_id)
        tx = session.get(Transaction, transaction_id)
        if user is None or tx is None:
            raise LookupError("customer or transaction missing")
        events = session.query(SimEvent).filter(SimEvent.phone == user.phone).all()
        swap = _latest(events, "sim_swap")
        imei = _latest(events, "new_imei")
        location = _latest(events, "location_change")
        port = _latest(events, "port_out")
        resets = (
            session.query(Transaction)
            .filter(Transaction.customer_id == customer_id, Transaction.kind == "password_reset")
            .all()
        )
        reset_age = None
        if resets:
            latest_reset = max(resets, key=lambda item: item.occurred_at)
            reset_age = age_hours(latest_reset.occurred_at, now)
        logins = (
            session.query(Transaction)
            .filter(
                Transaction.customer_id == customer_id,
                Transaction.kind == "login",
                Transaction.occurred_at >= now - timedelta(hours=24),
            )
            .all()
        )
        new_device = any((item.detail or {}).get("new_device") for item in logins)
        swap_age = age_hours(swap.occurred_at, now) if swap else None
        signals = Signals(
            sim_swap_age_hours=swap_age,
            new_imei_age_hours=age_hours(imei.occurred_at, now) if imei else None,
            location_change_age_hours=age_hours(location.occurred_at, now) if location else None,
            location_note=(location.detail or {}).get("to") if location else None,
            port_out=_open_port(port, now),
            new_payee=bool(tx.new_payee),
            amount_lkr=tx.amount_lkr or 0,
            password_reset_age_hours=reset_age,
            new_device_login=new_device,
            latest_sim_change=iso(swap.occurred_at) if swap else None,
            swapped=swap_age is not None and swap_age <= 240,
        )
        context = {
            "customer": user.full_name,
            "phone": user.phone,
            "bank": user.bank,
            "payee": tx.payee,
            "amount_lkr": tx.amount_lkr,
            "new_payee": bool(tx.new_payee),
            "swapped": "yes" if signals.swapped else "no",
            "latest_sim_change": signals.latest_sim_change,
            "new_imei": None if imei is None else (imei.detail or {}),
            "location": None if location is None else (location.detail or {}),
            "port_out": signals.port_out,
            "transactions": [_tx_dict(item) for item in session.query(Transaction).filter(Transaction.customer_id == customer_id).order_by(Transaction.occurred_at.desc()).limit(8)],
        }
        return signals, context
