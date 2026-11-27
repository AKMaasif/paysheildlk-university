"""Face verification for registration and for the "Yes, it was me" step.

What it does
  * Enrolment keeps several good frames from a live capture: up to three encrypted images and up to five
    face embeddings (128 numbers each, from a ResNet face model). Nothing here is sent to any AI model.
  * Verification captures a short live burst, checks it is a live person who answered a random challenge,
    checks the image quality, and compares the face with the enrolled templates.
  * The comparison gives a similarity score from 0 to 100, not a yes or no. The score has four tiers, and each
    tier has its own consequence (see `decide`). A low score does not lock a genuine customer out: an unsure
    result gets one more try and then goes to a person.

What it can and cannot promise
  * The face model is a published one (dlib, 99.38% on the LFW benchmark at its default threshold). The
    thresholds here are conservative starting points taken from that threshold, not measurements on this
    bank's customers. `python -m evaluation.face_eval` measures false accepts and false rejects on a labelled
    folder of your own images. Run it before relying on the thresholds.
  * Liveness is active: the customer must follow a random instruction (turn left, turn right, open the mouth)
    inside a short window, with the same face in every frame. That stops a printed photo, a still image on a
    screen and a replayed video. It does not stop a live deepfake or a 3D mask. There is no learned
    presentation-attack model here.
  * Changes in appearance (beard, haircut, a cap, glasses, lighting, expression) move the score down a little.
    Several enrolment templates, a graded score, an extra attempt for unsure results, and refreshing the
    templates after a fully successful verification are how the system copes. None of that is a promise that
    a very different look will be accepted.
"""

import base64
import hashlib
import hmac
import io
import json
import logging
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("payshield.face")

MODEL_VERSION = "dlib-resnet-v1"

# ---- score: a logistic curve over the embedding distance. d = 0.56 scores 50.
CENTRE, WIDTH = 0.56, 0.05
MATCH, LIKELY, UNCERTAIN = 80.0, 60.0, 30.0  # score floors for each tier
REFRESH_SCORE = 90.0  # only a very confident, fully completed verification may refresh a template
MAX_TEMPLATES, MAX_IMAGES, FIXED_TEMPLATES = 6, 3, 3  # the first three embeddings never rotate out

# ---- capture limits
MAX_FRAMES, MIN_FRAMES = 20, 6
MAX_FRAME_BYTES, MAX_TOTAL_BYTES = 450_000, 3_000_000
MIN_SPAN_S, MAX_SPAN_S = 1.5, 20.0
CHALLENGE_TTL_S = 180

# ---- image quality
MIN_FACE_RATIO, MAX_FACE_RATIO = 0.20, 0.80
MIN_SHARPNESS, MIN_BRIGHT, MAX_BRIGHT, MIN_CONTRAST = 35.0, 55.0, 215.0, 22.0
FRONTAL_YAW = 0.18  # |yaw ratio| for an identity frame (a hand-held selfie is rarely exactly square on)
MIN_EAR = 0.15  # eyes at least this open for an identity frame

SCAN_STEPS = ("turn_left", "turn_right")
STEPS = {
    "turn_left": "Turn your head slowly to your left",
    "turn_right": "Turn your head slowly to your right",
    "open_mouth": "Open your mouth wide, then close it",
}
TURN_DELTA, MOUTH_DELTA = 0.14, 0.18


class ChallengeError(ValueError):
    """The challenge token is missing, altered, expired, for another case, or already used."""


class BadCapture(ValueError):
    """The upload is not a usable set of frames."""


def similarity(distance: float) -> float:
    exponent = max(-60.0, min(60.0, (distance - CENTRE) / WIDTH))  # beyond this the score is already 0 or 100, and exp() would overflow
    return round(100.0 / (1.0 + math.exp(exponent)), 1)


def tier(score: float) -> str:
    if score >= MATCH:
        return "match"
    if score >= LIKELY:
        return "likely"
    if score >= UNCERTAIN:
        return "uncertain"
    return "no_match"


# ---------------------------------------------------------------- what the engine sees in one frame

