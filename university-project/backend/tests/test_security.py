import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.agents import graph
from app.config import Settings
from app.db import session_scope
from app.main import app
from app.models import User
from app.privacy import PrivacyViolation, guard, mask
from app.protocol import HandoffRejected, open_sealed, seal
from app.runtime import runtime
from app.security import (
    hash_password,
    open_totp,
    password_needs_upgrade,
    verify_password,
)


def _legacy_pbkdf2(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 20_000)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


# Passwords

def test_passwords_use_argon2id():
    stored = hash_password("pilot-secret-1")
    assert stored.startswith("$argon2id$")
    assert verify_password("pilot-secret-1", stored)
    assert not verify_password("pilot-secret-2", stored)
    assert not password_needs_upgrade(stored)


def test_legacy_hash_is_upgraded_on_sign_in(boot):
    with session_scope() as session:
        user = session.query(User).filter(User.email == "analyst@payshield.lk").one()
        user.password_hash = _legacy_pbkdf2("pilot-analyst")
    with TestClient(app) as client:
        reply = client.post("/api/auth/login", json={"email": "analyst@payshield.lk", "password": "pilot-analyst"})
    assert reply.status_code == 200
    with session_scope() as session:
        stored = session.query(User).filter(User.email == "analyst@payshield.lk").one().password_hash
    assert stored.startswith("$argon2id$")


# Rate limits and tokens

def test_repeated_wrong_passwords_are_throttled(boot):
    with TestClient(app) as client:
        for _ in range(5):
            bad = client.post("/api/auth/login", json={"email": "analyst@payshield.lk", "password": "wrong-one"})
            assert bad.status_code == 401
        locked = client.post("/api/auth/login", json={"email": "analyst@payshield.lk", "password": "pilot-analyst"})
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers


def test_registration_guesses_are_throttled(boot):
    from helpers import register

    with TestClient(app) as client:
        for _ in range(6):
            bad = register(client, nic="000000000000")
            assert bad.status_code == 422
        locked = register(client, nic="000000000000")
        # Even the right details are refused while the account number is locked out.
        right = register(client)
    assert locked.status_code == 429
    assert right.status_code == 429


def test_customer_sign_in_gets_short_access_and_refresh(boot):
    from helpers import PASSWORD, record, register

    with TestClient(app) as client:
        assert register(client).status_code == 200
        done = client.post(
            "/api/auth/login",
            json={"email": record("cus_nimal")["name"], "password": PASSWORD, "portal": "customer"},
        ).json()
        # A refresh token is not an access token.
        refused = client.get("/api/customer/home", headers={"Authorization": "Bearer " + done["refresh_token"]})
        assert refused.status_code in {401, 403}
        renewed = client.post("/api/auth/refresh", json={"refresh_token": done["refresh_token"]})
        assert renewed.status_code == 200
        home = client.get("/api/customer/home", headers={"Authorization": "Bearer " + renewed.json()["token"]})
        assert home.status_code == 200
        # And an access token cannot be used to refresh.
        wrong = client.post("/api/auth/refresh", json={"refresh_token": done["token"]})
        assert wrong.status_code == 401


def test_authenticator_secrets_are_stored_encrypted(boot):
    with session_scope() as session:
        stored = session.get(User, "cus_nimal").totp_secret
    assert stored.startswith("gcm$")
    assert open_totp(stored) == "57DNTC7YHTX72K5OESQCRL6OXOJBXNKX"


# Privacy

def test_identifiers_are_masked_before_a_model_sees_them():
    text = mask("call +94772451183 or 0772451183, mail a.b@mail.lk, account 8200451190, NIC 199012345678 on 2026-11-17")
    assert "772451183" not in text and "a.b@mail.lk" not in text and "8200451190" not in text
    assert "199012345678" not in text
    assert "[phone ..83]" in text and "[account ..90]" in text
    assert "2026-11-17" in text


def test_secrets_never_reach_a_model(boot):
    with pytest.raises(PrivacyViolation):
        guard("security answer: Royal College")
    with pytest.raises(PrivacyViolation):
        runtime.llm.complete(system="note", user="password: pilot-nimal", purpose="risk")


# Signed handoffs

def test_handoffs_are_signed_and_checked():
    key = "k" * 40
    sealed = seal(key, "policy", "release", "PS-1", {"decision": "release"})
    assert open_sealed(key, sealed, "release", "PS-1") == {"decision": "release"}
    # The same message cannot be used twice.
    with pytest.raises(HandoffRejected, match="replayed"):
        open_sealed(key, sealed, "release", "PS-1")


def test_tampered_misaddressed_or_off_route_handoffs_are_refused():
    key = "k" * 40
    sealed = seal(key, "policy", "alert", "PS-2", {"decision": "hold"})
    forged = {**sealed, "payload": {"decision": "release"}}
    with pytest.raises(HandoffRejected, match="bad signature"):
        open_sealed(key, forged, "alert", "PS-2")
    with pytest.raises(HandoffRejected, match="addressed"):
        open_sealed(key, seal(key, "policy", "alert", "PS-2", {}), "release", "PS-2")
    with pytest.raises(HandoffRejected, match="another case"):
        open_sealed(key, seal(key, "policy", "alert", "PS-2", {}), "alert", "PS-3")
    with pytest.raises(HandoffRejected):
        seal(key, "telecom", "release", "PS-2", {})
    with pytest.raises(HandoffRejected, match="bad signature"):
        open_sealed("x" * 40, seal(key, "policy", "alert", "PS-2", {}), "alert", "PS-2")


def test_release_refuses_without_a_signed_instruction(boot):
    from app.demo import trigger

    case_id = trigger("cus_nimal", "sim_swap_transfer")
    state = {"case_id": case_id, "customer_id": "cus_nimal", "transaction_id": "tx", "customer_decision": "yes"}
    with pytest.raises(HandoffRejected):
        graph.release({**state, "_inbox": {}})
    # A correctly signed handoff that says "block" does not authorise a release.
    blocked = graph._send(case_id, "policy", "release", {"decision": "block"})
    with pytest.raises(PermissionError, match="does not authorise"):
        graph.release({**state, "customer_decision": None, **blocked})


# Startup

def test_production_refuses_development_secrets(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    for name in ("JWT_SECRET", "ENCRYPTION_KEY", "HMAC_KEY", "ANSWER_PEPPER"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings.from_env().assert_safe_for_environment()
    for name in ("JWT_SECRET", "ENCRYPTION_KEY", "HMAC_KEY", "ANSWER_PEPPER"):
        monkeypatch.setenv(name, os.urandom(24).hex())
    monkeypatch.setenv("DEMO_ASSIST", "true")
    with pytest.raises(RuntimeError, match="DEMO_ASSIST"):
        Settings.from_env().assert_safe_for_environment()
    monkeypatch.setenv("DEMO_ASSIST", "false")
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        Settings.from_env().assert_safe_for_environment()
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://bank.example")
    Settings.from_env().assert_safe_for_environment()
    ratelimit.reset_all()
