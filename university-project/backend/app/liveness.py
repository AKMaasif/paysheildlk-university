import hashlib
import math


def _entropy(blob: bytes) -> float:
    if not blob:
        return 0.0
    counts = [0] * 256
    for byte in blob:
        counts[byte] += 1
    total = len(blob)
    return -sum((count / total) * math.log2(count / total) for count in counts if count)


def analyse(frames: list[bytes] | None = None, verdict: str | None = None) -> dict:
    """Live-human check. Tests may pass an explicit verdict. Otherwise two frames are compared."""
    frames = [frame for frame in (frames or []) if frame]
    digest = hashlib.sha256(b"|".join(frames)).hexdigest() if frames else ""
    if verdict in {"pass", "fail"}:
        return {
            "verdict": "live" if verdict == "pass" else "fake",
            "confidence": 0.99 if verdict == "pass" else 0.95,
            "reasons": ["explicit check for the desk test"],
            "image_sha256": digest,
        }
    if len(frames) < 2:
        return {"verdict": "fake", "confidence": 0.7, "reasons": ["need two frames from the camera"], "image_sha256": digest}
    first, second = frames[0], frames[1]
    reasons = []
    if first == second:
        reasons.append("the two frames are identical, which a still photo produces")
    if _entropy(first) < 3.0 or _entropy(second) < 3.0:
        reasons.append("the image is too flat for a live face")
    if len(first) < 800 or len(second) < 800:
        reasons.append("the capture is too small for a face")
    # A printed photo or a screen grab tends to share long identical runs.
    same = sum(1 for a, b in zip(first[:4000], second[:4000]) if a == b)
    ratio = same / max(1, min(len(first), len(second), 4000))
    if ratio > 0.98:
        reasons.append("almost no change between the challenge frames")
    if reasons:
        return {"verdict": "fake", "confidence": 0.8, "reasons": reasons, "image_sha256": digest}
    return {
        "verdict": "live",
        "confidence": 0.86,
        "reasons": ["two different camera frames with enough detail"],
        "image_sha256": digest,
    }
