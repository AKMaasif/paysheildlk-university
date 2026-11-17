import threading
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

_session_factory = None
_db_lock = threading.Lock()


class Base(DeclarativeBase):
    pass


def configure_engine(url: str):
    global _session_factory
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {
            "connect_args": {"check_same_thread": False, "timeout": 30},
            "poolclass": NullPool,
        }
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        if engine.dialect.name == "sqlite":
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    _session_factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    return engine


@contextmanager
def session_scope():
    if _session_factory is None:
        raise RuntimeError("database is not ready")
    with _db_lock:
        session = _session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
