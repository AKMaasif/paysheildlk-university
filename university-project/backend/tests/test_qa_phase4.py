"""Usernames the customer chooses, size limits on what the camera step accepts, and no claimed verdicts."""
import base64
import os

import pytest
from fastapi.testclient import TestClient
from helpers import PASSWORD, authenticate, live_selfie, register, username_for

from app import limits
from app.agents.runner import begin_case, resume_case
from app.api import VerifyIn
from app.db import session_scope
from app.demo import trigger
from app.main import app
from app.models import Case, User


def login(client, name, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": name, "password": password, "portal": "customer"})


def stored_username(customer="cus_nimal"):
    with session_scope() as session:
        return session.get(User, customer).username


# ------------------------------------------------------------------ usernames

def test_a_chosen_username_is_stored_in_lower_case_and_signs_in_regardless_of_case_or_spaces(boot):
    with TestClient(app) as client:
        reply = register(client, username="Nimal_P-85")
        assert reply.status_code == 200 and reply.json()["username"] == "nimal_p-85"
        assert stored_username() == "nimal_p-85"
        for typed in ("nimal_p-85", "NIMAL_P-85", "  Nimal_P-85  "):
            assert login(client, typed).json()["step"] == "enrol", typed
        assert login(client, "nimal perera").status_code == 401  # the bank's name is no longer a way in
        assert login(client, "nimal.perera").status_code == 401


@pytest.mark.parametrize("bad", ["abc", "x" * 31, "has space", "semi;colon", "quote'd", "tab\tname", "-leading", "ünï-code"])
def test_a_username_that_breaks_the_rule_is_refused_and_nothing_is_registered(boot, bad):
    with TestClient(app) as client:
        reply = register(client, username=bad)
        assert reply.status_code == 422 and "username" in reply.json()["detail"].lower(), (bad, reply.text)
    with session_scope() as session:
        assert session.get(User, "cus_nimal").registered is False


def test_a_taken_username_is_refused_whatever_its_letter_case(boot):
    with TestClient(app) as client:
        assert register(client, "cus_nimal", username="shared.name").status_code == 200
        clash = register(client, "cus_dilani", username="Shared.Name")
        assert clash.status_code == 409 and "taken" in clash.json()["detail"]
        assert register(client, "cus_dilani", username="dilani.w").status_code == 200  # the second customer can still register
    assert stored_username("cus_dilani") == "dilani.w"


def test_without_a_choice_a_username_is_made_that_is_not_the_banks_record_of_the_name(boot):
    with TestClient(app) as client:
        reply = client.post("/api/customer/register", json={**__import__("helpers").registration("cus_nimal"), "username": None})
        assert reply.status_code == 200
        made = reply.json()["username"]
        assert made.startswith("nimal.perera-") and len(made) == len("nimal.perera-") + 4 and made != "nimal perera"
        assert login(client, made).json()["step"] == "enrol"
        assert login(client, "nimal perera").status_code == 401


def test_two_people_with_the_same_name_get_different_generated_usernames(boot):
    with TestClient(app) as client:
        first = client.post("/api/customer/register", json={**__import__("helpers").registration("cus_nimal"), "username": ""}).json()["username"]
    with session_scope() as session:
        twin = session.get(User, "cus_dilani")
        twin.full_name = "Nimal Perera"  # a different customer with the same name
    with TestClient(app) as client:
        second = client.post("/api/customer/register", json={**__import__("helpers").registration("cus_dilani", full_name="Nimal Perera"), "username": ""})
        # the bank record must match, so this registers only if the details do; compare when it does
        if second.status_code == 200:
            assert second.json()["username"] != first


def test_a_customer_registered_before_this_change_keeps_signing_in_with_the_name_they_had(boot):
    with TestClient(app) as client:
        assert register(client).status_code == 200
        with session_scope() as session:
            session.get(User, "cus_nimal").username = "nimal perera"  # how earlier registrations were stored
        assert login(client, "Nimal  Perera").json()["step"] == "enrol"
        assert authenticate(client, login(client, "nimal perera").json())["token"]


def test_the_username_is_what_the_audit_log_and_the_welcome_email_use(boot):
    from app.models import AuditLog, Notification

    with TestClient(app) as client:
        register(client, username="audit.me")
    with session_scope() as session:
        assert any(r.actor_email == "audit.me" and r.action == "customer_registered" for r in session.query(AuditLog).all())
        body = " ".join(n.body for n in session.query(Notification).filter(Notification.user_id == "cus_nimal").all())
        assert "audit.me" in body


# ------------------------------------------------------------------ the camera step

def waiting_for_selfie():
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    assert resume_case(case_id, {"decision": "yes"}, "waiting_customer")
    return case_id


def customer_headers(client):
    from app.runtime import runtime
    from app.security import create_token

    return {"Authorization": "Bearer " + create_token(runtime.settings, "cus_nimal", "customer")}


def post_frames(client, case_id, payload, **kw):
    return client.post(f"/api/customer/cases/{case_id}/liveness", json=payload, headers=customer_headers(client), **kw)


def frame(size=2000):
    return base64.b64encode(os.urandom(size)).decode()


def test_the_selfie_body_has_a_type_and_each_limit_is_enforced(boot):
    case_id = waiting_for_selfie()
    with TestClient(app) as client:
        assert post_frames(client, case_id, {"frames": [frame()] * (limits.MAX_FRAMES + 1)}).status_code == 422
        assert post_frames(client, case_id, {"frames": ["A" * (limits.FRAME_MAX_CHARS + 1)]}).status_code == 422
        assert post_frames(client, case_id, {"frames": [123]}).status_code == 422
        assert post_frames(client, case_id, {"frames": "notalist"}).status_code == 422
        assert post_frames(client, case_id, {"frames": [], "offsets_ms": ["soon"]}).status_code == 422
        assert post_frames(client, case_id, {"frames": [], "offsets_ms": list(range(limits.MAX_FRAMES + 1))}).status_code == 422
        assert post_frames(client, case_id, {"frames": [], "challenge": "x" * 1001}).status_code == 422
        assert post_frames(client, case_id, ["not", "an", "object"]).status_code == 422
        assert post_frames(client, case_id, {"frames": ["!!!not base64!!!"]}).status_code == 422
        assert Case  # none of the refusals above moved the case on
    with session_scope() as session:
        assert session.get(Case, case_id).status == "waiting_liveness"


def test_frames_that_are_each_allowed_but_too_large_together_are_refused_with_413(boot):
    case_id = waiting_for_selfie()
    each = "A" * (limits.FRAME_MAX_CHARS - 4)
    count = limits.TOTAL_FRAME_CHARS // len(each) + 2
    assert count <= limits.MAX_FRAMES
    with TestClient(app) as client:
        assert post_frames(client, case_id, {"frames": [each] * count}).status_code == 413


def test_a_request_body_larger_than_the_limit_is_refused_before_it_is_read(boot):
    case_id = waiting_for_selfie()
    with TestClient(app) as client:
        big = b"x" * (limits.MAX_BODY_BYTES + 1024)
        reply = client.post(f"/api/customer/cases/{case_id}/liveness", content=big, headers={**customer_headers(client), "content-type": "application/json"})
        assert reply.status_code == 413 and "too large" in reply.json()["detail"]
        assert client.get("/api/health").status_code == 200  # the service is fine


def test_a_normal_camera_burst_still_goes_through(boot):
    case_id = waiting_for_selfie()
    with TestClient(app) as client:
        reply = post_frames(client, case_id, {"frames": [frame(60_000), frame(60_000)]})
        assert reply.status_code == 200
    with session_scope() as session:
        assert session.get(Case, case_id).status == "waiting_verification"


def test_the_biggest_allowed_frame_is_far_bigger_than_a_real_one(boot):
    assert limits.FRAME_MAX_CHARS >= 4 * 150_000  # a 640x480 JPEG frame is about 60 KB to 150 KB as base64
    assert limits.MAX_BODY_BYTES > limits.TOTAL_FRAME_CHARS


# ------------------------------------------------------------------ no claimed verdicts

def test_the_verify_request_has_no_liveness_field_and_one_sent_anyway_changes_nothing(boot):
    assert "liveness" not in VerifyIn.model_fields
    case_id = waiting_for_selfie()
    with TestClient(app) as client:
        assert post_frames(client, case_id, {"liveness": "pass", "frames": []}).status_code == 200
    with session_scope() as session:
        case = session.get(Case, case_id)
        assert case.status == "waiting_liveness" and case.liveness_strikes == 1  # an empty selfie is a failed one


def test_the_graph_ignores_a_verdict_key_in_the_resume_value(boot):
    case_id = waiting_for_selfie()
    assert resume_case(case_id, {"liveness": "pass"}, "waiting_liveness")
    with session_scope() as session:
        assert session.get(Case, case_id).status == "waiting_liveness"  # still waiting: no frames, no pass
    assert resume_case(case_id, live_selfie(), "waiting_liveness")
    with session_scope() as session:
        assert session.get(Case, case_id).status == "waiting_verification"


def test_username_helper_matches_the_tests_naming(boot):
    assert username_for("cus_nimal") == "nimal.perera"
