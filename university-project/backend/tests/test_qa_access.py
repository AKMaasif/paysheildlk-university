"""Who may see or touch which record: customer against customer, bank against bank, tokens that are not what they claim."""
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from helpers import PASSWORD, register

from app.agents.runner import begin_case
from app.db import session_scope
from app.demo import trigger
from app.main import app
from app.models import Case, Report, Transaction, User
from app.runtime import runtime
from app.security import create_token


def bearer(user_id, role, **kw):
    return {"Authorization": "Bearer " + create_token(runtime.settings, user_id, role, **kw)}


@pytest.fixture()
def world(boot):
    """Nimal's case at BankCylone and Dilani's case moved to a second bank, both held and waiting for their owners."""
    nimal = trigger("cus_nimal", "sim_swap_transfer")
    dilani = trigger("cus_dilani", "sim_swap_transfer")
    begin_case(nimal)
    begin_case(dilani)
    with session_scope() as session:
        session.get(User, "cus_dilani").bank = "Island Savings"
        session.get(Case, dilani).bank = "Island Savings"
        for case_id in (nimal, dilani):
            session.add(Report(id=f"rpt_{case_id}", case_id=case_id, title="t", markdown="# secret body", sha256="0" * 64, created_at=session.get(Case, case_id).created_at))
        tx = {"nimal": session.get(Case, nimal).transaction_id, "dilani": session.get(Case, dilani).transaction_id}
    return {"nimal": nimal, "dilani": dilani, "tx": tx}


# ------------------------------------------------------------------ customer against customer

CUSTOMER_CASE_CALLS = [
    ("POST", "/api/customer/cases/{c}/respond", {"decision": "yes"}),
    ("POST", "/api/customer/cases/{c}/respond", {"decision": "no"}),
    ("POST", "/api/customer/cases/{c}/verify", {"answers": {"school": "x"}, "liveness": True}),
    ("POST", "/api/customer/cases/{c}/liveness", {"frames": []}),
    ("GET", "/api/customer/cases/{c}/receipt", None),
]


@pytest.mark.parametrize("method,path,body", CUSTOMER_CASE_CALLS)
def test_a_customer_cannot_act_on_or_read_another_customers_case(world, method, path, body):
    with TestClient(app) as client:
        reply = client.request(method, path.format(c=world["dilani"]), json=body, headers=bearer("cus_nimal", "customer"))
        assert reply.status_code in {403, 404}, (path, reply.status_code, reply.text[:200])
        assert "Dilani" not in reply.text and "dilani" not in reply.text.lower()
    with session_scope() as session:  # nothing about the other case moved
        assert session.get(Case, world["dilani"]).status == "waiting_customer"
        assert session.get(Transaction, world["tx"]["dilani"]).status == "held"


def test_a_customer_cannot_pass_a_case_id_that_does_not_exist(world):
    with TestClient(app) as client:
        for method, path, body in CUSTOMER_CASE_CALLS:
            reply = client.request(method, path.format(c="PS-99-99999"), json=body, headers=bearer("cus_nimal", "customer"))
            assert reply.status_code in {403, 404}


def test_the_home_and_account_views_show_only_the_signed_in_customer(world):
    with TestClient(app) as client:
        for path in ("/api/customer/home", "/api/customer/account"):
            text = client.get(path, headers=bearer("cus_nimal", "customer")).text
            assert "Dilani" not in text and world["dilani"] not in text and "Island Savings" not in text, path
        home = client.get("/api/customer/home", headers=bearer("cus_nimal", "customer")).text
        assert world["nimal"] in home


def test_a_customer_cannot_read_operator_records_by_id(world):
    with TestClient(app) as client:
        h = bearer("cus_nimal", "customer")
        for path in (f"/api/cases/{world['nimal']}", f"/api/reports/rpt_{world['nimal']}", f"/api/reports/rpt_{world['nimal']}/pdf",
                     f"/api/reports/rpt_{world['nimal']}/markdown"):
            assert client.get(path, headers=h).status_code == 403, path
        assert client.post(f"/api/transactions/{world['tx']['nimal']}/override", json={"action": "release", "reason": "please let me"}, headers=h).status_code == 403
        assert client.post(f"/api/cases/{world['nimal']}/kill", json={"action": "block", "reason": "I do not like it"}, headers=h).status_code == 403


