"""Customer sign-in needs the password and then an authenticator code. Also: who the backend believes a visitor is."""
import pytest
from fastapi.testclient import TestClient
from helpers import PASSWORD, authenticate, record, register, sign_in, username_for
from starlette.requests import Request

from app import ratelimit, security
from app.api import _client_ip
from app.db import session_scope
from app.main import app
from app.models import User
from app.runtime import runtime
from app.seed import CUSTOMERS


@pytest.fixture(autouse=True)
def _fresh():
    ratelimit.reset_all()
    security._used_codes.clear()
    yield
    ratelimit.reset_all()
    security._used_codes.clear()


def password_step(client, customer="cus_nimal", password=PASSWORD, ip=None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return client.post("/api/auth/login", json={"email": username_for(customer), "password": password, "portal": "customer"}, headers=headers)


def registered(client, customer="cus_nimal"):
    assert register(client, customer).status_code == 200


def secret_of(customer="cus_nimal"):
    with session_scope() as session:
        return security.open_totp(session.get(User, customer).totp_secret)


# ------------------------------------------------------------------ the two steps

def test_the_password_alone_never_opens_a_session(boot):
    with TestClient(app) as client:
        registered(client)
        reply = password_step(client)
        body = reply.json()
        assert reply.status_code == 200 and "token" not in body and "refresh_token" not in body and "user" not in body
        assert body["step"] == "enrol" and body["pending_token"]  # registered, but the authenticator was never confirmed
        # the pending token opens nothing else
        assert client.get("/api/customer/account", headers={"Authorization": "Bearer " + body["pending_token"]}).status_code == 401


def test_after_the_first_code_the_next_sign_in_asks_for_a_code_and_a_right_code_signs_in(boot):
    with TestClient(app) as client:
        registered(client)
        authenticate(client, password_step(client).json())  # first time: set up and confirm
        again = password_step(client).json()
        assert again["step"] == "totp"
        security._used_codes.clear()
        done = client.post("/api/auth/totp", json={"pending_token": again["pending_token"], "code": security.totp_now(secret_of())})
        assert done.status_code == 200 and done.json()["token"] and done.json()["user"]["bank"] == "BankCylone"
        assert client.get("/api/customer/account", headers={"Authorization": "Bearer " + done.json()["token"]}).status_code == 200


def test_a_wrong_code_is_refused_and_five_wrong_codes_lock_the_account_for_a_while(boot):
    with TestClient(app) as client:
        registered(client)
        authenticate(client, password_step(client).json())
        pending = password_step(client).json()["pending_token"]
        for _ in range(5):
            assert client.post("/api/auth/totp", json={"pending_token": pending, "code": "000000"}).status_code == 401
        real = security.totp_now(secret_of())
        assert client.post("/api/auth/totp", json={"pending_token": pending, "code": real}).status_code == 429


def test_a_code_cannot_be_used_twice(boot):
    with TestClient(app) as client:
        registered(client)
        authenticate(client, password_step(client).json())
        code = security.totp_now(secret_of())
        security._used_codes.clear()
        first = password_step(client).json()["pending_token"]
        assert client.post("/api/auth/totp", json={"pending_token": first, "code": code}).status_code == 200
        second = password_step(client).json()["pending_token"]
        assert client.post("/api/auth/totp", json={"pending_token": second, "code": code}).status_code == 401


def test_pending_tokens_of_the_wrong_kind_or_age_do_nothing(boot):
    with TestClient(app) as client:
        registered(client)
        authenticate(client, password_step(client).json())
        code = security.totp_now(secret_of())
        security._used_codes.clear()
        access = security.create_token(runtime.settings, "cus_nimal", "customer", purpose="access")
        assert client.post("/api/auth/totp", json={"pending_token": access, "code": code}).status_code == 401
        expired = security.create_token(runtime.settings, "cus_nimal", "customer", purpose="totp", minutes=-1)
        assert client.post("/api/auth/totp", json={"pending_token": expired, "code": code}).status_code == 401
        assert client.post("/api/auth/totp", json={"pending_token": "garbage", "code": code}).status_code == 401


def test_a_disabled_customer_cannot_finish_a_sign_in_that_was_already_started(boot):
    with TestClient(app) as client:
        registered(client)
        authenticate(client, password_step(client).json())
        pending = password_step(client).json()["pending_token"]
        with session_scope() as session:
            session.get(User, "cus_nimal").status = "disabled"
        security._used_codes.clear()
        assert client.post("/api/auth/totp", json={"pending_token": pending, "code": security.totp_now(secret_of())}).status_code == 400


def test_a_wrong_password_never_reaches_the_code_step(boot):
    with TestClient(app) as client:
        registered(client)
        reply = password_step(client, password="Wrong-9999")
        assert reply.status_code == 401 and "pending_token" not in reply.text


def test_operators_sign_in_as_before(boot):
    with TestClient(app) as client:
        reply = client.post("/api/auth/login", json={"email": "analyst@payshield.lk", "password": "pilot-analyst", "portal": "operator"})
        assert reply.status_code == 200 and reply.json()["token"]


# ------------------------------------------------------------------ existing customers and the preset demonstration secret

def test_an_existing_registered_customer_with_no_authenticator_is_forced_to_set_one_up(boot):
    with TestClient(app) as client:
        registered(client)
        with session_scope() as session:
            row = session.get(User, "cus_nimal")
            row.totp_secret, row.totp_enrolled = None, False  # an account from before codes were required
        step = password_step(client).json()
        assert step["step"] == "enrol"
        done = authenticate(client, step)
        assert done["token"]
        assert password_step(client).json()["step"] == "totp"


def test_registration_ends_at_the_authenticator_step_not_in_a_session(boot):
    with TestClient(app) as client:
        reply = register(client)
        assert "token" not in reply.json() and reply.json()["step"] == "enrol"
        with session_scope() as session:
            assert session.get(User, "cus_nimal").totp_enrolled is False


def test_a_demonstration_customer_keeps_the_preset_secret_through_registration_and_a_reset(boot):
    from app.demo import unregister

    preset = record("cus_nimal")["totp"]
    with TestClient(app) as client:
        reply = register(client)
        setup = client.post("/api/customer/totp/setup", headers={"Authorization": "Bearer " + reply.json()["pending_token"]})
        assert setup.json()["secret"] == preset  # the secret the presenter already added to the app
        again = client.post("/api/customer/totp/setup", headers={"Authorization": "Bearer " + reply.json()["pending_token"]})
        assert again.json()["secret"] == preset  # reloading the screen does not change it
        security._used_codes.clear()
        confirmed = client.post("/api/customer/totp/confirm", headers={"Authorization": "Bearer " + reply.json()["pending_token"]}, json={"code": security.totp_now(preset)})
        assert confirmed.status_code == 200
        assert unregister("cus_nimal")
        assert secret_of() == preset
        with session_scope() as session:
            assert session.get(User, "cus_nimal").totp_enrolled is False


def test_demo_assist_shows_the_preset_authenticator_only_while_demo_assist_is_on(boot, monkeypatch):
    from dataclasses import replace

    from app.demo import assist

    shown = assist("cus_nimal")["authenticator"]
    assert shown["secret"] == record("cus_nimal")["totp"] and shown["otpauth_uri"].startswith("otpauth://totp/") and shown["qr_png_base64"]
    monkeypatch.setattr(runtime, "settings", replace(runtime.settings, demo_assist=False))
    with pytest.raises(PermissionError):
        assist("cus_nimal")


def test_every_seeded_customer_has_its_own_secret():
    secrets = [row["totp"] for row in CUSTOMERS]
    assert len(set(secrets)) == len(secrets) and all(len(s) >= 16 for s in secrets)


def test_the_sign_in_helper_really_exercises_both_steps(boot):
    with TestClient(app) as client:
        registered(client)
        headers = sign_in(client)
        assert client.get("/api/customer/account", headers=headers).status_code == 200


# ------------------------------------------------------------------ whose address is it

def request(peer, forwarded=None):
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers, "client": (peer, 5000), "query_string": b""})


