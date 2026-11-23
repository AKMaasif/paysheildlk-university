"""All HTTP and WebSocket routes, assembled from the modules in app/routes.

Each module owns one area (auth, console, customer, admin, demo, telco, stream, health); shared request models and
helpers live in app/routes/common.py. Names other parts of the code import from here are re-exported below."""
from fastapi import APIRouter

from app.agents.runner import (
    process_timeouts,  # noqa: F401  (tests drive the sweeper without the HTTP layer)
)
from app.routes import admin, auth, console, customer, demo, health, stream, telco
from app.routes.common import (  # noqa: F401
    VerifyIn,
    _client_ip,
    _customer_case_state,
    _pending_step,
    _public_user,
    _timers,
    current_user,
    require_customer,
    require_drills,
    require_operator,
    require_writer,
)

router = APIRouter()
for module in (health, auth, telco, console, customer, admin, demo, stream):
    router.include_router(module.router)

__all__ = ["process_timeouts", "router"]
