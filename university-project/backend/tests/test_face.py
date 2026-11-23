"""Face verification: scoring, quality, liveness challenge, decisions and how the case reacts."""
import json
import time

import pytest
from face_fakes import FakeEngine, burst, frame, near, turn_left_then_open, vec
from fastapi.testclient import TestClient
from helpers import PASSWORD, correct_answers, record

from app import face
from app.agents.runner import begin_case, resume_case
from app.db import session_scope
from app.demo import trigger
from app.main import app
from app.models import Case, Transaction, User
from app.runtime import runtime

ENROLLED = vec(1)


@pytest.fixture(autouse=True)
def _engine(boot, monkeypatch):
    monkeypatch.setenv("FACE_ENGINE", "dlib")  # the app re-reads the environment when a TestClient starts it
    face.set_engine(FakeEngine())
    runtime.settings.face_engine = "dlib"
    runtime.settings.face_challenge = True
    face._used.clear()
    yield
    face.set_engine(None)


def _enrol_sealed(challenge=False):
    runtime.settings.face_challenge = challenge
    frames, offsets = burst(ENROLLED, n=8)
    res = face.enroll(frames, offsets, None)
    runtime.settings.face_challenge = True
    assert res["ok"], res
    return res["sealed"]


def _verify(sealed, emb, token=None, binding="case-1", challenge=False, **kw):
    runtime.settings.face_challenge = challenge
    try:
        frames, offsets = burst(emb, n=8, **kw)
        return face.verify(sealed, frames, offsets, token, binding)
    finally:
        runtime.settings.face_challenge = True


# ---------------------------------------------------------------- the score

def test_score_falls_as_distance_grows_and_tiers_follow():
    scores = [face.similarity(d) for d in (0.2, 0.4, 0.5, 0.56, 0.62, 0.7, 0.9)]
    assert scores == sorted(scores, reverse=True)
    assert face.similarity(0.56) == 50.0
    assert [face.tier(face.similarity(d)) for d in (0.3, 0.52, 0.58, 0.8)] == ["match", "likely", "uncertain", "no_match"]


def test_verdicts_for_genuine_unsure_and_impostor_faces():
    sealed = _enrol_sealed()
    assert _verify(sealed, near(ENROLLED, 0.30)).verdict == "match"
    assert _verify(sealed, near(ENROLLED, 0.52)).verdict == "likely"
    assert _verify(sealed, near(ENROLLED, 0.58)).verdict == "uncertain"
    result = _verify(sealed, near(ENROLLED, 0.85))
    assert result.verdict == "no_match" and result.score < 5


def test_the_median_frame_decides_so_one_odd_frame_cannot():
    sealed = _enrol_sealed()
    runtime.settings.face_challenge = False
    frames, offsets = burst(ENROLLED, n=8)
    frames[2] = frame(vec(500), tag=2)  # one frame of someone else
    res = face.verify(sealed, frames, offsets, None, "c")
    runtime.settings.face_challenge = True
    # Someone else in one frame is also a changed face during the capture, so it is not accepted as live.
    assert res.verdict == "fake"


def test_any_enrolled_template_may_match():
    runtime.settings.face_challenge = False
    other = near(ENROLLED, 0.45, seed=5)
    f1, o1 = burst(ENROLLED, n=4)
    f2, o2 = burst(other, n=4)
    frames = f1 + f2
    offsets = o1 + [o + 5000 for o in o2]
    res = face.enroll(frames, offsets, None)
    runtime.settings.face_challenge = True
    assert res["ok"]
    assert len(face.load_templates(res["sealed"])["embeddings"]) >= 2
    assert _verify(res["sealed"], near(other, 0.1, seed=6)).verdict == "match"


# ---------------------------------------------------------------- image quality