def test_password_change_only_ever_changes_the_callers_own_password(boot):
    with TestClient(app) as client:
        assert register(client).status_code == 200
        with session_scope() as session:
            other = session.get(User, "cus_dilani").password_hash
        reply = client.post("/api/customer/password", json={"old_password": PASSWORD, "new_password": "Lantern-7391", "confirm_password": "Lantern-7391",
                                                             "user_id": "cus_dilani", "customer_id": "cus_dilani"}, headers=bearer("cus_nimal", "customer"))
        assert reply.status_code == 200, reply.text
    with session_scope() as session:
        assert session.get(User, "cus_dilani").password_hash == other


# ------------------------------------------------------------------ bank against bank

def test_a_bank_supervisor_cannot_open_another_banks_case_or_report(world):
    with TestClient(app) as client:
        sup = bearer("usr_supervisor", "bank_supervisor")
        assert client.get(f"/api/cases/{world['nimal']}", headers=sup).status_code == 200
        assert client.get(f"/api/cases/{world['dilani']}", headers=sup).status_code == 404
        for suffix in ("", "/pdf", "/markdown"):
            assert client.get(f"/api/reports/rpt_{world['nimal']}{suffix}", headers=sup).status_code == 200, suffix
            assert client.get(f"/api/reports/rpt_{world['dilani']}{suffix}", headers=sup).status_code == 404, suffix


def test_a_bank_supervisor_cannot_release_or_block_another_banks_payment(world):
    with TestClient(app) as client:
        sup = bearer("usr_supervisor", "bank_supervisor")
        for action in ("release", "block"):
            reply = client.post(f"/api/transactions/{world['tx']['dilani']}/override", json={"action": action, "reason": "not my bank at all"}, headers=sup)
            assert reply.status_code == 403
    with session_scope() as session:
        assert session.get(Transaction, world["tx"]["dilani"]).status == "held"


@pytest.mark.parametrize("action", ["freeze", "block"])
def test_a_bank_supervisor_cannot_use_the_manual_switch_on_another_banks_case(world, action):
    with TestClient(app) as client:
        sup = bearer("usr_supervisor", "bank_supervisor")
        reply = client.post(f"/api/cases/{world['dilani']}/kill", json={"action": action, "reason": "I am from a different bank"}, headers=sup)
        assert reply.status_code in {403, 404}, reply.text
    with session_scope() as session:
        assert not session.get(User, "cus_dilani").restricted
        assert session.get(Transaction, world["tx"]["dilani"]).status == "held"


def test_lists_and_totals_for_a_bank_supervisor_leave_out_other_banks(world):
    with TestClient(app) as client:
        sup = bearer("usr_supervisor", "bank_supervisor")
        for path in ("/api/cases", "/api/holds", "/api/overview", "/api/map", "/api/analytics", "/api/reports"):
            reply = client.get(path, headers=sup)
            if reply.status_code == 404:
                continue
            assert reply.status_code == 200, path
            assert world["dilani"] not in reply.text and "Island Savings" not in reply.text.replace("by_bank", ""), path
        # Asking for the other bank by name does not widen the view.
        assert world["dilani"] not in client.get("/api/cases?bank=Island Savings", headers=sup).text
        assert world["dilani"] not in client.get("/api/cases?bank=all", headers=sup).text
        # An analyst, who is not tied to a bank, sees both.
        assert world["dilani"] in client.get("/api/cases", headers=bearer("usr_analyst", "fraud_analyst")).text


# ------------------------------------------------------------------ tokens that are not what they claim

def _forged(payload, key=None, algorithm="HS256"):
    base = {"sub": "usr_admin", "role": "super_admin", "purpose": "access", "iss": "payshield", "iat": int(time.time()), "exp": int(time.time()) + 600}
    return jwt.encode({**base, **payload}, key or runtime.settings.jwt_secret, algorithm=algorithm)


def _status(token):
    with TestClient(app) as client:
        return client.get("/api/overview", headers={"Authorization": "Bearer " + token}).status_code


