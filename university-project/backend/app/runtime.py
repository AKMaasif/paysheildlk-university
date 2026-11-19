import json
import logging
import threading
from pathlib import Path

from app.agents.runner import timeout_loop
from app.config import Settings
from app.db import Base, configure_engine, session_scope
from app.events import EventBus
from app.llm import LLMRouter
from app.seed import seed

log = logging.getLogger("payshield")


class Runtime:
    def __init__(self):
        self.settings: Settings | None = None
        self.engine = None
        self.bus = EventBus()
        self.llm: LLMRouter | None = None
        self.redis = None
        self.graph = None
        self.checkpointer = None
        self.checkpointer_cm = None
        self.checkpointer_kind = "sqlite"
        self.stop: threading.Event | None = None
        self.generation = 0


runtime = Runtime()


def psycopg_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgres+psycopg://", "postgresql://")
    )


def _connect_redis(settings: Settings):
    if not settings.redis_url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        return client
    except Exception:
        log.warning("Redis is not reachable. Events stay in memory and Postgres.")
        return None


def _open_checkpointer(settings: Settings):
    want_postgres = settings.checkpointer == "postgres" or (
        settings.database_url.startswith("postgres") and settings.checkpointer != "sqlite"
    )
    if want_postgres:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            manager = PostgresSaver.from_conn_string(psycopg_url(settings.database_url))
            saver = manager.__enter__()
            saver.setup()
            return manager, saver, "postgres"
        except Exception:
            log.exception("Postgres checkpointer failed. Falling back to sqlite.")
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = Path(settings.checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    manager = SqliteSaver.from_conn_string(str(path))
    saver = manager.__enter__()
    saver.setup()
    return manager, saver, "sqlite"


def stop_background() -> None:
    if runtime.stop is not None:
        runtime.stop.set()
    runtime.stop = None


def start_background() -> None:
    stop_background()
    runtime.generation += 1
    runtime.stop = threading.Event()
    threads = [
        threading.Thread(
            target=timeout_loop,
            args=(runtime.stop, runtime.generation),
            name="payshield-timeouts",
            daemon=True,
        )
    ]
    if runtime.redis is not None:
        threads.append(threading.Thread(target=redis_loop, args=(runtime.stop,), name="payshield-redis", daemon=True))
    for thread in threads:
        thread.start()


def redis_loop(stop: threading.Event) -> None:
    client = runtime.redis
    if client is None:
        return
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe("payshield:live")
    while not stop.is_set():
        message = pubsub.get_message(timeout=1.0)
        if not message or message.get("type") != "message":
            continue
        raw = message.get("data")
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        runtime.bus.observe_remote(payload)


def _ensure_columns(engine) -> None:
    """Add columns introduced after the first pilot database."""
    alters = {
        "users": {
            "username": "VARCHAR(80)",
            "totp_failures": "INTEGER DEFAULT 0",
            "totp_locked_until": "TIMESTAMP",
            "nic": "VARCHAR(20)",
            "balance_lkr": "INTEGER DEFAULT 0",
            "registered": "BOOLEAN DEFAULT 0",
            "registered_at": "TIMESTAMP",
            "must_change_password": "BOOLEAN DEFAULT 0",
            "password_changed_at": "TIMESTAMP",
            "face_ref": "TEXT",
            "face_hash": "VARCHAR(64)",
        },
        "cases": {
            "liveness_strikes": "INTEGER DEFAULT 0",
            "knowledge_round": "INTEGER DEFAULT 0",
            "face_score": "FLOAT",
            "face_verdict": "VARCHAR(20)",
        },
        "case_events": {"fallback_from": "VARCHAR(40)"},
        "reports": {"sha256": "VARCHAR(64)"},
        "audit_log": {"prev_hash": "VARCHAR(64)", "entry_hash": "VARCHAR(64)"},
    }
    with engine.begin() as conn:
        for table, columns in alters.items():
            found = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()} if engine.dialect.name == "sqlite" else set()
            if engine.dialect.name != "sqlite":
                continue
            if not found:
                continue
            for name, ddl in columns.items():
                if name not in found:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_runtime() -> None:
    from app.agents.graph import build_graph

    stop_background()
    runtime.generation += 1
    if runtime.engine is not None:
        runtime.engine.dispose()
    if runtime.checkpointer_cm is not None:
        try:
            runtime.checkpointer_cm.__exit__(None, None, None)
        except Exception:
            log.exception("could not close the previous checkpointer")
        runtime.checkpointer_cm = None

    settings = Settings.from_env()
    settings.assert_safe_for_environment()
    if settings.database_url.startswith("sqlite"):
        raw = settings.database_url.split("///", 1)[-1]
        if raw and raw != ":memory:":
            Path(raw).parent.mkdir(parents=True, exist_ok=True)
    engine = configure_engine(settings.database_url)
    Base.metadata.create_all(engine)
    _ensure_columns(engine)
    runtime.settings = settings
    runtime.engine = engine
    runtime.bus = EventBus()
    runtime.llm = LLMRouter(
        gemini_api_key=settings.gemini_api_key,
        gemini_model=settings.gemini_model,
        meta_api_key=settings.meta_api_key,
        meta_base_url=settings.meta_base_url,
        meta_model=settings.meta_model,
    )
    runtime.redis = _connect_redis(settings)
    manager, saver, kind = _open_checkpointer(settings)
    runtime.checkpointer_cm = manager
    runtime.checkpointer = saver
    runtime.checkpointer_kind = kind
    runtime.graph = build_graph(saver)
    from app.seed import upgrade_existing
    from app.simbook import refresh as refresh_simulated_book
    from app.util import utcnow

    with session_scope() as session:
        created = seed(session, settings)
        upgrade_existing(session)
        from app.calibration import load_thresholds

        load_thresholds(session)
        # Databases made by an older build get the current simulated book in place.
        rebuilt = refresh_simulated_book(session, utcnow())
    if created:
        log.info("Seeded the pilot workspace")
    elif rebuilt:
        log.info("Rebuilt the simulated payment book")
    log.info(
        "Ready database=%s checkpointer=%s redis=%s llm=%s",
        "postgres" if settings.database_url.startswith("postgres") else "sqlite",
        kind,
        "yes" if runtime.redis is not None else "memory",
        "gemini" if settings.gemini_api_key else ("meta" if settings.meta_api_key else "mock"),
    )
