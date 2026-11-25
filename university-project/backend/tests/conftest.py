import pytest

from app.runtime import init_runtime, runtime


@pytest.fixture
def boot(tmp_path, monkeypatch):
    from helpers import external_services

    database, redis_url = external_services()
    monkeypatch.setenv("DATABASE_URL", database or f"sqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("CHECKPOINT_PATH", str(tmp_path / "checkpoints.sqlite"))
    monkeypatch.setenv("CHECKPOINTER", "postgres" if database else "sqlite")
    monkeypatch.setenv("REDIS_URL", redis_url or "")
    monkeypatch.setenv("JWT_SECRET", "test-secret-not-for-production-use")
    monkeypatch.setenv("STEP_DELAY_MS", "0")
    monkeypatch.setenv("FACE_ENGINE", "legacy")
    monkeypatch.setenv("DEMO_ASSIST", "true")
    monkeypatch.setenv("XAI_API_KEY", "")
    monkeypatch.setenv("META_API_KEY", "")
    monkeypatch.setenv("REALERT_SECONDS", "180")
    monkeypatch.setenv("ESCALATE_SECONDS", "600")
    monkeypatch.setenv("TEXTLK_API_KEY", "")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
    from app import ratelimit

    ratelimit.reset_all()
    init_runtime()
    return runtime