@pytest.mark.parametrize(
    "kw,hint",
    [
        ({"quality": {"face_ratio": 0.4, "sharpness": 120.0, "brightness": 30.0, "contrast": 50.0}}, "too dark"),
        ({"quality": {"face_ratio": 0.4, "sharpness": 120.0, "brightness": 240.0, "contrast": 50.0}}, "too bright"),
        ({"quality": {"face_ratio": 0.4, "sharpness": 5.0, "brightness": 130.0, "contrast": 50.0}}, "blurry"),
        ({"quality": {"face_ratio": 0.1, "sharpness": 120.0, "brightness": 130.0, "contrast": 50.0}}, "closer"),
        ({"quality": {"face_ratio": 0.4, "sharpness": 120.0, "brightness": 130.0, "contrast": 8.0}}, "flat"),
        ({"faces": 0}, "No face"),
        ({"faces": 2}, "More than one"),
    ],
)
def test_poor_images_ask_for_a_better_one_and_are_never_called_fake(kw, hint):
    sealed = _enrol_sealed()
    result = _verify(sealed, ENROLLED, **kw)
    assert result.verdict == "poor_quality"
    assert any(hint.lower() in h.lower() for h in result.hints), result.hints


def test_a_turned_head_or_closed_eyes_is_not_used_to_identify():
    sealed = _enrol_sealed()
    runtime.settings.face_challenge = False
    frames, offsets = burst(ENROLLED, n=8)
    frames = [frame(ENROLLED, yaw=0.30, tag=i, jitter=0.3 * i) for i in range(8)]
    res = face.verify(sealed, frames, offsets, None, "c")
    runtime.settings.face_challenge = True
    assert res.verdict == "poor_quality"
    frames = [frame(ENROLLED, ear=0.05, tag=i, jitter=0.3 * i) for i in range(8)]
    runtime.settings.face_challenge = False
    assert face.verify(sealed, frames, offsets, None, "c").verdict == "poor_quality"
    runtime.settings.face_challenge = True


# ---------------------------------------------------------------- liveness

def _challenge(binding="case-1"):
    return face.issue_challenge(binding)


def _forced(steps):
    """A token for chosen steps, so a test knows what the burst must do."""
    import base64
    import hashlib
    import hmac

    body = json.dumps({"s": steps, "b": "case-1", "t": int(time.time()), "n": f"n{time.time_ns()}"}, separators=(",", ":"))
    packed = base64.urlsafe_b64encode(body.encode()).decode()
    return f"{packed}." + hmac.new(face._key(), packed.encode(), hashlib.sha256).hexdigest()


def test_following_the_random_instructions_passes():
    sealed = _enrol_sealed()
    frames, offsets = turn_left_then_open(ENROLLED)
    res = face.verify(sealed, frames, offsets, _forced(["turn_left", "open_mouth"]), "case-1")
    assert res.verdict == "match" and res.liveness["live"]


def test_not_following_them_is_rejected():
    sealed = _enrol_sealed()
    frames, offsets = turn_left_then_open(ENROLLED)
    # Asked to turn right and then to open the mouth, but the person turned left.
    res = face.verify(sealed, frames, offsets, _forced(["turn_right", "open_mouth"]), "case-1")
    assert res.verdict == "fake" and any("turn your head slowly to your right" in r.lower() for r in res.hints)
    # A person who just sits still.
    frames, offsets = burst(ENROLLED, n=10)
    res = face.verify(sealed, frames, offsets, _forced(["turn_left", "turn_right"]), "case-1")
    assert res.verdict == "fake"


def test_a_still_image_is_rejected_even_with_no_instruction_to_follow():
    sealed = _enrol_sealed()
    still = frame(ENROLLED)
    frames = [still] * 8
    offsets = [i * 500 for i in range(8)]
    runtime.settings.face_challenge = True
    res = face.verify(sealed, frames, offsets, _forced(["turn_left", "open_mouth"]), "case-1")
    assert res.verdict == "fake"
    assert any("identical" in r.lower() or "did not move" in r.lower() for r in res.hints)


def test_a_different_face_appearing_part_way_through_is_rejected():
    sealed = _enrol_sealed()
    frames, offsets = turn_left_then_open(ENROLLED)
    stranger = vec(77)
    frames = frames[:6] + [frame(stranger, yaw=0.0, mouth=m, jitter=0.3 * i, tag=100 + i) for i, m in enumerate([0.05, 0.1, 0.35, 0.4, 0.12, 0.05])]
    res = face.verify(sealed, frames, offsets, _forced(["turn_left", "open_mouth"]), "case-1")
    assert res.verdict == "fake" and any("changed" in r.lower() for r in res.hints)


