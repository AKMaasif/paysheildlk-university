"""A stand-in for the face engine, so the decision logic can be tested with exact numbers.

A "frame" is just JSON bytes describing what the engine would have seen: how many faces, the embedding,
head turn, eye openness, mouth opening and image quality."""
import json
import math
import random

from app.face import Observation

GOOD = {"face_ratio": 0.4, "sharpness": 120.0, "brightness": 130.0, "contrast": 50.0}


def vec(seed: int, scale: float = 0.09) -> list:
    rng = random.Random(seed)
    return [rng.uniform(-scale, scale) for _ in range(128)]


def near(base: list, distance: float, seed: int = 99) -> list:
    """An embedding exactly `distance` away from `base`."""
    rng = random.Random(seed)
    d = [rng.uniform(-1, 1) for _ in range(len(base))]
    norm = math.sqrt(sum(x * x for x in d))
    return [b + x / norm * distance for b, x in zip(base, d)]


def frame(emb, yaw=0.0, ear=0.3, mouth=0.05, faces=1, quality=None, jitter=0.0, tag=0) -> bytes:
    return json.dumps(
        {"emb": emb, "yaw": yaw, "ear": ear, "mouth": mouth, "faces": faces, "q": quality or GOOD, "jitter": jitter, "tag": tag}
    ).encode()


def landmarks(yaw: float, ear: float, mouth: float, jitter: float) -> list:
    p = [(50.0, 50.0)] * 68
    p = list(p)
    p[1], p[15] = (10.0 + jitter, 50.0), (90.0 + jitter, 50.0)
    p[30] = (10.0 + (0.5 + yaw) * 80 + jitter, 60.0)
    for a, x0 in ((36, 25.0), (42, 55.0)):
        gap = ear * 20
        p[a], p[a + 3] = (x0 + jitter, 40.0), (x0 + 20 + jitter, 40.0)
        p[a + 1], p[a + 5] = (x0 + 6 + jitter, 40.0 - gap / 2), (x0 + 6 + jitter, 40.0 + gap / 2)
        p[a + 2], p[a + 4] = (x0 + 14 + jitter, 40.0 - gap / 2), (x0 + 14 + jitter, 40.0 + gap / 2)
    p[60], p[64] = (40.0 + jitter, 80.0), (60.0 + jitter, 80.0)
    p[62], p[66] = (50.0 + jitter, 80.0), (50.0 + jitter, 80.0 + mouth * 20)
    p[48], p[54] = (38.0 + jitter, 80.0), (62.0 + jitter, 80.0)
    return p


class FakeEngine:
    def observe(self, jpeg: bytes, want_embedding: bool) -> Observation:
        d = json.loads(jpeg)
        if d["faces"] != 1:
            return Observation(faces=d["faces"], width=100, height=100)
        return Observation(
            faces=1, width=100, height=100, box=(0, 0, 100, 100), landmarks=landmarks(d["yaw"], d["ear"], d["mouth"], d["jitter"]),
            embedding=d["emb"] if want_embedding else None, quality=d["q"],
        )


def burst(emb, n=10, span_s=4.0, yaws=None, mouths=None, **kw):
    """n frames over span_s seconds. yaws and mouths are per-frame lists. Frames are all different."""
    frames, offsets = [], []
    for i in range(n):
        # A real person's embedding differs a little from frame to frame.
        frames.append(frame(near(emb, 0.03 + 0.01 * i, seed=1000 + i), yaw=(yaws[i] if yaws else 0.0), mouth=(mouths[i] if mouths else 0.05), jitter=0.4 * math.sin(i), tag=i, **kw))
        offsets.append(int(i * span_s * 1000 / (n - 1)))
    return frames, offsets


def turn_left_then_open(emb, n=12):
    """First half: head turns to the person's left (nose to image right). Second half: mouth opens."""
    yaws = [0.0, 0.0, 0.0, 0.10, 0.20, 0.24] + [0.0] * (n - 6)
    mouths = [0.05] * 6 + [0.05, 0.10, 0.35, 0.40, 0.12, 0.05][: n - 6]
    return burst(emb, n=n, yaws=yaws, mouths=mouths)
