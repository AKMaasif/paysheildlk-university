"""stream routes (split from app/api.py)."""
import asyncio
import logging

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
)

from app.db import session_scope
from app.events import event_dict
from app.models import (
    Case,
    CaseEvent,
    User,
)
from app.runtime import runtime
from app.security import (
    decode_token,
    is_revoked,
)
from app.util import (
    OPERATOR_ROLES,
)

log = logging.getLogger("payshield.api")
router = APIRouter()


def _visible(role: str, user_id: str, bank: str | None, event: dict) -> bool:
    if role == "customer":
        return event.get("customer_id") == user_id
    if role == "bank_supervisor" and bank:
        return event.get("bank") in {bank, None}
    return role in OPERATOR_ROLES


def _snapshot(role: str, user_id: str, bank: str | None) -> list[dict]:
    with session_scope() as session:
        rows = (
            session.query(CaseEvent, Case)
            .join(Case, Case.id == CaseEvent.case_id)
            .order_by(CaseEvent.created_at.desc())
            .limit(250)
            .all()
        )
        events = []
        for event, case in rows:
            payload = event_dict(event, case.customer_id, case.bank)
            if _visible(role, user_id, bank, payload):
                events.append(payload)
        events.reverse()
        return events


@router.websocket("/ws")
async def stream(websocket: WebSocket):
    await websocket.accept()
    from app import sessions

    token = websocket.query_params.get("token")
    if not token:
        # A browser signed in with cookies sends them with the connection. Because cookies travel by themselves, the
        # page that opened the connection must be one of ours: another site's page is refused.
        portal = "customer" if websocket.query_params.get("portal") == "customer" else "operator"
        token = websocket.cookies.get(sessions.names(portal)[0])
        origin = websocket.headers.get("origin")
        if token and origin and origin.rstrip("/") not in runtime.settings.allowed_origins:
            await websocket.close(code=4403)
            return
    if not token:
        await websocket.close(code=4401)
        return
    try:
        payload = decode_token(runtime.settings, token, purpose="access")
    except Exception:
        await websocket.close(code=4401)
        return
    with session_scope() as session:
        user = session.get(User, payload["sub"])
        if user is None or user.status != "active" or is_revoked(session, payload, user):
            await websocket.close(code=4401)
            return
        role, user_id, bank = user.role, user.id, user.bank
    queue = runtime.bus.subscribe()
    closed = asyncio.ensure_future(_until_closed(websocket))
    try:
        await websocket.send_json({"type": "snapshot", "events": _snapshot(role, user_id, bank)})
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _pending = await asyncio.wait({getter, closed}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
            if closed in done:
                getter.cancel()
                break  # the client left: stop at once rather than at the next ping
            if getter not in done:
                getter.cancel()
                await websocket.send_json({"type": "ping"})
                continue
            event = getter.result()
            if not _visible(role, user_id, bank, event):
                continue
            await websocket.send_json({"type": "event", "event": event})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.info("websocket closed")
    finally:
        closed.cancel()
        runtime.bus.unsubscribe(queue)


async def _until_closed(websocket: WebSocket) -> None:
    """Returns when the client disconnects. Anything the client sends is read and ignored."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