@dataclass
class Observation:
    faces: int = 0
    width: int = 0
    height: int = 0
    box: tuple | None = None  # left, top, right, bottom
    landmarks: list | None = None  # 68 (x, y) points
    embedding: list | None = None
    quality: dict = field(default_factory=dict)

    @property
    def yaw(self) -> float | None:
        p = self.landmarks
        if not p:
            return None
        span = p[15][0] - p[1][0]
        return None if span <= 0 else (p[30][0] - p[1][0]) / span - 0.5

    def _eye(self, a: int) -> float | None:
        p = self.landmarks
        if not p:
            return None
        def d(i: int, j: int) -> float:
            return math.dist(p[a + i], p[a + j])

        horizontal = d(0, 3)
        return None if horizontal == 0 else (d(1, 5) + d(2, 4)) / (2 * horizontal)

    @property
    def ear(self) -> float | None:
        left, right = self._eye(36), self._eye(42)
        return None if left is None or right is None else (left + right) / 2

    @property
    def mouth(self) -> float | None:
        p = self.landmarks
        if not p:
            return None
        width = math.dist(p[60], p[64])
        return None if width == 0 else math.dist(p[62], p[66]) / width


class DlibEngine:
    """dlib's HOG face detector, 68-point landmarks and ResNet face embeddings. Models come from the
    `face_recognition_models` package, which is only used here as a folder of model files."""

    def __init__(self):
        import importlib.util

        import dlib

        spec = importlib.util.find_spec("face_recognition_models")
        if spec is None or not spec.submodule_search_locations:
            raise RuntimeError("face_recognition_models is not installed")
        folder = os.path.join(list(spec.submodule_search_locations)[0], "models")
        self._dlib = dlib
        self._detector = dlib.get_frontal_face_detector()
        self._shape = dlib.shape_predictor(os.path.join(folder, "shape_predictor_68_face_landmarks.dat"))
        self._embed = dlib.face_recognition_model_v1(os.path.join(folder, "dlib_face_recognition_resnet_model_v1.dat"))
        self._lock = threading.Lock()  # dlib objects are used by one thread at a time

    def observe(self, jpeg: bytes, want_embedding: bool) -> Observation:
        import numpy as np
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(jpeg))
            image.load()
        except Exception as exc:
            log.warning("a frame could not be decoded: %r", exc)
            raise BadCapture("a frame is not an image") from exc
        image = image.convert("RGB")
        if max(image.size) > 1280:
            image.thumbnail((1280, 1280))
        array = np.array(image)
        h, w = array.shape[:2]
        with self._lock:
            found = self._detector(array, 0) or self._detector(array, 1)
            if len(found) != 1:
                return Observation(faces=len(found), width=w, height=h)
            rect = found[0]
            shape = self._shape(array, rect)
            points = [(shape.part(i).x, shape.part(i).y) for i in range(68)]
            vector = list(map(float, self._embed.compute_face_descriptor(array, shape, 1))) if want_embedding else None
        box = (rect.left(), rect.top(), rect.right(), rect.bottom())
        return Observation(faces=1, width=w, height=h, box=box, landmarks=points, embedding=vector, quality=_quality(array, box))


def _quality(array, box) -> dict:
    import numpy as np
    from PIL import Image

    h, w = array.shape[:2]
    left, top, right, bottom = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
    crop = array[top:bottom, left:right]
    if crop.size == 0:
        return {"face_ratio": 0.0, "sharpness": 0.0, "brightness": 0.0, "contrast": 0.0}
    gray = np.array(Image.fromarray(crop).convert("L").resize((160, 160)), dtype=float)
    lap = -4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    return {
        "face_ratio": round((right - left) / w, 3),
        "sharpness": round(float(lap.var()), 1),
        "brightness": round(float(gray.mean()), 1),
        "contrast": round(float(gray.std()), 1),
    }


_engine = None
_engine_error: str | None = None
_engine_lock = threading.Lock()


def set_engine(engine) -> None:
    """Tests give the module a stand-in engine."""
    global _engine, _engine_error
    _engine, _engine_error = engine, None


def get_engine():
    global _engine, _engine_error
    with _engine_lock:
        if _engine is None and _engine_error is None:
            try:
                _engine = DlibEngine()
            except Exception as exc:  # missing package, missing model files, no compiler wheel
                _engine_error = str(exc)
                log.error("Face engine unavailable: %s", exc)
        return _engine


