"""admin routes (split from app/api.py)."""
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel

from app.db import session_scope
from app.ledger import audit
from app.models import (
    AccessRequest,
    AuditLog,
    Invitation,
    User,
)
from app.routes.common import _public_user, require_operator, require_writer
from app.util import (
    OPERATOR_ROLES,
    colombo_stamp,
    iso,
    nid,
)

log = logging.getLogger("payshield.api")
router = APIRouter()


class DecideIn(BaseModel):
    decision: str


class UserStatusIn(BaseModel):
    status: str


@router.get("/api/admin/summary")
def admin_summary(user: User = Depends(require_operator)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Administration is limited to the super admin")
    with session_scope() as session:
        requests = session.query(AccessRequest).all()
        users = session.query(User).filter(User.role != "customer").all()
        audits = session.query(AuditLog).count()
        recent_refused = session.query(AuditLog).filter(AuditLog.action == "access_refused").count()
        return {
            "pending_requests": sum(1 for row in requests if row.state == "pending"),
            "users": len(users),
            "disabled_users": sum(1 for row in users if row.status != "active"),
            "active_users": sum(1 for row in users if row.status == "active"),
            "audit_entries": audits,
            "refused_recent": recent_refused,
        }


@router.get("/api/admin/requests")
def admin_requests(user: User = Depends(require_operator)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Administration is limited to the super admin")
    with session_scope() as session:
        rows = session.query(AccessRequest).order_by(AccessRequest.created_at.desc()).all()
        return {
            "requests": [
                {
                    "id": row.id,
                    "name": row.name,
                    "email": row.email,
                    "organisation": row.organisation,
                    "role_asked": row.role_asked,
                    "scope_asked": row.scope_asked,
                    "reason": row.reason,
                    "state": row.state,
                    "decided_by": row.decided_by,
                    "created_at": iso(row.created_at),
                }
                for row in rows
            ]
        }


@router.post("/api/admin/requests/{request_id}")
def decide_request(request_id: str, body: DecideIn, user: User = Depends(require_writer)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Administration is limited to the super admin")
    if body.decision not in {"approve", "refuse"}:
        raise HTTPException(status_code=400, detail="Decision must be approve or refuse")
    invite_token = None
    with session_scope() as session:
        row = session.get(AccessRequest, request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Request not found")
        if row.state != "pending":
            raise HTTPException(status_code=409, detail="That request is already decided")
        row.state = "approved" if body.decision == "approve" else "refused"
        row.decided_by = user.email
        if body.decision == "approve" and session.query(User).filter(User.email == row.email).one_or_none() is None:
            invite_token = "PS-" + nid("inv").split("_")[-1][:8].upper()
            session.add(
                Invitation(
                    token=invite_token,
                    email=row.email,
                    full_name=row.name,
                    role=row.role_asked if row.role_asked in OPERATOR_ROLES else "fraud_analyst",
                    bank=None,
                    used=False,
                )
            )
        request_email = row.email
    audit(user.email, "access_" + body.decision, request_id, request_email)
    return {"state": "approved" if body.decision == "approve" else "refused", "invite_token": invite_token}


@router.get("/api/admin/users")
def admin_users(user: User = Depends(require_operator)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Administration is limited to the super admin")
    with session_scope() as session:
        rows = session.query(User).filter(User.role != "customer").order_by(User.full_name.asc()).all()
        return {"users": [_public_user(row) for row in rows]}


@router.post("/api/admin/users/{user_id}")
def set_user_status(user_id: str, body: UserStatusIn, user: User = Depends(require_writer)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Administration is limited to the super admin")
    if body.status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="Status must be active or disabled")
    if user_id == user.id and body.status == "disabled":
        raise HTTPException(status_code=409, detail="You cannot disable your own account")
    with session_scope() as session:
        target = session.get(User, user_id)
        if target is None or target.role == "customer":
            raise HTTPException(status_code=404, detail="User not found")
        target.status = body.status
    audit(user.email, "user_" + body.status, user_id, body.status)
    return {"ok": True}


@router.get("/api/admin/audit")
def admin_audit(user: User = Depends(require_operator)):
    if user.role not in {"super_admin", "auditor"}:
        raise HTTPException(status_code=403, detail="The audit log is limited to the super admin and the auditor")
    with session_scope() as session:
        rows = session.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()
        return {
            "entries": [
                {
                    "id": row.id,
                    "actor_email": row.actor_email,
                    "action": row.action,
                    "target": row.target,
                    "detail": row.detail,
                    "created_at": iso(row.created_at),
                    "created_label": colombo_stamp(row.created_at),
                }
                for row in rows
            ]
        }
