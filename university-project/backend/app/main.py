import asyncio
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError

from app import ops
from app.api import router
from app.config import Settings
from app.customer import router as customer_router
from app.desk import router as desk_router
from app.limits import BodyLimit
from app.logs import configure_logging
from app.ops import router as ops_router
from app.runtime import init_runtime, runtime, start_background, stop_background
from app.simulate import router as simulate_router

configure_logging()
log = logging.getLogger("payshield.http")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_runtime()
    runtime.bus.bind(asyncio.get_running_loop())
    start_background()
    yield
    stop_background()


def install_cors(application: FastAPI, origins) -> None:
    """Only the listed sites may call the API from a browser. Sign-in uses a bearer token, never a cookie,
    so credentials are not allowed and a page on another site cannot ride on anyone's session."""
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )


app = FastAPI(title="PayShield", version="0.1.0", lifespan=lifespan)
install_cors(app, Settings.from_env().allowed_origins)
app.add_middleware(BodyLimit)


@app.middleware("http")
async def request_id(request: Request, call_next):
    """Give every request an id (the caller's, if it sent a sane one), return it in a header, and log failures with it."""
    incoming = request.headers.get("x-request-id", "")
    rid = incoming if re.fullmatch(r"[A-Za-z0-9._-]{8,64}", incoming) else uuid.uuid4().hex
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        ops.count("http_5xx")
        log.exception("request failed", extra={"request_id": rid, "method": request.method, "path": request.url.path, "status": 500})
        raise
    response.headers["X-Request-ID"] = rid
    elapsed = int((time.monotonic() - started) * 1000)
    if response.status_code >= 500:
        ops.count("http_5xx")
        log.error("server error", extra={"request_id": rid, "method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": elapsed})
    elif elapsed > 2000:
        log.warning("slow request", extra={"request_id": rid, "method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": elapsed})
    return response


@app.exception_handler(DataError)
async def bad_value(_request: Request, exc: DataError):
    """Postgres refuses a value that is too long for its column, or that holds a NUL byte, where SQLite accepted it.
    That is bad input, not a server fault. The transaction has already been rolled back."""
    logging.getLogger("payshield").info("a value the database refused: %s", type(exc.orig).__name__)
    return JSONResponse(status_code=422, content={"detail": "One of the values is not valid. Check the length and the characters."})


app.include_router(router)
app.include_router(ops_router)
app.include_router(customer_router)
app.include_router(desk_router)
app.include_router(simulate_router)
