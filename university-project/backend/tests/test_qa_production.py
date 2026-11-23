"""What a production start looks like: one administrator and nothing fictional, drills off, and refusals that say why."""
import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.main import app
from app.models import AccessRequest, Case, Invitation, Transaction, User
from app.runtime import init_runtime, runtime

GOOD_PASSWORD = "Granite-Harbour-2468"


@pytest.fixture
def production(tmp_path, monkeypatch):
    env = {
        "APP_ENV": "production", "DATABASE_URL": f"sqlite:///{tmp_path}/prod.db", "CHECKPOINT_PATH": str(tmp_path / "cp.sqlite"), "CHECKPOINTER": "sqlite",
        "REDIS_URL": "", "JWT_SECRET": "j" * 40, "ENCRYPTION_KEY": "e" * 40, "HMAC_KEY": "h" * 40, "ANSWER_PEPPER": "a" * 40, "DEMO_ASSIST": "false",
        "ALLOWED_ORIGINS": "https://bank.example", "FACE_ENGINE": "legacy", "STEP_DELAY_MS": "0", "GEMINI_API_KEY": "", "META_API_KEY": "",
        "BOOTSTRAP_ADMIN_EMAIL": "admin@bank.example", "BOOTSTRAP_ADMIN_PASSWORD": GOOD_PASSWORD,
        "TEXTLK_API_KEY": "", "SMTP_HOST": "", "FIREBASE_PROJECT_ID": "",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for name in ("DRILLS_ENABLED", "SIMULATED_BOOK"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def users():
    with session_scope() as session:
        return [(u.email, u.role) for u in session.query(User).all()]


# ------------------------------------------------------------------ the first start

def test_a_production_database_starts_with_one_administrator_and_nothing_fictional(production):
    init_runtime()
    assert users() == [("admin@bank.example", "super_admin")]
    with session_scope() as session:
        for model in (Invitation, AccessRequest, Case, Transaction):
            assert session.query(model).count() == 0, model.__name__


def test_the_administrator_can_sign_in_and_the_published_demo_accounts_cannot(production):
    init_runtime()
    with TestClient(app) as client:
        good = client.post("/api/auth/login", json={"email": "admin@bank.example", "password": GOOD_PASSWORD, "portal": "operator"})
        assert good.status_code == 200 and good.json()["user"]["role"] == "super_admin"
        for email, password in [("admin@payshield.lk", "pilot-admin"), ("analyst@payshield.lk", "pilot-analyst"), ("auditor@payshield.lk", "pilot-auditor")]:
            assert client.post("/api/auth/login", json={"email": email, "password": password, "portal": "operator"}).status_code == 401


def test_a_second_start_adds_nothing(production):
    init_runtime()
    init_runtime()
    assert users() == [("admin@bank.example", "super_admin")]


@pytest.mark.parametrize("email,password,why", [
    ("", GOOD_PASSWORD, "BOOTSTRAP_ADMIN_EMAIL"), ("admin@bank.example", "", "BOOTSTRAP_ADMIN_PASSWORD"),
    ("not-an-email", GOOD_PASSWORD, "not an email"), ("admin@bank.example", "Short-1", "at least 14"),
    ("admin@bank.example", "onlylettersonlyletters", "letter and a number"), ("admin@bank.example", "1234567890123456", "letter and a number"),
    ("admin@bank.example", "pilot-admin", "at least 14"),
])
def test_a_first_start_without_a_proper_administrator_is_refused(production, email, password, why):
    production.setenv("BOOTSTRAP_ADMIN_EMAIL", email)
    production.setenv("BOOTSTRAP_ADMIN_PASSWORD", password)
    with pytest.raises(RuntimeError, match=why):
        init_runtime()


def test_the_published_demonstration_passwords_are_refused_even_when_long_enough(production, monkeypatch):
    from app import seed

    monkeypatch.setattr(seed, "OPERATORS", seed.OPERATORS + [("x", "X", "x@y.lk", GOOD_PASSWORD, "auditor", None)])
    with pytest.raises(RuntimeError, match="published"):
        init_runtime()


def test_a_database_that_already_has_users_needs_no_bootstrap_variables(production):
    init_runtime()
    production.delenv("BOOTSTRAP_ADMIN_EMAIL")
    production.delenv("BOOTSTRAP_ADMIN_PASSWORD")
    init_runtime()
    assert users() == [("admin@bank.example", "super_admin")]


def test_development_still_seeds_the_demonstration_workspace(boot):
    emails = {e for e, _ in users()}
    assert {"analyst@payshield.lk", "admin@payshield.lk"} <= emails and len(emails) > 5


# ------------------------------------------------------------------ the demonstration helpers

ADMIN = {"email": "admin@bank.example", "password": GOOD_PASSWORD, "portal": "operator"}
DRILL_CALLS = [
    ("POST", "/api/demo/trigger", {"customer_id": "x", "scenario": "sim_swap_transfer"}), ("POST", "/api/demo/unregister", {"customer_id": "x"}), ("POST", "/api/demo/reset", {}),
    ("GET", "/api/demo/assist?customer_id=x", None), ("GET", "/api/demo/customers", None),
    ("POST", "/api/demo/nudge", {"case_id": "x", "action": "realert"}),
    ("POST", "/api/simulate/run", {"scenario": "sim_swap_transfer"}), ("POST", "/api/simulate/clear", {}),
]


def test_drills_and_demonstration_helpers_are_switched_off_in_production(production):
    init_runtime()
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + client.post("/api/auth/login", json=ADMIN).json()["token"]}
        for method, path, body in DRILL_CALLS:
            reply = client.request(method, path, json=body, headers=headers)
            assert reply.status_code == 404 and "switched off" in reply.json()["detail"], (path, reply.status_code)


def test_the_simulate_page_still_loads_in_production_with_no_scenarios_so_the_real_emergency_switch_keeps_working(production):
    init_runtime()
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + client.post("/api/auth/login", json=ADMIN).json()["token"]}
        listing = client.get("/api/simulate", headers=headers).json()
        assert listing["drills_enabled"] is False and listing["scenarios"] == [] and listing["customers"] == [] and "emergency" in listing
        on = client.post("/api/emergency", json={"district": "Colombo", "on": True, "reason": "fraud ring reported"}, headers=headers)
        assert on.status_code == 200
        assert "Colombo" in client.get("/api/simulate", headers=headers).json()["emergency"]["active"]


