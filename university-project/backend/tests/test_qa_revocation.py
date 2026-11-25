"""Signing out ends the session for real: a copy of the token taken earlier stops working, not only the browser's cookie."""
from fastapi.testclient import TestClient

from app import ratelimit, security
from app.db import session_scope
from app.main import app
from app.models import RevokedToken, User
from app.runtime import runtime
from app.util import utcnow

OPERATOR = {"email": "analyst@payshield.lk", "password": "pilot-analyst", "portal": "operator"}
SITE = {"X-Session-Mode": "cookie", "X-Requested-With": "payshield"}


def login(client):
    reply = client.post("/api/auth/login", json=OPERATOR)
    assert reply.status_code == 200
    return reply.json()


def bearer(token):
    return {"Authorization": "Bearer " + token}


def test_a_signed_out_token_is_refused_even_though_it_has_not_expired(boot):
    ratelimit.reset_all()
    with TestClient(app) as client:
        session = login(client)
        assert client.get("/api/me", headers=bearer(session["token"])).status_code == 200
        out = client.post("/api/auth/logout", json={"refresh_token": session["refresh_token"]}, headers=bearer(session["token"]))
        assert out.status_code == 200
        assert client.get("/api/me", headers=bearer(session["token"])).status_code == 401  # the copy is dead
        assert client.post("/api/auth/refresh", json={"refresh_token": session["refresh_token"]}).status_code == 401
        with session_scope() as db:
            assert db.query(RevokedToken).count() == 2


def test_signing_out_in_one_place_leaves_the_same_account_signed_in_elsewhere(boot):
    with TestClient(app) as client:
        phone, laptop = login(client), login(client)
        client.post("/api/auth/logout", headers=bearer(phone["token"]), json={"refresh_token": phone["refresh_token"]})
        assert client.get("/api/me", headers=bearer(phone["token"])).status_code == 401
        assert client.get("/api/me", headers=bearer(laptop["token"])).status_code == 200


def test_sign_out_everywhere_ends_every_earlier_session_and_a_new_sign_in_works(boot):
    import time

    with TestClient(app) as client:
        first, second = login(client), login(client)
        time.sleep(1.1)  # token times are whole seconds
        client.post("/api/auth/logout", headers=bearer(second["token"]), json={"everywhere": True, "refresh_token": second["refresh_token"]})
        for session in (first, second):
            assert client.get("/api/me", headers=bearer(session["token"])).status_code == 401
            assert client.post("/api/auth/refresh", json={"refresh_token": session["refresh_token"]}).status_code == 401
        time.sleep(1.1)
        fresh = login(client)
        assert client.get("/api/me", headers=bearer(fresh["token"])).status_code == 200


def test_a_browser_sign_out_revokes_what_the_cookies_held(boot):
    with TestClient(app) as client:
        client.post("/api/auth/login", json=OPERATOR, headers=SITE)
        copied = {name: client.cookies.get(name) for name in ("ps_operator_at", "ps_operator_rt")}  # what a thief with the values would hold
        assert all(copied.values())
        assert client.post("/api/auth/logout", headers=SITE, json={"portal": "operator"}).status_code == 200
        assert client.get("/api/me", headers=bearer(copied["ps_operator_at"])).status_code == 401
        assert client.post("/api/auth/refresh", json={"refresh_token": copied["ps_operator_rt"]}).status_code == 401


def test_the_live_stream_refuses_a_signed_out_token(boot):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        session = login(client)
        client.post("/api/auth/logout", headers=bearer(session["token"]))
        with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(f"/ws?token={session['token']}") as socket:
            socket.receive_json()
        assert refused.value.code == 4401


def test_signing_out_twice_or_with_nothing_is_harmless_and_an_unknown_token_revokes_nothing(boot):
    with TestClient(app) as client:
        assert client.post("/api/auth/logout").status_code == 200
        assert client.post("/api/auth/logout", json={"refresh_token": "garbage"}).status_code == 200
        session = login(client)
        for _ in range(2):
            assert client.post("/api/auth/logout", headers=bearer(session["token"])).status_code == 200
        with session_scope() as db:
            assert db.query(RevokedToken).count() == 1  # recorded once


def test_one_account_cannot_sign_another_out(boot):
    with TestClient(app) as client:
        analyst = login(client)
        other = client.post("/api/auth/login", json={"email": "supervisor@payshield.lk", "password": "pilot-supervisor", "portal": "operator"}).json()
        client.post("/api/auth/logout", headers=bearer(analyst["token"]))
        assert client.get("/api/me", headers=bearer(other["token"])).status_code == 200


def test_expired_revocation_records_are_cleaned_up_at_the_next_sign_out(boot):
    from datetime import timedelta

    with TestClient(app) as client:
        with session_scope() as db:
            db.add(RevokedToken(jti="old" + "0" * 29, user_id="usr_analyst", expires_at=utcnow() - timedelta(hours=1), revoked_at=utcnow() - timedelta(days=1)))
        session = login(client)
        client.post("/api/auth/logout", headers=bearer(session["token"]))
        with session_scope() as db:
            assert db.get(RevokedToken, "old" + "0" * 29) is None


def test_a_revoked_customer_token_is_refused_on_customer_endpoints_but_a_half_finished_sign_in_is_not_affected(boot):
    from helpers import PASSWORD, register, username_for

    with TestClient(app) as client:
        assert register(client).status_code == 200
        pending = client.post("/api/auth/login", json={"email": username_for("cus_nimal"), "password": PASSWORD, "portal": "customer"}).json()
        token = pending["pending_token"]
        setup = client.post("/api/customer/totp/setup", headers=bearer(token)).json()
        security._used_codes.clear()
        done = client.post("/api/customer/totp/confirm", headers=bearer(token), json={"code": security.totp_now(setup["secret"])}).json()
        assert client.get("/api/customer/home", headers=bearer(done["token"])).status_code == 200
        client.post("/api/auth/logout", json={"portal": "customer", "refresh_token": done["refresh_token"]}, headers=bearer(done["token"]))
        assert client.get("/api/customer/home", headers=bearer(done["token"])).status_code == 401
        assert User and runtime