def test_a_good_token_works_and_these_forgeries_do_not(boot):
    assert _status(_forged({})) == 200
    assert _status(_forged({}, key="not-the-secret")) == 401
    assert _status(_forged({"exp": int(time.time()) - 5})) == 401
    assert _status(_forged({"iss": "someone-else"})) == 401
    assert _status(_forged({"purpose": "refresh"})) == 401
    assert _status(_forged({"purpose": "step_up"})) == 401
    assert _status(_forged({"sub": "usr_nobody"})) == 401
    forged_none = jwt.encode({"sub": "usr_admin", "role": "super_admin", "purpose": "access", "iss": "payshield"}, key=None, algorithm="none")
    assert _status(forged_none) == 401
    assert _status("") == 401 and _status("garbage") == 401 and _status("a.b.c") == 401


def test_the_role_inside_a_token_is_not_trusted(boot):
    """A customer's token that claims to be an admin is still a customer."""
    claimed = _forged({"sub": "cus_nimal", "role": "super_admin"})
    assert _status(claimed) == 403
    with TestClient(app) as client:
        assert client.get("/api/admin/audit", headers={"Authorization": "Bearer " + claimed}).status_code == 403
    analyst_claims_admin = _forged({"sub": "usr_analyst", "role": "super_admin"})
    with TestClient(app) as client:
        assert client.get("/api/admin/audit", headers={"Authorization": "Bearer " + analyst_claims_admin}).status_code == 403


def test_a_suspended_user_loses_access_even_with_a_valid_token(boot):
    token = bearer("usr_telco", "telecom_operator")
    with TestClient(app) as client:
        assert client.get("/api/overview", headers=token).status_code == 200
        with session_scope() as session:
            session.get(User, "usr_telco").status = "suspended"
        assert client.get("/api/overview", headers=token).status_code in {401, 403}


def test_a_refresh_token_cannot_be_used_as_an_access_token(boot):
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"email": "analyst@payshield.lk", "password": "pilot-analyst", "portal": "operator"}).json()
        refresh = login.get("refresh_token")
        assert refresh
        assert client.get("/api/overview", headers={"Authorization": "Bearer " + refresh}).status_code == 401


def test_an_operator_token_does_not_open_the_customer_portal_and_back(boot):
    with TestClient(app) as client:
        assert client.get("/api/customer/home", headers=bearer("usr_analyst", "fraud_analyst")).status_code == 403
        assert client.get("/api/overview", headers=bearer("cus_nimal", "customer")).status_code == 403


# ------------------------------------------------------------------ the live stream

def _ws_closed_with(client, token):
    from starlette.websockets import WebSocketDisconnect

    try:
        with client.websocket_connect("/ws" + (f"?token={token}" if token is not None else "")) as ws:
            ws.receive_json()
        return None
    except WebSocketDisconnect as exc:
        return exc.code


def test_the_live_stream_refuses_a_missing_wrong_or_wrong_purpose_token(boot):
    with TestClient(app) as client:
        assert _ws_closed_with(client, None) == 4401
        assert _ws_closed_with(client, "garbage") == 4401
        assert _ws_closed_with(client, _forged({"purpose": "refresh"})) == 4401
        assert _ws_closed_with(client, _forged({"sub": "usr_nobody"})) == 4401
        assert _ws_closed_with(client, _forged({}, key="nope")) == 4401


def _snapshot_for(client, user_id, role):
    token = create_token(runtime.settings, user_id, role)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        first = ws.receive_json()
    assert first["type"] == "snapshot"
    return first["events"]


def test_the_live_stream_shows_each_person_only_what_they_may_see(world):
    with TestClient(app) as client:
        analyst = {e.get("case_id") for e in _snapshot_for(client, "usr_analyst", "fraud_analyst")}
        assert world["nimal"] in analyst and world["dilani"] in analyst
        supervisor = {e.get("case_id") for e in _snapshot_for(client, "usr_supervisor", "bank_supervisor")}
        assert world["nimal"] in supervisor and world["dilani"] not in supervisor
        nimal = _snapshot_for(client, "cus_nimal", "customer")
        assert nimal and all(e["customer_id"] == "cus_nimal" for e in nimal)
        assert world["dilani"] not in str(nimal)
