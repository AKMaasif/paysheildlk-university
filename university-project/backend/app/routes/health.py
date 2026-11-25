"""health routes (split from app/api.py)."""
import logging

from fastapi import (
    APIRouter,
)

from app.runtime import runtime

log = logging.getLogger("payshield.api")
router = APIRouter()


def _db_label() -> str:
    url = runtime.settings.database_url if runtime.settings else ""
    return "postgres" if url.startswith("postgres") else "sqlite"


@router.get("/api/health")
def health():
    settings = runtime.settings
    primary = "mock"
    if settings and settings.meta_api_key:
        primary = "meta"
    elif settings and settings.xai_api_key:
        primary = "grok"
    return {
        "ok": True,
        "service": "payshield",
        "database": _db_label(),
        "checkpointer": runtime.checkpointer_kind,
        "redis": "connected" if runtime.redis is not None else "memory",
        "llm": {
            "primary": primary,
            "meta_configured": bool(settings and settings.meta_api_key),
            "grok_configured": bool(settings and settings.xai_api_key),
            "meta_model": settings.meta_model if settings else "",
            "grok_model": settings.xai_model if settings else "",
            "fallback": "grok" if settings and settings.meta_api_key and settings.xai_api_key else "mock",
        },
        "sms": settings.sms_driver if settings else "log",
        "telco": "operator gateway" if settings and settings.telco_base_url else "simulated CAMARA",
        "email": settings.email_driver if settings else "log",
        "push": settings.push_driver if settings else "websocket",
        "timezone": "Asia/Colombo",
        "demo_data": True,
        "realert_seconds": settings.realert_seconds if settings else 180,
        "escalate_seconds": settings.escalate_seconds if settings else 600,
        "jwt_dev_secret": bool(settings and settings.jwt_is_dev_default),
        # What the presenter needs to say truthfully about this run. Counts only: no address, number or key is shown.
        "mode": settings.demo_mode if settings else "deterministic",
        "mode_note": settings.mode_note if settings else "",
        "ignored_by_mode": list(settings.ignored_by_mode) if settings else [],
        "recipients_cleared": {"phones": len(settings.demo_allowed_phones), "emails": len(settings.demo_allowed_emails)} if settings else {},
        "real_actions": {
            "bank_ledger": "synthetic",
            "sim_freeze": "simulated request, no operator integration",
            "sim_swap_data": "operator gateway" if settings and settings.telco_base_url else "synthetic",
            "sms": "real provider" if settings and settings.sms_driver == "textlk" else "logged only",
            "email": "real provider" if settings and settings.email_driver == "smtp" else "logged only",
        },
    }


@router.get("/")
def root():
    return {"service": "PayShield", "docs": "/docs"}
