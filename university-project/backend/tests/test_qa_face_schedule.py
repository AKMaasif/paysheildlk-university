"""The browser's face-scan schedule against the server's liveness check.

The page gives a countdown, a straight-ahead moment, then slow moves of five seconds each. These tests build the frames a
person following that schedule would produce (a human takes a moment to react and turns gradually) and check the server
accepts them for every pair of moves. The constants below must match frontend/components/FaceCapture.tsx."""
import itertools

import pytest
from face_fakes import FakeEngine, frame, near, vec

from app import face
from app.runtime import runtime

ENROLLED = vec(1)
NEW = dict(lead=2.5, step=5.0, first=0.7)  # seconds; the page now
OLD = dict(lead=0.7, step=2.4, first=1.0)  # the page before: two quick moves, no coming back
FRAMES = 20


@pytest.fixture(autouse=True)
def _engine(boot, monkeypatch):
    monkeypatch.setenv("FACE_ENGINE", "dlib")
    face.set_engine(FakeEngine())
    runtime.settings.face_engine = "dlib"
    runtime.settings.face_challenge = True
    face._used.clear()
    yield
    face.set_engine(None)


def person(steps, plan, reaction=0.7, ramp=1.2, peak=0.20, open_to=0.40):
    """The frames and times of someone who follows `plan`: they react after `reaction` seconds and take `ramp` seconds
    to reach full turn, hold it for the first part of the move, then return."""
    total = plan["lead"] + plan["step"] * len(steps)
    frames, offsets = [], []
    for i in range(FRAMES):
        t = i * total / (FRAMES - 1)
        yaw, mouth = 0.0, 0.05
        for index, step in enumerate(steps):
            start = plan["lead"] + plan["step"] * index
            move_end = start + plan["step"] * plan["first"]
            end = start + plan["step"]
            moving = start + reaction  # when this person actually starts
            if step in ("turn_left", "turn_right"):
                sign = 1 if step == "turn_left" else -1
                reached = peak * min(1.0, max(0.0, (move_end - moving) / ramp))  # how far they had got when told to return
                if moving <= t < move_end:
                    yaw = sign * peak * min(1.0, (t - moving) / ramp)
                elif move_end <= t < end:
                    yaw = sign * reached * max(0.0, 1 - (t - move_end) / 1.0)  # coming back to the middle, from where they are
            else:
                if moving <= t < move_end:
                    mouth = open_to
                elif move_end <= t < end:
                    mouth = 0.05
        frames.append(frame(near(ENROLLED, 0.03 + 0.004 * i, seed=1000 + i), yaw=yaw, mouth=mouth, jitter=0.4 * (i % 3 - 1), tag=i))
        offsets.append(int(t * 1000))
    return frames, offsets


def sealed():
    runtime.settings.face_challenge = False
    from face_fakes import burst

    frames, offsets = burst(ENROLLED, n=8)
    result = face.enroll(frames, offsets, None)
    runtime.settings.face_challenge = True
    return result["sealed"]


def forced(steps):
    import base64
    import hashlib
    import hmac
    import json
    import time

    body = json.dumps({"s": steps, "b": "case-1", "t": int(time.time()), "n": f"n{time.time_ns()}"}, separators=(",", ":"))
    packed = base64.urlsafe_b64encode(body.encode()).decode()
    return f"{packed}." + hmac.new(face._key(), packed.encode(), hashlib.sha256).hexdigest()


PAIRS = list(itertools.permutations(["turn_left", "turn_right", "open_mouth"], 2))


@pytest.mark.parametrize("steps", PAIRS, ids=lambda s: "+".join(s))
def test_a_person_following_the_new_schedule_passes_every_pair_of_moves(steps):
    frames, offsets = person(list(steps), NEW)
    result = face.verify(sealed(), frames, offsets, forced(list(steps)), "case-1")
    assert result.liveness["live"], result.liveness
    assert all(result.liveness["challenge"].values()) and result.verdict == "match"


@pytest.mark.parametrize("steps", PAIRS, ids=lambda s: "+".join(s))
def test_a_slow_person_who_reacts_after_a_second_and_turns_only_a_little_still_passes(steps):
    frames, offsets = person(list(steps), NEW, reaction=1.0, ramp=1.6, peak=0.17, open_to=0.30)
    result = face.verify(sealed(), frames, offsets, forced(list(steps)), "case-1")
    assert result.liveness["live"], result.liveness


def test_the_same_slow_person_could_not_keep_up_with_the_old_schedule():
    # This is what you saw: 2.4 seconds a move, no warm-up. Someone who needs a moment to react misses a turn.
    misses = 0
    for steps in PAIRS:
        frames, offsets = person(list(steps), OLD, reaction=1.0, ramp=1.6, peak=0.17, open_to=0.30)
        misses += not face.verify(sealed(), frames, offsets, forced(list(steps)), "case-1").liveness["live"]
    assert misses >= 3, "the old pace should have failed for a slow person on several pairs"


def test_sitting_still_or_doing_the_wrong_move_still_fails_on_the_new_schedule():
    still = person(["turn_left", "turn_right"], NEW, peak=0.0, open_to=0.05)
    assert not face.verify(sealed(), *still, forced(["turn_left", "turn_right"]), "case-1").liveness["live"]
    wrong = person(["turn_left", "turn_right"], NEW)  # asked for the other pair
    result = face.verify(sealed(), *wrong, forced(["turn_right", "open_mouth"]), "case-1")
    assert not result.liveness["live"] and not all(result.liveness["challenge"].values())
    same_direction_twice = person(["turn_left", "turn_left"], NEW)
    assert not face.verify(sealed(), *same_direction_twice, forced(["turn_right", "open_mouth"]), "case-1").liveness["live"]


def test_the_new_scan_is_well_inside_the_servers_time_and_frame_limits():
    total = NEW["lead"] + NEW["step"] * 2
    assert face.MIN_SPAN_S < total < face.MAX_SPAN_S and FRAMES <= face.MAX_FRAMES and FRAMES >= face.MIN_FRAMES
