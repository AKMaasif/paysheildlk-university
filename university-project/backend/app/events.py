import contextvars
import json
import logging
import threading
from collections import deque

from app.db import session_scope
from app.models import Case, CaseEvent
from app.util import iso, nid, utcnow

log = logging.getLogger("payshield.events")

# One id for each run of the workflow on a case (the first run, then each resume, reminder or escalation). Every event
# written during that run carries it, so a presenter can pull out exactly what one run did.
run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("payshield_run_id", default=None)


def start_run(prefix: str = "run") -> str:
    value = nid(prefix)
    run_id.set(value)
    return value


class EventBus:
    def __init__(self):
        self.loop = None
        self.subscribers: list = []
        self.lock = threading.Lock()
        self.seen: set[str] = set()
        self.recent_ids: deque[str] = deque(maxlen=4000)

    def bind(self, loop) -> None:
        self.loop = loop

    def subscribe(self):
        import asyncio

        queue = asyncio.Queue(maxsize=400)
        with self.lock:
            self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue) -> None:
        with self.lock:
            if queue in self.subscribers:
                self.subscribers.remove(queue)

    def _remember(self, event_id: str) -> None:
        if event_id in self.seen:
            return
        self.recent_ids.append(event_id)
        self.seen.add(event_id)
        if len(self.seen) > 4500:
            self.seen = set(self.recent_ids)

    def publish_live(self, payload: dict) -> None:
        event_id = payload.get("id")
        if event_id:
            self._remember(event_id)
        self._fanout(payload)

    def observe_remote(self, payload: dict) -> None:
        event_id = payload.get("id")
        if event_id and event_id in self.seen:
            return
        if event_id:
            self._remember(event_id)
        self._fanout(payload)

    def _fanout(self, payload: dict) -> None:
        if self.loop is None:
            return
        with self.lock:
            subscribers = list(self.subscribers)
        for queue in subscribers:
            self.loop.call_soon_threadsafe(self._offer, queue, payload)

    @staticmethod
    def _offer(queue, payload: dict) -> None:
        try:
            queue.put_nowait(payload)
        except Exception:
            try:
                queue.get_nowait()
                queue.put_nowait(payload)
            except Exception:
                log.debug("a live-event subscriber is gone; the event is dropped for it")


def emit(
    case_id: str,
    *,
    agent: str,
    state: str,
    tool_name: str | None = None,
    tool_args: dict | None = None,
    result_summary: str | None = None,
    handoff_from: str | None = None,
    handoff_to: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    fell_back: bool = False,
    fallback_from: str | None = None,
    latency_ms: int | None = None,
    message: str | None = None,
    phase: str | None = None,
    call_id: str | None = None,
    result: dict | None = None,
    meta: dict | None = None,
) -> dict:
    from app.privacy import redact
    from app.runtime import runtime

    event_id = nid("evt")
    created = utcnow()
    with session_scope() as session:
        case = session.get(Case, case_id)
        customer_id = case.customer_id if case else None
        bank = case.bank if case else None
        # Identifiers that tie one event to the rest of the case, added to whatever the caller says.
        meta = {**(meta or {}), "case_id": case_id, "transaction_id": case.transaction_id if case else None, "run_id": run_id.get()}
        result = redact(result) if result is not None else None
        row = CaseEvent(
            id=event_id,
            case_id=case_id,
            agent=agent,
            state=state,
            tool_name=tool_name,
            tool_args=tool_args,
            result_summary=result_summary,
            handoff_from=handoff_from,
            handoff_to=handoff_to,
            provider=provider,
            model=model,
            fell_back=fell_back,
            fallback_from=fallback_from,
            latency_ms=latency_ms,
            message=message,
            created_at=created,
            phase=phase,
            call_id=call_id,
            result=result,
            meta=meta,
        )
        session.add(row)
        session.flush()
        payload = {
            "id": event_id,
            "case_id": case_id,
            "customer_id": customer_id,
            "bank": bank,
            "agent": agent,
            "state": state,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "result_summary": result_summary,
            "handoff_from": handoff_from,
            "handoff_to": handoff_to,
            "provider": provider,
            "model": model,
            "fell_back": fell_back,
            "fallback_from": fallback_from,
            "latency_ms": latency_ms,
            "message": message,
            "created_at": iso(created),
            "phase": phase,
            "call_id": call_id,
            "result": result,
            "meta": meta,
        }
    runtime.bus.publish_live(payload)
    client = runtime.redis
    if client is not None:
        try:
            encoded = json.dumps(payload)
            client.xadd("payshield:events", {"data": encoded}, maxlen=5000, approximate=True)
            client.publish("payshield:live", encoded)
        except Exception:
            log.warning("redis publish failed", exc_info=True)
    return payload


def event_dict(row: CaseEvent, customer_id: str | None = None, bank: str | None = None) -> dict:
    return {
        "id": row.id,
        "case_id": row.case_id,
        "customer_id": customer_id,
        "bank": bank,
        "agent": row.agent,
        "state": row.state,
        "tool_name": row.tool_name,
        "tool_args": row.tool_args,
        "result_summary": row.result_summary,
        "handoff_from": row.handoff_from,
        "handoff_to": row.handoff_to,
        "provider": row.provider,
        "model": row.model,
        "fell_back": row.fell_back,
        "fallback_from": row.fallback_from,
        "latency_ms": row.latency_ms,
        "message": row.message,
        "created_at": iso(row.created_at),
        "phase": row.phase,
        "call_id": row.call_id,
        "result": row.result,
        "meta": row.meta,
    }
