from fastapi.testclient import TestClient

from app.main import app


def test_camara_check_and_retrieve_date(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("CHECKPOINT_PATH", str(tmp_path / "checkpoints.sqlite"))
    monkeypatch.setenv("CHECKPOINTER", "sqlite")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("JWT_SECRET", "test-secret-not-for-production-use")
    monkeypatch.setenv("STEP_DELAY_MS", "0")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("META_API_KEY", "")

    with TestClient(app) as client:
        unknown = client.post("/sim-swap/v1/check", json={"phoneNumber": "+94770000000", "maxAge": 24})
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "NOT_FOUND"

        clean = client.post("/sim-swap/v1/check", json={"phoneNumber": "+94772451183"})
        assert clean.status_code == 200
        assert clean.json() == {"swapped": False}

        dated = client.post("/sim-swap/v1/retrieve-date", json={"phoneNumber": "+94772451183"})
        assert dated.status_code == 200
        assert "latestSimChange" in dated.json()

        bad = client.post("/sim-swap/v1/check", json={"phoneNumber": "0772451183", "maxAge": 0})
        assert bad.status_code == 400

        login = client.post(
            "/api/auth/login",
            json={"email": "analyst@payshield.lk", "password": "pilot-analyst", "portal": "operator"},
        )
        assert login.status_code == 200
        token = login.json()["token"]
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["llm"]["primary"] == "mock"
        me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["role"] == "fraud_analyst"