def mode() -> str:
    """legacy (the two-frame heuristic, for tests and demos without models), dlib, or unavailable."""
    from app.runtime import runtime

    wanted = (runtime.settings.face_engine if runtime.settings else "dlib").lower()
    if wanted == "legacy":
        return "legacy"
    return "dlib" if get_engine() is not None else "unavailable"


def challenge_required() -> bool:
    from app.runtime import runtime

    return bool(runtime.settings.face_challenge) if runtime.settings else True


# ---------------------------------------------------------------- the random challenge

_used: dict[str, float] = {}
_used_lock = threading.Lock()


def _key() -> bytes:
    from app.runtime import runtime

    return runtime.settings.hmac_key.encode()


def issue_challenge(binding: str, rng: random.Random | None = None) -> dict:
    """The same simple scan every time: look straight, turn to the left, then to the right. It is bound to a case (or
    "register"), signed, short-lived and usable once, so a recording of an earlier scan cannot be replayed. The head
    movements are still measured from the frames; the fixed order only makes it easy for a person to follow."""
    steps = list(SCAN_STEPS) if challenge_required() else []
    body = json.dumps({"s": steps, "b": binding, "t": int(time.time()), "n": os.urandom(6).hex()}, separators=(",", ":"))
    packed = base64.urlsafe_b64encode(body.encode()).decode()
    sig = hmac.new(_key(), packed.encode(), hashlib.sha256).hexdigest()
    return {"token": f"{packed}.{sig}", "steps": steps, "instructions": [STEPS[s] for s in steps], "expires_in": CHALLENGE_TTL_S}


