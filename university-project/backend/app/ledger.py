from app.db import session_scope
from app.models import AuditLog, Case, SimEvent, Transaction, User
from app.util import nid, utcnow


def set_case_fields(case_id: str, **fields) -> None:
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None:
            return
        for key, value in fields.items():
            setattr(case, key, value)
        case.updated_at = utcnow()


def bump_provider(case_id: str, provider: str) -> None:
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None:
            return
        counts = dict(case.provider_counts or {})
        counts[provider] = int(counts.get(provider, 0)) + 1
        case.provider_counts = counts
        case.updated_at = utcnow()


def hold_transaction(transaction_id: str, case_id: str) -> dict:
    with session_scope() as session:
        tx = session.get(Transaction, transaction_id)
        if tx is None:
            raise LookupError(transaction_id)
        if tx.status in {"blocked", "released"}:
            return {"transaction_id": tx.id, "status": tx.status, "changed": False}
        tx.status = "held"
        tx.case_id = case_id
        return {
            "transaction_id": tx.id,
            "status": "held",
            "changed": True,
            "amount_lkr": tx.amount_lkr,
            "payee": tx.payee,
        }


def block_transaction(transaction_id: str, case_id: str, reason: str) -> dict:
    with session_scope() as session:
        tx = session.get(Transaction, transaction_id)
        if tx is None:
            raise LookupError(transaction_id)
        detail = dict(tx.detail or {})
        detail["block_reason"] = reason
        tx.detail = detail
        tx.status = "blocked"
        tx.case_id = case_id
        return {"transaction_id": tx.id, "status": "blocked", "amount_lkr": tx.amount_lkr, "payee": tx.payee}


def release_transaction(transaction_id: str, case_id: str, reason: str) -> dict:
    with session_scope() as session:
        tx = session.get(Transaction, transaction_id)
        if tx is None:
            raise LookupError(transaction_id)
        if tx.status == "blocked":
            return {"transaction_id": tx.id, "status": "blocked", "changed": False, "note": "Already blocked"}
        detail = dict(tx.detail or {})
        detail["release_reason"] = reason
        tx.detail = detail
        first_release = tx.status != "released"
        tx.status = "released"
        tx.case_id = case_id
        if first_release and tx.kind == "transfer":
            # The money leaves the account only when the payment is released, and only once.
            from app.models import User

            owner = session.get(User, tx.customer_id)
            if owner is not None:
                owner.balance_lkr = max(0, int(owner.balance_lkr or 0) - int(tx.amount_lkr or 0))
        return {"transaction_id": tx.id, "status": "released", "changed": True, "amount_lkr": tx.amount_lkr}


def lock_account(customer_id: str, case_id: str) -> dict:
    actions = ["transfer", "new_payee", "password_reset", "port_out"]
    with session_scope() as session:
        user = session.get(User, customer_id)
        if user is None:
            raise LookupError(customer_id)
        user.restricted = True
        user.restricted_actions = actions
        user.restricted_reason = f"demo:{case_id}"
        return {"customer_id": customer_id, "restricted": True, "actions": actions}


def request_sim_freeze(phone: str, case_id: str, reason: str) -> dict:
    reference = "FRZ-" + nid("x").split("_")[-1][:8].upper()
    with session_scope() as session:
        session.add(
            SimEvent(
                id=nid("sim"),
                phone=phone,
                event_type="sim_freeze",
                occurred_at=utcnow(),
                detail={"reference": reference, "reason": reason, "status": "freeze_requested"},
                origin="demo",
                case_id=case_id,
            )
        )
        case = session.get(Case, case_id)
        if case is not None:
            case.sim_freeze_ref = reference
            case.updated_at = utcnow()
    return {"status": "freeze_requested", "reference": reference, "phoneNumber": phone}


def _stamp(moment) -> str:
    from app.util import aware

    value = aware(moment)
    if value is None:
        return ""
    return value.astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def verify_audit_chain() -> dict:
    import hashlib

    with session_scope() as session:
        rows = session.query(AuditLog).order_by(AuditLog.created_at.asc()).all()
        previous = "genesis"
        checked = 0
        for row in rows:
            if not row.entry_hash:
                continue
            material = "|".join([
                row.prev_hash or "genesis",
                row.actor_email or "",
                row.action or "",
                row.target or "",
                row.detail or "",
                _stamp(row.created_at),
            ])
            digest = hashlib.sha256(material.encode()).hexdigest()
            if row.prev_hash != previous or digest != row.entry_hash:
                return {"ok": False, "checked": checked, "broken_at": row.id}
            previous = row.entry_hash
            checked += 1
    return {"ok": True, "checked": checked}


def audit(actor_email: str, action: str, target: str | None, detail: str | None) -> None:
    import hashlib

    with session_scope() as session:
        previous = session.query(AuditLog).order_by(AuditLog.created_at.desc()).first()
        prev_hash = previous.entry_hash if previous and previous.entry_hash else "genesis"
        created = utcnow()
        material = "|".join([prev_hash, actor_email or "", action or "", target or "", detail or "", _stamp(created)])
        entry_hash = hashlib.sha256(material.encode()).hexdigest()
        session.add(
            AuditLog(
                id=nid("aud"),
                actor_email=actor_email,
                action=action,
                target=target,
                detail=detail,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
                created_at=created,
            )
        )
