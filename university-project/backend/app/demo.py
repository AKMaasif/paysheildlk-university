from datetime import timedelta

from app.db import session_scope
from app.models import (
    Case,
    CaseEvent,
    Notification,
    Report,
    SimEvent,
    Transaction,
    User,
)
from app.seed import PILOT_ANSWERS
from app.util import nid, utcnow


def next_case_id(session) -> str:
    nums = []
    for (value,) in session.query(Case.id).all():
        try:
            nums.append(int(str(value).split("-")[-1]))
        except ValueError:
            continue
    return f"PS-26-{max(nums, default=30) + 1:05d}"


def trigger(customer_id: str, scenario: str) -> str:
    with session_scope() as session:
        user = session.get(User, customer_id)
        if user is None or user.role != "customer":
            raise LookupError("Unknown customer")
        now = utcnow()
        case_id = next_case_id(session)
        tx_id = "TX-" + nid("t").split("_")[-1][:6].upper()
        if scenario == "sim_swap_transfer":
            _sim(session, user.phone, "sim_swap", now - timedelta(minutes=25), {"channel": "ussd", "previous_imsi_suffix": "4412", "new_imsi_suffix": "9081"}, case_id)
            _sim(session, user.phone, "new_imei", now - timedelta(minutes=24), {"imei_masked": "35693810....441", "model": "Samsung Galaxy A15"}, case_id)
            _sim(session, user.phone, "location_change", now - timedelta(minutes=20), {"from": user.district, "to": "Negombo"}, case_id)
            session.add(_tx(user.id, "login", 0, None, False, now - timedelta(minutes=30), "completed", None, {"new_device": True}))
            session.add(_tx(user.id, "new_payee", 0, "K.D. Enterprises", True, now - timedelta(minutes=12), "completed", None, {}))
            tx = _tx(user.id, "transfer", 200_000, "K.D. Enterprises", True, now, "pending", case_id, {"payee_account": "20938411"})
            tx.id = tx_id
            tx.payee_bank = "BankCylone"
            tx.payee_account = "20938411"
            amount, payee = 200_000, "K.D. Enterprises"
        elif scenario == "port_out_critical":
            _sim(session, user.phone, "sim_swap", now - timedelta(hours=2), {"channel": "store"}, case_id)
            _sim(session, user.phone, "port_out", now - timedelta(minutes=40), {"status": "requested", "recipient": "Other operator"}, case_id)
            session.add(_tx(user.id, "password_reset", 0, None, False, now - timedelta(minutes=30), "completed", None, {}))
            session.add(_tx(user.id, "new_payee", 0, "A.R. Imports", True, now - timedelta(minutes=20), "completed", None, {}))
            tx = _tx(user.id, "transfer", 800_000, "A.R. Imports", True, now, "pending", case_id, {})
            tx.id = tx_id
            tx.payee_bank = "BankCylone"
            amount, payee = 800_000, "A.R. Imports"
        elif scenario == "large_payment":
            # No SIM signal and no new payee: the normal rules release this. Emergency mode is what holds it.
            tx = _tx(user.id, "transfer", 75_000, "Ceylon Traders", False, now, "pending", case_id, {})
            tx.id = tx_id
            tx.payee_bank = user.bank
            amount, payee = 75_000, "Ceylon Traders"
        elif scenario == "routine_payment":
            tx = _tx(user.id, "transfer", 4850, "CEB Electricity", False, now, "pending", case_id, {})
            tx.id = tx_id
            tx.payee_bank = user.bank
            amount, payee = 4850, "CEB Electricity"
        else:
            raise ValueError("Unknown scenario")
        session.add(tx)
        session.add(
            Case(
                id=case_id,
                customer_id=user.id,
                transaction_id=tx.id,
                status="new",
                created_at=now,
                updated_at=now,
                alert_count=0,
                origin="demo",
                scenario=scenario,
                amount_lkr=amount,
                payee=payee,
                bank=user.bank,
                province=user.province,
                district=user.district,
                provider_counts={},
            )
        )
        return case_id