def redeem_challenge(token: str, binding: str) -> list[str]:
    """Check the signature, the binding, the age and that it has not been used. Returns the steps."""
    try:
        packed, sig = (token or "").rsplit(".", 1)
        expected = hmac.new(_key(), packed.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise ChallengeError("The challenge was altered.")
        body = json.loads(base64.urlsafe_b64decode(packed.encode()))
    except ChallengeError:
        raise
    except Exception as exc:
        raise ChallengeError("The challenge is not valid.") from exc
    if body.get("b") != binding:
        raise ChallengeError("The challenge belongs to something else.")
    age = time.time() - int(body.get("t", 0))
    if age > CHALLENGE_TTL_S or age < -5:
        raise ChallengeError("The challenge has expired. A new one is needed.")
    with _used_lock:
        now = time.time()
        for stale in [n for n, at in _used.items() if now - at > CHALLENGE_TTL_S * 2]:
            _used.pop(stale, None)
        if body["n"] in _used:
            raise ChallengeError("That challenge was already used.")
        _used[body["n"]] = now
    return list(body.get("s") or [])


def _satisfied(step: str, obs: list[Observation], times: list[float], lo: float, hi: float) -> bool:
    window = [(o, t) for o, t in zip(obs, times, strict=False) if lo <= t <= hi and o.faces == 1]
    if len(window) < 2:
        return False
    if step in {"turn_left", "turn_right"}:
        base = [o.yaw for o in obs[:3] if o.yaw is not None]
        values = [o.yaw for o, _ in window if o.yaw is not None]
        if not base or not values:
            return False
        centre = sorted(base)[len(base) // 2]
        # A person turning to their own left moves their nose towards the right of the camera's picture.
        reach = max(values) - centre if step == "turn_left" else centre - min(values)
        return reach >= TURN_DELTA
    if step == "open_mouth":
        values = [o.mouth for o, _ in window if o.mouth is not None]
        return bool(values) and max(values) - min(values) >= MOUTH_DELTA
    return False


# ---------------------------------------------------------------- reading a capture

@dataclass
class Capture:
    obs: list
    times: list
    jpegs: list


def _decode(frames: list, offsets_ms: list | None) -> tuple[list[bytes], list[float]]:
    jpegs: list[bytes] = []
    for item in frames or []:
        try:
            raw = item if isinstance(item, (bytes, bytearray)) else base64.b64decode(item, validate=False)
        except Exception as exc:
            raise BadCapture("a frame could not be read") from exc
        if not raw or len(raw) > MAX_FRAME_BYTES:
            raise BadCapture("a frame is empty or too large")
        jpegs.append(bytes(raw))
    if not MIN_FRAMES <= len(jpegs) <= MAX_FRAMES:
        raise BadCapture(f"send between {MIN_FRAMES} and {MAX_FRAMES} frames")
    if sum(len(j) for j in jpegs) > MAX_TOTAL_BYTES:
        raise BadCapture("the capture is too large")
    offsets = [float(o) / 1000.0 for o in (offsets_ms or [])]
    if len(offsets) != len(jpegs) or any(b < a for a, b in zip(offsets, offsets[1:], strict=False)):
        raise BadCapture("frame times are missing or out of order")
    return jpegs, offsets


def _read(frames, offsets_ms, want_embeddings: bool) -> Capture:
    engine = get_engine()
    jpegs, times = _decode(frames, offsets_ms)
    obs = [engine.observe(j, want_embedding=want_embeddings) for j in jpegs]
    return Capture(obs, times, jpegs)


def _issues(o: Observation) -> list[str]:
    if o.faces == 0:
        return ["No face was found. Look straight at the camera."]
    if o.faces > 1:
        return ["More than one face is in view. Only you should be in the picture."]
    q, out = o.quality, []
    if q["face_ratio"] < MIN_FACE_RATIO:
        out.append("Move closer to the camera.")
    if q["face_ratio"] > MAX_FACE_RATIO:
        out.append("Move back a little.")
    if q["sharpness"] < MIN_SHARPNESS:
        out.append("The picture is blurry. Hold the phone steady.")
    if q["brightness"] < MIN_BRIGHT:
        out.append("It is too dark. Face a light.")
    if q["brightness"] > MAX_BRIGHT:
        out.append("It is too bright. Avoid a light behind or glare on your face.")
    if q["contrast"] < MIN_CONTRAST:
        out.append("The lighting is too flat. Face a window or a lamp.")
    return out


def _identity_grade(o: Observation) -> bool:
    return (
        o.faces == 1 and not _issues(o) and o.yaw is not None and abs(o.yaw) <= FRONTAL_YAW
        and (o.ear is None or o.ear >= MIN_EAR) and o.embedding is not None
    )


def _quality_score(o: Observation) -> float:
    q = o.quality
    return min(q["sharpness"], 300) / 3 + min(q["contrast"], 60) + 40 * (1 - min(abs(o.yaw or 0) / 0.3, 1))


def _visible(cap: Capture) -> bool:
    return sum(1 for o in cap.obs if o.faces == 1) >= max(MIN_FRAMES - 1, int(0.7 * len(cap.obs)))


def _visibility_hints(cap: Capture) -> list[str]:
    hints: list[str] = []
    for o in cap.obs:
        hints += _issues(o)
    if any(o.faces == 0 for o in cap.obs):
        hints.append("Take off sunglasses, a mask or anything covering your eyes and nose.")
    return list(dict.fromkeys(hints)) or ["Look straight at the camera."]


def check_liveness(cap: Capture, steps: list[str]) -> dict:
    reasons: list[str] = []
    span = cap.times[-1] - cap.times[0]
    if span < MIN_SPAN_S:
        reasons.append("The capture was too short.")
    if span > MAX_SPAN_S:
        reasons.append("The capture took too long.")
    present = [o for o in cap.obs if o.faces == 1]
    strict = bool(steps)  # with the challenge switched off (a demo setting) a still image is not penalised
    hashes = {hashlib.sha256(j).hexdigest() for j in cap.jpegs}
    if strict and len(hashes) < 0.6 * len(cap.jpegs):
        reasons.append("Many frames are identical, which a still image produces.")
    # The same face from start to end: nobody swapped in during the capture.
    emb = [o.embedding for o in present if o.embedding]
    if len(emb) >= 2 and max(_distance(a, b) for a in emb for b in emb) > 0.58:
        reasons.append("The face changed during the capture.")
    # A live face is never perfectly still.
    if strict and len(present) >= 4 and not _moves(present):
        reasons.append("The face did not move at all.")
    results = {}
    if steps:
        n = len(steps)
        for index, step in enumerate(steps):
            lo = cap.times[0] + span * index / n - 0.4
            hi = cap.times[0] + span * (index + 1) / n + 0.4
            results[step] = _satisfied(step, cap.obs, cap.times, lo, hi)
            if not results[step]:
                reasons.append(f"The instruction was not followed: {STEPS[step].lower()}.")
    return {"live": not reasons, "reasons": reasons, "challenge": results, "frames_with_one_face": len(present), "span_s": round(span, 1)}


def _moves(present: list[Observation]) -> bool:
    spread = 0.0
    for i in (1, 30, 36, 45, 48, 54):
        xs = [(o.landmarks[i][0] - o.box[0]) / max(1, o.box[2] - o.box[0]) for o in present]
        ys = [(o.landmarks[i][1] - o.box[1]) / max(1, o.box[3] - o.box[1]) for o in present]
        spread += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return spread >= 0.02


def _distance(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=False)))