def test_drills_can_be_turned_on_on_purpose_in_production(production):
    production.setenv("DRILLS_ENABLED", "true")
    init_runtime()
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + client.post("/api/auth/login", json=ADMIN).json()["token"]}
        listing = client.get("/api/simulate", headers=headers).json()
        assert listing["drills_enabled"] is True and listing["scenarios"]


def test_the_real_emergency_switch_and_the_manual_controls_stay_available_in_production(production):
    init_runtime()
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + client.post("/api/auth/login", json=ADMIN).json()["token"]}
        assert client.get("/api/emergency", headers=headers).status_code == 200
        assert client.get("/api/cases", headers=headers).status_code == 200


def test_no_simulated_payments_are_written_in_production(production):
    init_runtime()
    with session_scope() as session:
        assert session.query(Transaction).filter(Transaction.origin == "simulated").count() == 0
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + client.post("/api/auth/login", json=ADMIN).json()["token"]}
        totals = client.get("/api/analytics", headers=headers).json()["totals"]
        assert totals["payments"] == 0


def test_production_without_a_real_sim_swap_feed_says_so_loudly_at_start(production, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="payshield"):
        init_runtime()
    assert any("TELCO_BASE_URL is empty" in r.getMessage() for r in caplog.records)
    caplog.clear()
    production.setenv("TELCO_BASE_URL", "https://operator.example/camara")
    with caplog.at_level(logging.WARNING, logger="payshield"):
        init_runtime()
    assert not any("TELCO_BASE_URL is empty" in r.getMessage() for r in caplog.records)


def test_the_simulated_book_and_drills_are_on_by_default_in_development(boot):
    assert runtime.settings.drills_enabled and runtime.settings.simulated_book
    with session_scope() as session:
        assert session.query(Transaction).filter(Transaction.origin == "simulated").count() > 100