def test_too_short_or_too_few_frames_are_refused():
    sealed = _enrol_sealed()
    frames, offsets = burst(ENROLLED, n=8, span_s=0.5)
    assert face.verify(sealed, frames, offsets, _forced(["turn_left", "open_mouth"]), "case-1").verdict == "fake"
    with pytest.raises(face.BadCapture):
        face.verify(sealed, frames[:3], offsets[:3], _forced(["turn_left", "open_mouth"]), "case-1")


def test_challenge_tokens_are_signed_bound_short_lived_and_single_use(monkeypatch):
    ch = _challenge("case-1")
    assert len(ch["steps"]) == 2 and ch["steps"][0] != ch["steps"][1] and ch["instructions"]
    assert face.redeem_challenge(ch["token"], "case-1") == ch["steps"]
    with pytest.raises(face.ChallengeError, match="already used"):
        face.redeem_challenge(ch["token"], "case-1")
    with pytest.raises(face.ChallengeError, match="belongs"):
        face.redeem_challenge(_challenge("case-1")["token"], "case-2")
    packed, sig = _challenge("case-1")["token"].rsplit(".", 1)
    with pytest.raises(face.ChallengeError, match="altered"):
        face.redeem_challenge(packed + "." + "0" * len(sig), "case-1")
    old = _challenge("case-1")["token"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + face.CHALLENGE_TTL_S + 30)
    with pytest.raises(face.ChallengeError, match="expired"):
        face.redeem_challenge(old, "case-1")


# ---------------------------------------------------------------- what to do with a result

@pytest.mark.parametrize(
    "verdict,state,expect",
    [
        ("match", {}, {"next": "questions", "max_rounds": 4}),
        ("likely", {}, {"next": "questions", "max_rounds": 2}),
        ("uncertain", {}, {"next": "retake"}),
        ("uncertain", {"face_unsure": 1}, {"next": "review"}),
        ("poor_quality", {}, {"next": "retake"}),
        ("poor_quality", {"quality_retakes": 3}, {"next": "review"}),
        ("no_match", {}, {"next": "retake"}),
        ("no_match", {"liveness_strikes": 1}, {"next": "block"}),
        ("fake", {"liveness_strikes": 1}, {"next": "block"}),
        ("unavailable", {}, {"next": "review"}),
        ("no_template", {}, {"next": "review"}),
    ],
)
def test_each_result_has_its_own_consequence(verdict, state, expect):
    got = face.decide(face.Result(verdict), state)
    assert {k: got[k] for k in expect} == expect


def test_poor_quality_never_counts_as_a_strike():
    assert "liveness_strikes" not in face.decide(face.Result("poor_quality"), {})


# ---------------------------------------------------------------- templates

def test_templates_are_stored_encrypted_and_hold_several_faces():
    sealed = _enrol_sealed()
    assert sealed.startswith("gcm2$")
    data = face.load_templates(sealed)
    assert data["version"] == face.MODEL_VERSION and 3 <= len(data["embeddings"]) <= face.MAX_TEMPLATES and 1 <= len(data["images"]) <= face.MAX_IMAGES


def test_refresh_adds_a_template_but_never_replaces_the_first_three():
    sealed = _enrol_sealed()
    first = face.load_templates(sealed)["embeddings"][: face.FIXED_TEMPLATES]
    for i in range(10):
        sealed = face.refresh(sealed, near(ENROLLED, 0.2 + 0.01 * i, seed=200 + i)) or sealed
    data = face.load_templates(sealed)
    assert len(data["embeddings"]) <= face.MAX_TEMPLATES
    assert data["embeddings"][: face.FIXED_TEMPLATES] == first and data["refreshed"] >= 1
    assert face.refresh(sealed, data["embeddings"][-1]) is None  # a near-duplicate adds nothing


# ---------------------------------------------------------------- the case

def _customer_with_face(customer_id="cus_nimal"):
    sealed = _enrol_sealed()
    with session_scope() as session:
        session.get(User, customer_id).face_ref = sealed
    return sealed