# ---------------------------------------------------------------- templates (what is stored for a customer)

def _pack(images: list[bytes], embeddings: list[list], refreshed: int = 0) -> dict:
    return {
        "version": MODEL_VERSION,
        "images": [base64.b64encode(i).decode() for i in images],
        "embeddings": embeddings,
        "refreshed": refreshed,
        "enrolled_at": int(time.time()),
    }


def load_templates(blob: str | None) -> dict | None:
    """Decrypt a customer's stored face data. An older record holds one image and no embeddings."""
    from app.crypto import decrypt
    from app.runtime import runtime

    if not blob:
        return None
    try:
        plain = decrypt(blob, runtime.settings.encryption_key, "face")
    except Exception:
        # A corrupt or tampered record is the same as no record: the caller falls back to the security questions.
        log.warning("stored face data could not be decrypted")
        return None
    if not plain:
        return None
    try:
        data = json.loads(plain)
        if isinstance(data, dict) and "embeddings" in data:
            return data
    except ValueError:
        pass
    # Older record: one base64 image. Work out its embedding now.
    try:
        image = base64.b64decode(plain)
        engine = get_engine()
        if engine is None:
            return None
        o = engine.observe(image, want_embedding=True)
        if o.faces != 1 or not o.embedding:
            return None
        return _pack([image], [o.embedding])
    except Exception:
        return None


def seal(data: dict) -> str:
    from app.crypto import encrypt
    from app.runtime import runtime

    return encrypt(json.dumps(data, separators=(",", ":")), runtime.settings.encryption_key, "face")


def enroll(frames, offsets_ms, token: str | None) -> dict:
    """Registration. Returns {"ok": True, "sealed": <encrypted blob>, "hash": ...} or {"ok": False, "hints": [...]}."""
    steps = redeem_challenge(token, "register") if challenge_required() else []
    cap = _read(frames, offsets_ms, want_embeddings=True)
    if not _visible(cap):
        return {"ok": False, "reason": "poor_quality", "hints": _visibility_hints(cap), "liveness": {}}
    live = check_liveness(cap, steps)
    if not live["live"]:
        return {"ok": False, "reason": "not_live", "hints": live["reasons"], "liveness": live}
    good = sorted([(o, j) for o, j in zip(cap.obs, cap.jpegs, strict=False) if _identity_grade(o)], key=lambda p: -_quality_score(p[0]))
    if len(good) < 3:
        hints = []
        for o in cap.obs:
            hints += _issues(o)
        hints = list(dict.fromkeys(hints)) or ["Look straight at the camera with your eyes open."]
        return {"ok": False, "reason": "poor_quality", "hints": hints, "liveness": live}
    # Keep the best frames, and avoid keeping near-duplicates.
    kept: list[tuple] = []
    for o, j in good:
        if all(_distance(o.embedding, k[0].embedding) > 0.02 for k in kept):
            kept.append((o, j))
        if len(kept) == MAX_TEMPLATES - 1:
            break
    images = [_shrink(j) for _o, j in kept[:MAX_IMAGES]]
    data = _pack(images, [o.embedding for o, _j in kept])
    return {"ok": True, "sealed": seal(data), "hash": hashlib.sha256(b"".join(images)).hexdigest(), "templates": len(kept), "liveness": live}


def _shrink(jpeg: bytes) -> bytes:
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    except Exception:  # a stand-in engine in tests sends descriptions, not pictures
        return jpeg
    image.thumbnail((480, 480))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85)
    return out.getvalue()


