"""Browser sessions in HttpOnly cookies: how they are set, how they are read, and what they must not allow."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from helpers import PASSWORD, register, username_for
from starlette.websockets import WebSocketDisconnect

from app import ratelimit, security
from app.main import app
from app.runtime import runtime

COOKIE = {"X-Session-Mode": "cookie"}
SITE = {"X-Session-Mode": "cookie", "X-Requested-With": "payshield"}
OPERATOR = {"email": "analyst@payshield.lk", "password": "pilot-analyst", "portal": "operator"}


@pytest.fixture(autouse=True)
def _fresh():
    ratelimit.reset_all()
    security._used_codes.clear()
    yield
    security._used_codes.clear()


def set_cookies(reply):
    return [value for key, value in reply.headers.multi_items() if key.lower() == "set-cookie"]


def operator_in(client):
    reply = client.post("/api/auth/login", json=OPERATOR, headers=COOKIE)
    assert reply.status_code == 200, reply.text
    return reply


def customer_in(client, customer="cus_nimal", mode=COOKIE):
    """Register, then sign in the way the browser does: password, then the authenticator code."""
    from app.db import session_scope
    from app.models import User

    assert register(client, customer).status_code == 200
    pending = client.post("/api/auth/login", json={"email": username_for(customer), "password": PASSWORD, "portal": "customer"}, headers=mode).json()
    token = pending["pending_token"]
    setup = client.post("/api/customer/totp/setup", headers={"Authorization": "Bearer " + token}).json()
    security._used_codes.clear()
    done = client.post("/api/customer/totp/confirm", headers={"Authorization": "Bearer " + token, **mode}, json={"code": security.totp_now(setup["secret"])})
    assert done.status_code == 200, done.text
    assert session_scope  # (imported for the next check in tests that need it)
    assert User
    return done


# ------------------------------------------------------------------ setting the cookies

def test_a_browser_sign_in_sets_http_only_strict_cookies_and_keeps_tokens_out_of_the_body(boot):
    with TestClient(app) as client:
        reply = operator_in(client)
        body = reply.json()
        assert "token" not in body and "refresh_token" not in body and body["session"] == "cookie" and body["user"]["role"] == "fraud_analyst"
        cookies = {c.split("=", 1)[0]: c for c in set_cookies(reply)}
        assert set(cookies) == {"ps_operator_at", "ps_operator_rt"}
        for cookie in cookies.values():
            lowered = cookie.lower()
            assert "httponly" in lowered and "samesite=strict" in lowered and "path=/" in lowered and "secure" not in lowered.replace("samesite", "")
        assert "max-age=1800" in cookies["ps_operator_at"].lower() or "max-age=" in cookies["ps_operator_at"].lower()
        # the token never appears anywhere in the body, only in the cookie
        value = cookies["ps_operator_at"].split(";", 1)[0].split("=", 1)[1]
        assert value and value not in reply.text


def test_without_the_browser_header_the_tokens_still_come_in_the_body_and_no_cookie_is_set(boot):
    with TestClient(app) as client:
        reply = client.post("/api/auth/login", json=OPERATOR)
        assert reply.json()["token"] and reply.json()["refresh_token"] and set_cookies(reply) == []


def test_the_cookie_is_marked_secure_when_the_service_is_configured_for_it(boot, monkeypatch):
    with TestClient(app) as client:  # the service starts inside this block, so the setting is changed after it has
        monkeypatch.setattr(runtime, "settings", replace(runtime.settings, cookie_secure=True))
        assert all("secure" in c.lower().replace("samesite", "") for c in set_cookies(operator_in(client)))


def test_secure_cookies_are_the_default_in_production_and_off_in_development(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    assert Settings.from_env().cookie_secure is False
    monkeypatch.setenv("APP_ENV", "production")
    assert Settings.from_env().cookie_secure is True
    monkeypatch.setenv("COOKIE_SECURE", "")  # a Compose file that passes the name through with no value
    assert Settings.from_env().cookie_secure is True
    monkeypatch.setenv("COOKIE_SECURE", "false")
    assert Settings.from_env().cookie_secure is False


# ------------------------------------------------------------------ reading them

def test_a_signed_in_browser_reads_with_the_cookie_alone(boot):
    with TestClient(app) as client:
        operator_in(client)
        me = client.get("/api/me")
        assert me.status_code == 200 and me.json()["role"] == "fraud_analyst"
        assert client.get("/api/overview").status_code == 200


def test_a_state_changing_request_that_relies_on_the_cookie_needs_the_site_header(boot):
    with TestClient(app) as client:
        operator_in(client)
        refused = client.post("/api/audit/verify")
        assert refused.status_code == 403 and "PayShield site" in refused.json()["detail"]
        assert client.post("/api/audit/verify", headers={"X-Requested-With": "payshield"}).status_code == 200
        assert client.post("/api/audit/verify", headers={"X-Requested-With": "something-else"}).status_code == 403
        assert client.get("/api/overview").status_code == 200  # reading needs no header


def test_a_bearer_token_needs_no_site_header_and_wins_over_a_cookie(boot):
    with TestClient(app) as client:
        operator_in(client)
        other = security.create_token(runtime.settings, "usr_auditor", "auditor", purpose="access")
        me = client.get("/api/me", headers={"Authorization": "Bearer " + other})
        assert me.json()["role"] == "auditor"  # the header decided, not the cookie
        assert client.post("/api/audit/verify", headers={"Authorization": "Bearer " + other}).status_code == 200


def test_each_portal_uses_only_its_own_cookie(boot):
    with TestClient(app) as client:
        operator_in(client)
        assert client.get("/api/customer/home").status_code == 401  # an operator cookie is not a customer session
    with TestClient(app) as client:
        customer_in(client)
        assert client.get("/api/customer/home").status_code == 200
        assert client.get("/api/overview").status_code == 401  # and a customer cookie is not an operator session


def test_both_portals_can_be_signed_in_in_one_browser(boot):
    with TestClient(app) as client:
        customer_in(client)
        operator_in(client)
        assert client.get("/api/customer/home").status_code == 200
        assert client.get("/api/me").json()["role"] == "fraud_analyst"


def test_a_bad_expired_or_wrong_kind_of_cookie_is_not_a_session(boot):
    with TestClient(app) as client:
        client.cookies.set("ps_operator_at", "garbage")
        assert client.get("/api/me").status_code == 401
        client.cookies.set("ps_operator_at", security.create_token(runtime.settings, "usr_analyst", "fraud_analyst", purpose="access", minutes=-1))
        assert client.get("/api/me").status_code == 401
        client.cookies.set("ps_operator_at", security.create_token(runtime.settings, "usr_analyst", "fraud_analyst", purpose="refresh"))
        assert client.get("/api/me").status_code == 401  # a refresh token is not an access token
        client.cookies.set("ps_operator_at", security.create_token(runtime.settings, "usr_analyst", "fraud_analyst", purpose="totp"))
        assert client.get("/api/me").status_code == 401  # nor is a half-finished sign-in


def test_a_disabled_account_loses_its_cookie_session_at_once(boot):
    from app.db import session_scope
    from app.models import User

    with TestClient(app) as client:
        operator_in(client)
        with session_scope() as session:
            session.get(User, "usr_analyst").status = "disabled"
        assert client.get("/api/me").status_code == 401


# ------------------------------------------------------------------ renewing and leaving

def test_the_refresh_cookie_renews_the_access_cookie(boot):
    with TestClient(app) as client:
        operator_in(client)
        client.cookies.delete("ps_operator_at")  # as if the 30-minute cookie had run out
        assert client.get("/api/me").status_code == 401
        refused = client.post("/api/auth/refresh", headers=COOKIE, json={"portal": "operator"})
        assert refused.status_code == 403  # the site header is missing
        renewed = client.post("/api/auth/refresh", headers=SITE, json={"portal": "operator"})
        assert renewed.status_code == 200 and "token" not in renewed.json() and any(c.startswith("ps_operator_at=") for c in set_cookies(renewed))
        assert client.get("/api/me").status_code == 200


def test_renewing_needs_the_refresh_cookie_and_the_right_portal(boot):
    with TestClient(app) as client:
        assert client.post("/api/auth/refresh", headers=SITE, json={"portal": "operator"}).status_code == 401  # no cookie at all
        operator_in(client)
        assert client.post("/api/auth/refresh", headers=SITE, json={"portal": "customer"}).status_code == 401  # no customer cookie
        client.cookies.set("ps_customer_rt", client.cookies.get("ps_operator_rt"))
        assert client.post("/api/auth/refresh", headers=SITE, json={"portal": "customer"}).status_code == 401  # an operator's token is not a customer's
        # a token in the body is ignored in cookie mode
        client.cookies.delete("ps_operator_rt")
        body_token = security.create_token(runtime.settings, "usr_analyst", "fraud_analyst", purpose="refresh")
        assert client.post("/api/auth/refresh", headers=SITE, json={"portal": "operator", "refresh_token": body_token}).status_code == 401


def test_bearer_refresh_still_works_for_other_clients(boot):
    with TestClient(app) as client:
        refresh = client.post("/api/auth/login", json=OPERATOR).json()["refresh_token"]
        assert client.post("/api/auth/refresh", json={"refresh_token": refresh}).json()["token"]


def test_signing_out_clears_the_cookies(boot):
    with TestClient(app) as client:
        operator_in(client)
        out = client.post("/api/auth/logout", headers=SITE, json={"portal": "operator"})
        assert out.status_code == 200
        cleared = {c.split("=", 1)[0]: c.lower() for c in set_cookies(out)}
        assert set(cleared) == {"ps_operator_at", "ps_operator_rt"} and all("max-age=0" in c or "expires=" in c for c in cleared.values())
        assert client.get("/api/me").status_code == 401
        assert client.post("/api/auth/logout").status_code == 200  # signing out twice is harmless


def test_signing_out_of_one_portal_leaves_the_other_signed_in(boot):
    with TestClient(app) as client:
        customer_in(client)
        operator_in(client)
        client.post("/api/auth/logout", headers=SITE, json={"portal": "customer"})
        assert client.get("/api/customer/home").status_code == 401
        assert client.get("/api/me").status_code == 200


# ------------------------------------------------------------------ customers

def test_a_customer_gets_cookies_only_after_the_authenticator_code(boot):
    with TestClient(app) as client:
        assert register(client).status_code == 200
        step = client.post("/api/auth/login", json={"email": username_for("cus_nimal"), "password": PASSWORD, "portal": "customer"}, headers=COOKIE)
        assert set_cookies(step) == [] and step.json()["step"] == "enrol"  # the password alone sets nothing
        assert client.get("/api/customer/home").status_code == 401
    with TestClient(app) as client:
        client.cookies.clear()
        from app.db import session_scope as _scope
        from app.models import User as _User

        # finish the set-up the registration above started, the way the browser does
        token = client.post("/api/auth/login", json={"email": username_for("cus_nimal"), "password": PASSWORD, "portal": "customer"}, headers=COOKIE).json()["pending_token"]
        setup = client.post("/api/customer/totp/setup", headers={"Authorization": "Bearer " + token}).json()
        security._used_codes.clear()
        done = client.post("/api/customer/totp/confirm", headers={"Authorization": "Bearer " + token, **COOKIE}, json={"code": security.totp_now(setup["secret"])})
        assert _scope and _User
        assert {c.split("=", 1)[0] for c in set_cookies(done)} == {"ps_customer_at", "ps_customer_rt"} and "token" not in done.json()
        again = client.post("/api/auth/login", json={"email": username_for("cus_nimal"), "password": PASSWORD, "portal": "customer"}, headers=COOKIE).json()
        security._used_codes.clear()
        from app.db import session_scope
        from app.models import User

        with session_scope() as session:
            secret = security.open_totp(session.get(User, "cus_nimal").totp_secret)
        final = client.post("/api/auth/totp", headers=COOKIE, json={"pending_token": again["pending_token"], "code": security.totp_now(secret)})
        assert final.status_code == 200 and {c.split("=", 1)[0] for c in set_cookies(final)} == {"ps_customer_at", "ps_customer_rt"}


# ------------------------------------------------------------------ the live stream

def test_the_live_stream_accepts_the_cookie_from_our_own_pages_only(boot):
    with TestClient(app) as client:
        operator_in(client)
        origin = {"origin": "http://localhost:3000"}
        with client.websocket_connect("/ws?portal=operator", headers=origin) as socket:
            assert socket.receive_json()["type"] == "snapshot"
        with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect("/ws?portal=operator", headers={"origin": "https://evil.example"}) as socket:
            socket.receive_json()
        assert refused.value.code == 4403
        with pytest.raises(WebSocketDisconnect) as wrong_portal, client.websocket_connect("/ws?portal=customer", headers=origin) as socket:
            socket.receive_json()
        assert wrong_portal.value.code == 4401  # no customer cookie


def test_the_live_stream_without_any_credential_is_refused_and_a_query_token_still_works(boot):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect("/ws") as socket:
            socket.receive_json()
        assert refused.value.code == 4401
        token = client.post("/api/auth/login", json=OPERATOR).json()["token"]
        with client.websocket_connect(f"/ws?token={token}") as socket:
            assert socket.receive_json()["type"] == "snapshot"
