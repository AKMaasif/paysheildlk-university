"""Step-up checks must be decided on the server, never by what the client claims."""
from fastapi.testclient import TestClient

from app.agents.runner import begin_case, resume_case
from app.db import session_scope
from app.demo import trigger
from app.main import app
from app.models import Case
from app.runtime import runtime
from app.security import create_token


def _status(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        return case.status, dict(case.pending_prompt or {}), case.knowledge_round


def _held_at_selfie(customer="cus_nimal"):
    case_id = trigger(customer, "sim_swap_transfer")
    begin_case(case_id)
    assert resume_case(case_id, {"decision": "yes"}, "waiting_customer")
    assert _status(case_id)[0] == "waiting_liveness"
    return case_id


def _auth(user_id):
    return {"Authorization": "Bearer " + create_token(runtime.settings, user_id, "customer")}


def test_client_cannot_claim_a_live_selfie(boot):
    case_id = _held_at_selfie()
    with TestClient(app) as client:
        reply = client.post(
            f"/api/customer/cases/{case_id}/liveness",
            json={"liveness": "pass", "frames": []},
            headers=_auth("cus_nimal"),
        )
    assert reply.status_code == 200
    status, prompt, _round = _status(case_id)
    # The claimed "pass" is ignored: no frames counts as a failed selfie and a retake is asked for.
    assert status == "waiting_liveness"
    assert prompt.get("retake") is True


def test_another_customer_cannot_answer_the_selfie(boot):
    case_id = _held_at_selfie()
    with TestClient(app) as client:
        reply = client.post(
            f"/api/customer/cases/{case_id}/liveness",
            json={"frames": []},
            headers=_auth("cus_kumari"),
        )
    assert reply.status_code == 404
    assert _status(case_id)[0] == "waiting_liveness"
    assert "retake" not in _status(case_id)[1]


def test_answering_only_one_question_does_not_pass_the_round(boot):
    case_id = _held_at_selfie()
    assert resume_case(case_id, {"liveness": "pass"}, "waiting_liveness")
    status, prompt, round_no = _status(case_id)
    assert status == "waiting_verification" and round_no == 1
    known = {"school": "Royal College", "city": "Galle"}
    first = prompt["questions"][0]["id"]
    assert len(prompt["questions"]) >= 2
    assert resume_case(case_id, {"answers": {first: known[first]}}, "waiting_verification")
    status, _prompt, round_no = _status(case_id)
    assert status == "waiting_verification"
    assert round_no == 2