# ---------------------------------------------------------------- verification

@dataclass
class Result:
    verdict: str  # match | likely | uncertain | no_match | fake | poor_quality | no_template | unavailable
    score: float | None = None
    distance: float | None = None
    hints: list = field(default_factory=list)
    liveness: dict = field(default_factory=dict)
    frames_used: int = 0
    embedding: list | None = None  # kept only so a fully successful case may refresh the templates

    def public(self) -> dict:
        return {
            "verdict": self.verdict, "score": self.score, "distance": self.distance, "hints": self.hints,
            "frames_used": self.frames_used, "liveness_ok": self.liveness.get("live"),
        }


def verify(sealed_templates: str | None, frames, offsets_ms, token: str | None, binding: str) -> Result:
    if get_engine() is None:
        return Result("unavailable", hints=["Face verification is not available right now."])
    templates = load_templates(sealed_templates)
    if not templates or not templates.get("embeddings"):
        return Result("no_template", hints=["There is no usable face on file for this account."])
    steps = redeem_challenge(token, binding) if challenge_required() else []
    cap = _read(frames, offsets_ms, want_embeddings=True)
    if not _visible(cap):
        return Result("poor_quality", hints=_visibility_hints(cap))
    live = check_liveness(cap, steps)
    if not live["live"]:
        return Result("fake", hints=live["reasons"], liveness=live)
    good = sorted([o for o in cap.obs if _identity_grade(o)], key=lambda o: -_quality_score(o))[:4]
    if len(good) < 2:
        hints = []
        for o in cap.obs:
            hints += _issues(o)
        hints = list(dict.fromkeys(hints)) or ["Look straight at the camera with your eyes open."]
        return Result("poor_quality", hints=hints, liveness=live, frames_used=len(good))
    per_frame = [min(_distance(o.embedding, t) for t in templates["embeddings"]) for o in good]
    distance = sorted(per_frame)[len(per_frame) // 2]  # the median frame, so one odd frame cannot decide it
    score = similarity(distance)
    best = good[per_frame.index(min(per_frame))]
    return Result(tier(score), score=score, distance=round(distance, 3), liveness=live, frames_used=len(good), embedding=best.embedding)


def refresh(sealed_templates: str, embedding: list, jpeg: bytes | None = None) -> str | None:
    """Add a fresh embedding after a fully successful, very confident verification. The first three never rotate out."""
    data = load_templates(sealed_templates)
    if not data or not embedding:
        return None
    embeddings = list(data["embeddings"])
    if any(_distance(embedding, e) < 0.02 for e in embeddings):
        return None
    embeddings.append(embedding)
    if len(embeddings) > MAX_TEMPLATES:
        del embeddings[FIXED_TEMPLATES]  # the oldest refreshed one
    data["embeddings"] = embeddings
    data["refreshed"] = int(data.get("refreshed", 0)) + 1
    return seal(data)


# ---------------------------------------------------------------- what the verification agent does with a result

def decide(result: Result, state: dict) -> dict:
    """Turns a verification result into the next step. `state` carries the attempts so far.

    match      -> go on to the security questions
    likely     -> go on, but with only two question rounds instead of four
    uncertain  -> one more try; after that a person at the bank reviews it, and the money stays held
    poor_quality -> try again with guidance, three times, without counting against the customer; then a person
    no_match / fake -> a strike; the second strike blocks the payment
    unavailable / no_template -> a person reviews it (never an automatic pass)
    """
    strikes = int(state.get("liveness_strikes") or 0)
    unsure = int(state.get("face_unsure") or 0)
    retakes = int(state.get("quality_retakes") or 0)
    v = result.verdict
    if v in {"match", "likely"}:
        return {"next": "questions", "max_rounds": 4 if v == "match" else 2}
    if v == "poor_quality":
        retakes += 1
        return {"next": "retake" if retakes <= 3 else "review", "quality_retakes": retakes}
    if v == "uncertain":
        unsure += 1
        return {"next": "retake" if unsure < 2 else "review", "face_unsure": unsure}
    if v in {"no_match", "fake"}:
        strikes += 1
        return {"next": "retake" if strikes < 2 else "block", "liveness_strikes": strikes}
    return {"next": "review"}