def _sim(session, phone, kind, when, detail, case_id) -> None:
    session.add(
        SimEvent(
            id=nid("sim"),
            phone=phone,
            event_type=kind,
            occurred_at=when,
            detail=detail,
            origin="demo",
            case_id=case_id,
            received_at=utcnow(),  # it happened a little while ago; this system took it in now
        )
    )


def _tx(customer_id, kind, amount, payee, new_payee, when, status, case_id, detail) -> Transaction:
    return Transaction(
        id=nid("tx"),
        customer_id=customer_id,
        kind=kind,
        amount_lkr=amount,
        payee=payee,
        new_payee=new_payee,
        status=status,
        occurred_at=when,
        case_id=case_id,
        origin="demo",
        detail=detail,
    )


def reset_demo() -> dict:
    with session_scope() as session:
        case_ids = [row[0] for row in session.query(Case.id).filter(Case.origin == "demo").all()]
        if case_ids:
            session.query(CaseEvent).filter(CaseEvent.case_id.in_(case_ids)).delete(synchronize_session=False)
            session.query(Report).filter(Report.case_id.in_(case_ids)).delete(synchronize_session=False)
            session.query(Notification).filter(Notification.case_id.in_(case_ids)).delete(synchronize_session=False)
            session.query(Case).filter(Case.id.in_(case_ids)).delete(synchronize_session=False)
        session.query(Transaction).filter(Transaction.origin == "demo").delete(synchronize_session=False)
        session.query(SimEvent).filter(SimEvent.origin == "demo").delete(synchronize_session=False)
        cleared = 0
        for user in session.query(User).filter(User.restricted.is_(True)).all():
            if (user.restricted_reason or "").startswith("demo:"):
                user.restricted = False
                user.restricted_actions = []
                user.restricted_reason = None
                cleared += 1
        from app import faults

        # Drill payments are origin "demo", so they were removed above. An analyst's own emergency switch stays.
        return {"cases_removed": len(case_ids), "accounts_cleared": cleared, "faults_cleared": faults.clear_all()}


def assist(customer_id: str) -> dict:
    """What a presenter types into the registration form, and the answers to the security questions."""
    from app.runtime import runtime

    if not runtime.settings.demo_assist:
        raise PermissionError("Demo assist is off")
    with session_scope() as session:
        user = session.get(User, customer_id)
        if user is None or user.role != "customer":
            raise LookupError(customer_id)
        record = {
            "customer_id": customer_id,
            "full_name": user.full_name,
            "email": user.email,
            "nic": user.nic,
            "phone": user.phone,
            "account_number": user.account_number,
            "bank": user.bank,
            "registered": bool(user.registered),
            "username": user.username,
        }
    return {
        **record,
        "answers": PILOT_ANSWERS.get(customer_id, {}),
        "note": "Fictional customer. Type these details into the registration form. The face scan uses the phone camera.",
    }


def unregister(customer_id: str) -> bool:
    """Put a customer back to the first-time state so the registration can be shown again."""
    import secrets

    from app.security import hash_password

    with session_scope() as session:
        user = session.get(User, customer_id)
        if user is None or user.role != "customer":
            return False
        user.registered = False
        user.registered_at = None
        user.username = None
        user.face_ref = None
        user.face_hash = None
        user.must_change_password = False
        user.password_changed_at = None
        user.password_hash = hash_password(secrets.token_urlsafe(24))
        return True


def list_customers() -> list[dict]:
    with session_scope() as session:
        rows = session.query(User).filter(User.role == "customer").order_by(User.full_name.asc()).all()
        return [
            {
                "id": row.id,
                "full_name": row.full_name,
                "email": row.email,
                "phone": row.phone,
                "bank": row.bank,
                "province": row.province,
                "district": row.district,
                "restricted": row.restricted,
                "registered": bool(row.registered),
            }
            for row in rows
        ]
