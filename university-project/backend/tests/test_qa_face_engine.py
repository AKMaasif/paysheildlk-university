"""The real dlib engine's behaviour on input that has no usable face, and the geometry helpers.
These need the dlib models but no photos of people. The tests that need real faces are in test_face_real.py."""
import io

import pytest
from PIL import Image

from app import face

dlib = pytest.importorskip("dlib")


@pytest.fixture(scope="module")
def engine():
    try:
        return face.DlibEngine()
    except Exception as exc:  # the model files are missing in this environment
        pytest.skip(f"face models unavailable: {exc}")


def picture(size=(320, 240), colour=(120, 130, 140), mode="RGB", fmt="JPEG"):
    out = io.BytesIO()
    Image.new(mode, size, colour).save(out, fmt)
    return out.getvalue()


# ------------------------------------------------------------------ the engine on input with no face

def test_a_plain_picture_has_no_face_and_no_embedding(engine):
    seen = engine.observe(picture(), want_embedding=True)
    assert seen.faces == 0 and seen.embedding is None and seen.landmarks is None
    assert (seen.width, seen.height) == (320, 240)


@pytest.mark.parametrize("payload", [b"", b"not an image at all", b"\xff\xd8\xff\xe0 truncated", b"GIF89a" + b"\x00" * 10, bytes(range(256)) * 20])
def test_something_that_is_not_an_image_is_a_bad_capture_never_a_crash(engine, payload):
    with pytest.raises(face.BadCapture):
        engine.observe(payload, want_embedding=False)


@pytest.mark.parametrize("mode,colour,fmt", [("L", 128, "PNG"), ("RGBA", (10, 20, 30, 40), "PNG"), ("P", 3, "PNG"), ("RGB", (1, 2, 3), "BMP")])
def test_other_image_formats_are_accepted_and_read_as_rgb(engine, mode, colour, fmt):
    seen = engine.observe(picture(mode=mode, colour=colour, fmt=fmt), want_embedding=False)
    assert seen.faces == 0 and seen.width == 320


def test_a_very_large_picture_is_shrunk_before_it_is_searched(engine):
    seen = engine.observe(picture(size=(3200, 2400)), want_embedding=False)
    assert max(seen.width, seen.height) <= 1280


def test_a_tiny_picture_is_handled(engine):
    assert engine.observe(picture(size=(8, 8)), want_embedding=True).faces == 0


def test_the_engine_is_safe_to_use_from_several_threads(engine):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: engine.observe(picture(), want_embedding=False).faces, range(12)))
    assert results == [0] * 12


# ------------------------------------------------------------------ the quality numbers

def test_quality_of_an_empty_crop_is_all_zero():
    import numpy as np

    zero = face._quality(np.zeros((50, 50, 3), dtype="uint8"), (60, 60, 60, 60))
    assert zero == {"face_ratio": 0.0, "sharpness": 0.0, "brightness": 0.0, "contrast": 0.0}


def test_quality_tells_a_sharp_bright_crop_from_a_flat_dark_one():
    import numpy as np

    rng = np.random.default_rng(1)
    sharp = rng.integers(0, 255, (120, 120, 3), dtype="uint8")
    flat = np.full((120, 120, 3), 20, dtype="uint8")
    a, b = face._quality(sharp, (10, 10, 110, 110)), face._quality(flat, (10, 10, 110, 110))
    assert a["sharpness"] > 100 > b["sharpness"] == 0
    assert a["brightness"] > b["brightness"] and a["contrast"] > b["contrast"] == 0
    assert a["face_ratio"] == round(100 / 120, 3)


def test_a_box_that_hangs_over_the_edge_is_clamped():
    import numpy as np

    seen = face._quality(np.full((100, 100, 3), 90, dtype="uint8"), (-30, -30, 80, 80))
    assert seen["brightness"] == 90.0


# ------------------------------------------------------------------ the geometry helpers on made-up landmarks

def landmarks(**moves):
    points = [(50.0, 50.0)] * 68
    points = list(points)
    for i in range(36, 42):
        points[i] = (30.0 + (i - 36) * 4, 40.0)
    return points


def eye(open_by):
    return [(0, 0), (1, -open_by), (2, -open_by), (3, 0), (2, open_by), (1, open_by)]


def build(yaw_nose=50.0, open_by=1.0, mouth_open=0.0):
    pts = [(0.0, 0.0)] * 68
    pts[1] = (0.0, 0.0)
    pts[15] = (100.0, 0.0)
    pts[30] = (yaw_nose, 0.0)
    for base in (36, 42):
        for k, (x, y) in enumerate(eye(open_by)):
            pts[base + k] = (base + x * 10, y * 10)
    pts[60], pts[64] = (20.0, 80.0), (60.0, 80.0)
    pts[62], pts[66] = (40.0, 80.0 - mouth_open / 2), (40.0, 80.0 + mouth_open / 2)
    return pts


def test_yaw_is_zero_facing_forward_and_signed_when_turned():
    assert face.Observation(faces=1, landmarks=build(yaw_nose=50.0)).yaw == pytest.approx(0.0)
    assert face.Observation(faces=1, landmarks=build(yaw_nose=70.0)).yaw == pytest.approx(0.2)
    assert face.Observation(faces=1, landmarks=build(yaw_nose=30.0)).yaw == pytest.approx(-0.2)


def test_eye_openness_and_mouth_openness_move_with_the_face():
    closed, wide = face.Observation(faces=1, landmarks=build(open_by=0.1)), face.Observation(faces=1, landmarks=build(open_by=1.2))
    assert closed.ear < wide.ear
    shut, yawn = face.Observation(faces=1, landmarks=build(mouth_open=0.0)), face.Observation(faces=1, landmarks=build(mouth_open=30.0))
    assert shut.mouth < yawn.mouth


def test_missing_or_degenerate_landmarks_give_none_not_an_error():
    assert face.Observation(faces=0).yaw is None and face.Observation(faces=0).ear is None and face.Observation(faces=0).mouth is None
    flat = [(5.0, 5.0)] * 68  # every point the same: zero width, zero span
    seen = face.Observation(faces=1, landmarks=flat)
    assert seen.yaw is None and seen.ear is None and seen.mouth is None


# ------------------------------------------------------------------ choosing and loading the engine

def test_an_engine_that_cannot_start_is_reported_as_unavailable_and_not_retried(monkeypatch):
    face.set_engine(None)
    calls = []

    class Broken:
        def __init__(self):
            calls.append(1)
            raise RuntimeError("no model files")

    monkeypatch.setattr(face, "DlibEngine", Broken)
    try:
        assert face.get_engine() is None and face.get_engine() is None
        assert len(calls) == 1
        assert face._engine_error == "no model files"
    finally:
        face.set_engine(None)


def test_stored_face_data_that_is_missing_or_unreadable_loads_as_nothing(boot):
    assert face.load_templates(None) is None and face.load_templates("") is None
    assert face.load_templates("gcm$not-valid-ciphertext") is None


def test_tampered_stored_face_data_is_treated_as_no_face_on_file(boot):
    from app.crypto import encrypt

    good = encrypt('{"embeddings": [[0.1]], "images": []}', boot.settings.encryption_key, "face")
    assert face.load_templates(good) is not None
    nonce, token = good.split("$")[1:]
    flipped = f"gcm2${nonce}${token[:-2]}{'00' if token[-2:] != '00' else 'ff'}"
    assert face.load_templates(flipped) is None
    wrong_key = encrypt('{"embeddings": [[0.1]]}', "some-other-key-0123456789-0123456789-xx", "face")
    assert face.load_templates(wrong_key) is None