def _to_selfie(customer_id="cus_nimal"):
    case_id = trigger(customer_id, "sim_swap_transfer")
    begin_case(case_id)
    assert resume_case(case_id, {"decision": "yes"}, "waiting_customer")
    return case_id


def _prompt(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        return case.status, dict(case.pending_prompt or {})


def _submit(case_id, emb, **kw):
    status, prompt = _prompt(case_id)
    assert status == "waiting_liveness", status
    token = prompt["face"]["token"]
    steps = face.redeem_challenge(token, case_id)  # test helper: learn the steps, then use a fresh token
    runtime_token = _forced_for(case_id, steps)
    frames, offsets = _act(emb, steps, **kw)
    return resume_case(case_id, {"frames": frames, "offsets_ms": offsets, "challenge": runtime_token}, "waiting_liveness")


def _forced_for(case_id, steps):
    import base64
    import hashlib
    import hmac

    body = json.dumps({"s": steps, "b": case_id, "t": int(time.time()), "n": f"x{time.time_ns()}"}, separators=(",", ":"))
    packed = base64.urlsafe_b64encode(body.encode()).decode()
    return f"{packed}." + hmac.new(face._key(), packed.encode(), hashlib.sha256).hexdigest()


def _act(emb, steps, n=12, **kw):
    """A burst in which the person does what the steps ask, in order."""
    yaws, mouths = [0.0] * n, [0.05] * n
    half = n // 2
    for idx, step in enumerate(steps):
        lo, hi = (0, half) if idx == 0 else (half, n)
        mid = (lo + hi) // 2
        if step == "turn_left":
            for i in range(mid - 1, hi - 1):
                yaws[i] = 0.24
        if step == "turn_right":
            for i in range(mid - 1, hi - 1):
                yaws[i] = -0.24
        if step == "open_mouth":
            for i in range(mid, min(hi, mid + 2)):
                mouths[i] = 0.4
    return burst(emb, n=n, yaws=yaws, mouths=mouths, **kw)


def _status(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        tx = session.get(Transaction, case.transaction_id)
        return case.status, tx.status, case.face_verdict, case.face_score, case.pending_prompt


def test_the_selfie_step_carries_a_signed_challenge():
    _customer_with_face()
    case_id = _to_selfie()
    status, prompt = _prompt(case_id)
    assert status == "waiting_liveness" and len(prompt["face"]["steps"]) == 2 and prompt["face"]["token"]


def test_a_matching_face_goes_on_to_the_questions_and_the_questions_release_the_money():
    _customer_with_face()
    case_id = _to_selfie()
    assert _submit(case_id, near(ENROLLED, 0.25))
    status, tx, verdict, score, prompt = _status(case_id)
    assert status == "waiting_verification" and verdict == "match" and score >= 80
    assert resume_case(case_id, {"answers": correct_answers(case_id)}, "waiting_verification")
    assert _status(case_id)[:2] == ("closed_released", "released")


def test_a_matching_face_alone_never_releases_the_money():
    _customer_with_face()
    case_id = _to_selfie()
    _submit(case_id, near(ENROLLED, 0.2))
    assert _status(case_id)[1] == "held"


def test_a_very_confident_match_that_also_passes_the_questions_refreshes_the_templates():
    sealed = _customer_with_face()
    before = len(face.load_templates(sealed)["embeddings"])
    case_id = _to_selfie()
    _submit(case_id, near(ENROLLED, 0.18, seed=300))
    resume_case(case_id, {"answers": correct_answers(case_id)}, "waiting_verification")
    with session_scope() as session:
        after = len(face.load_templates(session.get(User, "cus_nimal").face_ref)["embeddings"])
    assert after == before + 1


def test_a_likely_match_gets_only_two_question_rounds_and_does_not_refresh():
    sealed = _customer_with_face()
    before = len(face.load_templates(sealed)["embeddings"])
    case_id = _to_selfie()
    _submit(case_id, near(ENROLLED, 0.52))
    assert _status(case_id)[2] == "likely"
    for _ in range(2):
        resume_case(case_id, {"answers": {"school": "wrong", "city": "wrong"}}, "waiting_verification")
    assert _status(case_id)[:2] == ("closed_blocked", "blocked")
    with session_scope() as session:
        assert len(face.load_templates(session.get(User, "cus_nimal").face_ref)["embeddings"]) == before


def test_a_different_person_twice_is_blocked():
    _customer_with_face()
    case_id = _to_selfie()
    _submit(case_id, vec(900))
    assert _status(case_id)[0] == "waiting_liveness"
    _submit(case_id, vec(901))
    assert _status(case_id)[:2] == ("closed_blocked", "blocked")


def test_an_unsure_face_gets_one_more_try_and_then_a_person_not_a_block():
    _customer_with_face()
    case_id = _to_selfie()
    _submit(case_id, near(ENROLLED, 0.58))
    assert _status(case_id)[0] == "waiting_liveness"
    _submit(case_id, near(ENROLLED, 0.59, seed=3))
    status, tx, verdict, *_ = _status(case_id)
    assert status == "escalated" and tx == "held"


def test_poor_pictures_get_guidance_and_three_free_retakes_then_a_person():
    _customer_with_face()
    case_id = _to_selfie()
    dark = {"face_ratio": 0.4, "sharpness": 120.0, "brightness": 20.0, "contrast": 50.0}
    for _ in range(3):
        _submit(case_id, ENROLLED, quality=dark)
        status, _tx, verdict, _s, prompt = _status(case_id)
        assert status == "waiting_liveness" and verdict == "poor_quality" and prompt["retake"] and any("dark" in h for h in prompt["hints"])
    with session_scope() as session:
        assert session.get(Case, case_id).liveness_strikes in (0, None)
    _submit(case_id, ENROLLED, quality=dark)
    assert _status(case_id)[:2] == ("escalated", "held")


def test_a_still_image_or_an_expired_challenge_does_not_pass():
    _customer_with_face()
    case_id = _to_selfie()
    status, prompt = _prompt(case_id)
    still = frame(ENROLLED)
    assert resume_case(case_id, {"frames": [still] * 8, "offsets_ms": [i * 500 for i in range(8)], "challenge": prompt["face"]["token"]}, "waiting_liveness")
    assert _status(case_id)[2] == "fake"
    # The same challenge cannot be used a second time.
    status, prompt2 = _prompt(case_id)
    assert prompt2["face"]["token"] != prompt["face"]["token"]


def test_the_client_cannot_claim_a_pass():
    _customer_with_face()
    case_id = _to_selfie()
    resume_case(case_id, {"frames": [], "offsets_ms": []}, "waiting_liveness")
    assert _status(case_id)[0] == "waiting_liveness" and _status(case_id)[2] == "poor_quality"


def test_with_no_face_on_file_or_no_engine_a_person_decides():
    case_id = _to_selfie("cus_kumari")  # no face stored for this customer
    _submit(case_id, ENROLLED)
    assert _status(case_id)[:2] == ("escalated", "held")


def test_if_the_face_engine_is_unavailable_nothing_is_released(monkeypatch):
    _customer_with_face()
    case_id = _to_selfie()
    monkeypatch.setattr(face, "get_engine", lambda: None)
    status, prompt = _prompt(case_id)
    resume_case(case_id, {"frames": [b"x"] * 8, "offsets_ms": list(range(8)), "challenge": ""}, "waiting_liveness")
    assert _status(case_id)[:2] == ("escalated", "held")


# ---------------------------------------------------------------- registration

def _registration(emb, token, **kw):
    r = record("cus_nimal")
    frames, offsets = _act(emb, face.redeem_challenge(token, "register"), n=12)
    return {
        "full_name": r["name"], "email": r["email"], "nic": r["nic"], "phone": r["phone"], "account_number": r["account"],
        "password": PASSWORD, "confirm_password": PASSWORD,
        "frames": [__import__("base64").b64encode(f).decode() for f in frames], "offsets_ms": offsets, **kw,
    }


def _mint(client):
    token = client.get("/api/customer/register/challenge").json()["token"]
    steps = face.redeem_challenge(token, "register")  # learn the steps; the token is now spent, so mint a matching fresh one
    return steps, _forced_for("register", steps)


def test_registration_stores_the_scan_encrypted_and_never_returns_it():
    with TestClient(app) as client:
        info = client.get("/api/customer/register/challenge").json()
        assert info["mode"] == "dlib" and len(info["steps"]) == 2
        steps, token = _mint(client)
        frames, offsets = _act(ENROLLED, steps, n=12)
        r = record("cus_nimal")
        body = {
            "full_name": r["name"], "email": r["email"], "nic": r["nic"], "phone": r["phone"], "account_number": r["account"],
            "password": PASSWORD, "confirm_password": PASSWORD, "challenge": token, "offsets_ms": offsets,
            "frames": [__import__("base64").b64encode(f).decode() for f in frames],
        }
        done = client.post("/api/customer/register", json=body)
        assert done.status_code == 200, done.text
        from helpers import authenticate

        headers = {"Authorization": "Bearer " + authenticate(client, done.json())["token"]}
        account = client.get("/api/customer/account", headers=headers).text
        assert "embedding" not in account and "images" not in account
        again = client.post("/api/customer/register", json=body)
        assert again.status_code in {409, 422}
    with session_scope() as session:
        stored = session.get(User, "cus_nimal").face_ref
    assert stored.startswith("gcm2$") and "{" not in stored
    assert len(face.load_templates(stored)["embeddings"]) >= 3


def test_registration_refuses_a_missing_reused_or_foreign_challenge_and_a_poor_scan():
    with TestClient(app) as client:
        steps, token = _mint(client)
        frames, offsets = _act(ENROLLED, steps, n=12)
        r = record("cus_nimal")
        b64 = lambda fs: [__import__("base64").b64encode(f).decode() for f in fs]
        base = {"full_name": r["name"], "email": r["email"], "nic": r["nic"], "phone": r["phone"], "account_number": r["account"], "password": PASSWORD, "confirm_password": PASSWORD, "offsets_ms": offsets, "frames": b64(frames)}
        assert client.post("/api/customer/register", json={**base, "challenge": ""}).status_code == 422
        assert client.post("/api/customer/register", json={**base, "challenge": _forced_for("case-9", steps)}).status_code == 422
        dark = {"face_ratio": 0.4, "sharpness": 120.0, "brightness": 20.0, "contrast": 50.0}
        poor, off2 = _act(ENROLLED, steps, n=12, quality=dark)
        res = client.post("/api/customer/register", json={**base, "frames": b64(poor), "offsets_ms": off2, "challenge": _forced_for("register", steps)})
        assert res.status_code == 422 and "dark" in res.json()["detail"].lower()
        with session_scope() as session:
            assert not session.get(User, "cus_nimal").registered


def test_registration_is_unavailable_rather_than_unchecked_when_the_engine_is_missing(monkeypatch):
    monkeypatch.setattr(face, "get_engine", lambda: None)
    with TestClient(app) as client:
        assert client.get("/api/customer/register/challenge").status_code == 503


def test_frames_sent_to_the_endpoint_reach_the_face_check_unaltered():
    """The whole path a phone takes: base64 in the request, bytes at the engine."""
    import base64

    _customer_with_face()
    case_id = _to_selfie()
    status, prompt = _prompt(case_id)
    steps = prompt["face"]["steps"]
    frames, offsets = _act(near(ENROLLED, 0.25), steps, n=12)
    from app.security import create_token

    token = _forced_for(case_id, steps)
    with TestClient(app) as client:
        reply = client.post(
            f"/api/customer/cases/{case_id}/liveness",
            headers={"Authorization": "Bearer " + create_token(runtime.settings, "cus_nimal", "customer")},
            json={"frames": [base64.b64encode(f).decode() for f in frames], "offsets_ms": offsets, "challenge": token},
        )
        assert reply.status_code == 200, reply.text
        assert _status(case_id)[2] == "match"
        bad = client.post(
            f"/api/customer/cases/{case_id}/liveness",
            headers={"Authorization": "Bearer " + create_token(runtime.settings, "cus_nimal", "customer")},
            json={"frames": ["not base64 !!"], "offsets_ms": [0], "challenge": ""},
        )
        assert bad.status_code == 422
