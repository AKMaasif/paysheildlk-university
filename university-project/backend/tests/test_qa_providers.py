"""Meta is the main model and Grok (xAI) is the fallback. Gemini is gone."""
from dataclasses import replace

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.runtime import runtime

OPERATOR = {"email": "analyst@payshield.lk", "password": "pilot-analyst", "portal": "operator"}


def operator(client):
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json=OPERATOR).json()["token"]}


def with_keys(meta="", grok=""):
    runtime.settings = replace(runtime.settings, meta_api_key=meta, xai_api_key=grok)


def test_health_names_meta_as_primary_and_grok_as_fallback_without_showing_any_key(boot):
    with TestClient(app) as client:
        with_keys("meta-secret-key", "grok-secret-key")
        body = client.get("/api/health").json()["llm"]
        assert (body["primary"], body["fallback"], body["meta_configured"], body["grok_configured"]) == ("meta", "grok", True, True)
        assert "gemini" not in str(body).lower()
        headers = operator(client)
        for path in ("/api/health", "/api/settings", "/api/agents"):
            text = client.get(path, headers=headers).text
            assert "meta-secret-key" not in text and "grok-secret-key" not in text, path


def test_with_only_a_grok_key_grok_is_primary_and_the_fallback_is_the_mock(boot):
    with TestClient(app) as client:
        with_keys("", "grok-secret-key")
        body = client.get("/api/health").json()["llm"]
        assert (body["primary"], body["fallback"], body["meta_configured"], body["grok_configured"]) == ("grok", "mock", False, True)


def test_with_no_keys_the_engine_is_the_offline_rules_engine(boot):
    with TestClient(app) as client:
        with_keys("", "")
        body = client.get("/api/health").json()["llm"]
        assert (body["primary"], body["fallback"]) == ("mock", "mock")


def test_the_agents_page_names_the_models_in_use(boot):
    with TestClient(app) as client:
        with_keys("m", "x")
        agents = client.get("/api/agents", headers=operator(client)).json()["agents"]
        model_agent = next(a for a in agents if a["configured_model"] != "deterministic")
        assert model_agent["configured_model"].startswith("meta:") and model_agent["fallback_model"].startswith("grok:")


def test_a_leftover_gemini_key_in_the_environment_does_nothing(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "left-over")
    monkeypatch.setenv("DEMO_MODE", "hybrid")
    settings = Settings.from_env()
    assert not hasattr(settings, "gemini_api_key") and settings.meta_api_key == "" and settings.xai_api_key == ""


def test_the_grok_settings_have_the_documented_defaults_and_can_be_changed(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "hybrid")
    for name in ("XAI_API_KEY", "XAI_BASE_URL", "XAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert (settings.xai_base_url, settings.xai_model) == ("https://api.x.ai/v1", "grok-4.20-0309-non-reasoning")
    monkeypatch.setenv("XAI_BASE_URL", "https://proxy.example/v1/")
    monkeypatch.setenv("XAI_MODEL", "grok-test")
    monkeypatch.setenv("XAI_API_KEY", "k")
    settings = Settings.from_env()
    assert (settings.xai_base_url, settings.xai_model, settings.xai_api_key) == ("https://proxy.example/v1", "grok-test", "k")


def test_the_demonstration_mode_that_sends_nothing_reports_an_ignored_grok_key(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "deterministic")
    monkeypatch.setenv("XAI_API_KEY", "k")
    monkeypatch.setenv("META_API_KEY", "m")
    settings = Settings.from_env()
    assert {"AI model (Meta)", "AI model (Grok)"} <= set(settings.ignored_by_mode) and settings.xai_api_key == "" and settings.meta_api_key == ""