def test_the_forwarded_address_is_believed_only_from_a_trusted_proxy(boot):
    assert _client_ip(request("127.0.0.1", "203.0.113.9")) == "203.0.113.9"
    assert _client_ip(request("127.0.0.1")) == "127.0.0.1"
    assert _client_ip(request("198.51.100.4", "203.0.113.9")) == "198.51.100.4"  # not a proxy: the header is ignored


def test_the_header_is_read_from_the_right_so_a_made_up_left_part_does_not_count(boot):
    assert _client_ip(request("127.0.0.1", "6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert _client_ip(request("127.0.0.1", "203.0.113.9, 127.0.0.1")) == "203.0.113.9"
    assert _client_ip(request("::1", "")) == "::1"


def test_trusted_proxies_accept_ranges(boot, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(runtime, "settings", replace(runtime.settings, trusted_proxies=("172.16.0.0/12",)))
    assert _client_ip(request("172.18.0.3", "203.0.113.9")) == "203.0.113.9"
    assert _client_ip(request("127.0.0.1", "203.0.113.9")) == "127.0.0.1"


def test_one_visitors_failed_passwords_do_not_lock_the_real_customer_out(boot):
    # The web server's proxy is the direct caller (127.0.0.1) and names the visitor in X-Forwarded-For.
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        registered(client)
        attacker, owner = "203.0.113.7", "203.0.113.50"
        for _ in range(5):
            assert password_step(client, password="Wrong-9999", ip=attacker).status_code == 401
        assert password_step(client, password="Wrong-9999", ip=attacker).status_code == 429  # the attacker is stopped
        assert password_step(client, ip=attacker).status_code == 429  # even with the right password, for now
        reply = password_step(client, ip=owner)  # the owner, from another address, is not
        assert reply.status_code == 200 and reply.json()["pending_token"]


def test_a_forged_header_from_an_ordinary_visitor_is_ignored(boot):
    with TestClient(app, client=("198.51.100.4", 50000)) as client:  # not a trusted proxy
        registered(client)
        for number in range(5):
            assert password_step(client, password="Wrong-9999", ip=f"203.0.113.{number}").status_code == 401
        assert password_step(client, password="Wrong-9999", ip="203.0.113.99").status_code == 429  # one real address, one counter


def test_a_spread_out_guessing_run_against_one_name_still_hits_a_cap(boot):
    for number in range(30):
        ratelimit.hit("login_fail_user", "nimal perera")
        ratelimit.hit("login_fail", f"nimal perera|198.51.100.{number}")
    with pytest.raises(Exception) as caught:
        ratelimit.check("login_fail_user", "nimal perera")
    assert getattr(caught.value, "status_code", None) == 429


def test_the_customer_home_says_how_many_payments_are_waiting(boot):
    from helpers import sign_in

    from app.agents.runner import begin_case
    from app.demo import trigger

    with TestClient(app) as client:
        registered(client)
        headers = sign_in(client)
        assert client.get("/api/customer/home", headers=headers).json()["open_cases"] == 0
        for _ in range(2):
            begin_case(trigger("cus_nimal", "sim_swap_transfer"))
        home = client.get("/api/customer/home", headers=headers).json()
        assert home["open_cases"] == 2 and home["active"]["payee"] == "K.D. Enterprises"
