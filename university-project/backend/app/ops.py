"""What an operator of the service needs to watch: is it ready, and is anything going wrong.

* `GET /api/ready`           public. 200 when the service can do its job, 503 when it cannot (for a load balancer or a probe).
* `GET /api/ops/metrics`     any signed-in operator. Numbers as JSON, or Prometheus text with `?format=prometheus`.

The counters live in memory and start again at zero when the process restarts. The gauges come from the database.
`docs/RUNBOOK.md` says which values to alert on."""
import threading
import time
from datetime import timedelta

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse, PlainTextResponse

STARTED = time.time()
_lock = threading.Lock()
_counters: dict[tuple[str, tuple], int] = {}
_chain_cache: dict = {"at": 0.0, "value": None}
CHAIN_CACHE_SECONDS = 300
SWEEP_STALE_SECONDS = 30  # the reminder and escalation sweep runs every 2 seconds

router = APIRouter()


def count(name: str, **labels: str) -> None:
    key = (name, tuple(sorted(labels.items())))
    with _lock:
        _counters[key] = _counters.get(key, 0) + 1


def counters() -> list[dict]:
    with _lock:
        return [{"name": n, "labels": dict(labels), "value": v} for (n, labels), v in sorted(_counters.items())]


def reset() -> None:
    with _lock:
        _counters.clear()
    _chain_cache.update(at=0.0, value=None)


def heartbeat() -> None:
    """Called by the reminder and escalation loop on every pass."""
    from app.runtime import runtime

    runtime.last_sweep = time.time()


# ------------------------------------------------------------------ readiness

def readiness() -> tuple[bool, dict]:
    from sqlalchemy import text

    from app.runtime import runtime

    checks: dict[str, str] = {}
    try:
        with runtime.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "down"
    checks["checkpointer"] = "ok" if runtime.checkpointer is not None else "down"
    sweep_age = time.time() - (getattr(runtime, "last_sweep", 0) or 0)
    checks["timers"] = "ok" if sweep_age < SWEEP_STALE_SECONDS else "stalled"
    if runtime.settings is not None and runtime.settings.redis_url:
        try:
            if runtime.redis is None:
                raise RuntimeError("not connected")
            runtime.redis.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "degraded"  # events stay in this process; the service still works
    ready = checks["database"] == "ok" and checks["checkpointer"] == "ok" and checks["timers"] == "ok"
    return ready, checks


@router.get("/api/ready")
def ready():
    ok, checks = readiness()
    return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, "checks": checks})


# ------------------------------------------------------------------ metrics

def gauges() -> dict:
    from app.db import session_scope
    from app.ledger import verify_audit_chain
    from app.models import Case, CaseEvent, EmergencyMode, Transaction
    from app.runtime import runtime
    from app.util import aware, utcnow

    now = utcnow()
    with session_scope() as session:
        by_status: dict[str, int] = {}
        for status, in session.query(Case.status).all():
            by_status[status] = by_status.get(status, 0) + 1
        waiting = [aware(w).timestamp() for w, in session.query(Case.waiting_since).filter(Case.status == "waiting_customer", Case.waiting_since.isnot(None)).all()]
        held = [aware(t).timestamp() for t, in session.query(Transaction.occurred_at).filter(Transaction.status == "held").all()]
        errors = session.query(CaseEvent).filter(CaseEvent.state == "error", CaseEvent.created_at >= now - timedelta(hours=1)).count()
        emergencies = session.query(EmergencyMode).count()
    stamp = now.timestamp()
    if time.time() - _chain_cache["at"] > CHAIN_CACHE_SECONDS or _chain_cache["value"] is None:
        _chain_cache.update(at=time.time(), value=verify_audit_chain())
    chain = _chain_cache["value"]
    return {
        "cases_by_status": by_status,
        "escalated_cases": by_status.get("escalated", 0),
        "held_payments": len(held),
        "oldest_held_payment_seconds": int(stamp - min(held)) if held else 0,
        "oldest_waiting_customer_seconds": int(stamp - min(waiting)) if waiting else 0,
        "agent_errors_last_hour": errors,
        "emergency_districts": emergencies,
        "audit_chain_ok": bool(chain["ok"]),
        "audit_entries": chain.get("checked", 0),
        "uptime_seconds": int(time.time() - STARTED),
        "timer_sweep_age_seconds": int(time.time() - (getattr(runtime, "last_sweep", 0) or 0)),
    }


def prometheus(g: dict, series: list[dict]) -> str:
    lines: list[str] = []

    def gauge(name: str, value, help_text: str, labels: str = "") -> None:
        lines.append(f"# HELP payshield_{name} {help_text}")
        lines.append(f"# TYPE payshield_{name} gauge")
        lines.append(f"payshield_{name}{labels} {value}")

    for status, n in sorted(g["cases_by_status"].items()):
        lines.append(f'payshield_cases{{status="{status}"}} {n}')
    gauge("escalated_cases", g["escalated_cases"], "Cases handed to an analyst and not closed")
    gauge("held_payments", g["held_payments"], "Payments currently held")
    gauge("oldest_held_payment_seconds", g["oldest_held_payment_seconds"], "Age of the oldest held payment")
    gauge("oldest_waiting_customer_seconds", g["oldest_waiting_customer_seconds"], "Longest a customer has been asked and not answered")
    gauge("agent_errors_last_hour", g["agent_errors_last_hour"], "Agent steps that failed in the last hour")
    gauge("emergency_districts", g["emergency_districts"], "Districts in emergency mode")
    gauge("audit_chain_ok", int(g["audit_chain_ok"]), "1 when the audit log hash chain verifies")
    gauge("uptime_seconds", g["uptime_seconds"], "Seconds since the process started")
    gauge("timer_sweep_age_seconds", g["timer_sweep_age_seconds"], "Seconds since the reminder and escalation sweep last ran")
    for item in series:
        labels = ",".join(f'{k}="{v}"' for k, v in sorted(item["labels"].items()))
        lines.append(f"payshield_{item['name']}_total{{{labels}}} {item['value']}" if labels else f"payshield_{item['name']}_total {item['value']}")
    return "\n".join(lines) + "\n"


@router.get("/api/ops/metrics")
def metrics(format: str = Query(default="json"), authorization: str | None = Header(default=None)):
    from app.desk import _reader

    _reader(authorization)
    g, series = gauges(), counters()
    if format == "prometheus":
        return PlainTextResponse(prometheus(g, series), media_type="text/plain; version=0.0.4")
    return {"gauges": g, "counters": series}
