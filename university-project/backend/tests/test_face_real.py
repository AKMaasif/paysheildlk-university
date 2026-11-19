"""The real face model on real photos. Skipped unless FACE_TEST_IMAGES points at a folder of test photos.

The photos are not in the repository. Put them in a folder as FACE_TEST_IMAGES/<person>/<photo>.jpg with at
least two photos of one person and one photo of someone else, for example:
    obama/obama.jpg  obama/obama_small.jpg  biden/biden.jpg
"""
import io
import os
from pathlib import Path

import pytest

folder = os.environ.get("FACE_TEST_IMAGES")
pytestmark = pytest.mark.skipif(not folder or not Path(folder).exists(), reason="set FACE_TEST_IMAGES to a folder of test photos")


@pytest.fixture
def real(boot, monkeypatch):
    from app import face
    from app.runtime import runtime

    face.set_engine(None)
    runtime.settings.face_engine = "dlib"
    runtime.settings.face_challenge = False  # still photos cannot follow a challenge
    yield face
    runtime.settings.face_challenge = True


def _frames(path, n=8):
    from PIL import Image

    img = Image.open(path).convert("RGB")
    img.thumbnail((640, 640))
    frames = []
    for i in range(n):
        buf = io.BytesIO()
        img.rotate(0.4 * (i - n / 2)).save(buf, "JPEG", quality=88)
        frames.append(buf.getvalue())
    return frames, [i * 400 for i in range(n)]


def _photo(name):
    return next(Path(folder).rglob(name))


def test_the_same_person_in_another_photo_matches_and_a_stranger_does_not(real):
    ref, off = _frames(_photo("obama.jpg"))
    enrolled = real.enroll(ref, off, None)
    assert enrolled["ok"], enrolled
    same = real.verify(enrolled["sealed"], *_frames(_photo("obama_small.jpg")), None, "c")
    assert same.verdict in {"match", "likely"} and same.score >= 60
    stranger = real.verify(enrolled["sealed"], *_frames(_photo("biden.jpg")), None, "c")
    assert stranger.verdict == "no_match" and stranger.score < 30


def test_ordinary_changes_to_the_picture_do_not_lock_out_the_real_customer(real):
    from PIL import Image, ImageEnhance, ImageFilter

    enrolled = real.enroll(*_frames(_photo("obama.jpg")), None)
    base = Image.open(_photo("obama_small.jpg")).convert("RGB")
    for change in (
        ImageEnhance.Brightness(base).enhance(1.4),
        base.convert("L").convert("RGB"),
        base.filter(ImageFilter.GaussianBlur(1)),
        base.rotate(8),
    ):
        buf = io.BytesIO()
        change.save(buf, "JPEG", quality=88)
        frames = [buf.getvalue()] * 8
        res = real.verify(enrolled["sealed"], [buf.getvalue() + bytes([i]) for i in range(8)], [i * 400 for i in range(8)], None, "c")
        assert res.verdict in {"match", "likely", "poor_quality"}, res.verdict
        del frames


def test_a_covered_face_is_asked_to_try_again_not_called_fake(real):
    from PIL import Image, ImageDraw

    enrolled = real.enroll(*_frames(_photo("obama.jpg")), None)
    img = Image.open(_photo("obama_small.jpg")).convert("RGB")
    w, h = img.size
    ImageDraw.Draw(img).rectangle([0, int(h * 0.3), w, int(h * 0.55)], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    res = real.verify(enrolled["sealed"], [buf.getvalue() + bytes([i]) for i in range(8)], [i * 400 for i in range(8)], None, "c")
    assert res.verdict == "poor_quality"


def test_a_page_with_no_face_is_poor_quality(real):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (120, 120, 120)).save(buf, "JPEG")
    enrolled = real.enroll(*_frames(_photo("obama.jpg")), None)
    res = real.verify(enrolled["sealed"], [buf.getvalue() + bytes([i]) for i in range(8)], [i * 400 for i in range(8)], None, "c")
    assert res.verdict == "poor_quality"
