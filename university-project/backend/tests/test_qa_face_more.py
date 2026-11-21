"""The numbers and limits around face matching that are easy to get wrong."""
import base64
import math

import pytest

from app import face
from app.crypto import encrypt


def sealed(embeddings, refreshed=0, boot=None):
    return face.seal(face._pack([b"img"] * len(embeddings), embeddings, refreshed))


# ------------------------------------------------------------------ the score

def test_the_score_is_50_at_the_centre_distance_and_falls_steadily():
    assert face.similarity(face.CENTRE) == 50.0
    scores = [face.similarity(d / 100) for d in range(0, 100)]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("distance,low,high", [(0.0, 99.9, 100.0), (0.40, 95, 100), (0.56, 49.9, 50.1), (0.70, 0, 6), (5.0, 0.0, 0.0)])
def test_the_score_at_known_distances(distance, low, high):
    assert low <= face.similarity(distance) <= high


def test_a_very_large_distance_does_not_overflow():
    assert face.similarity(1e6) == 0.0 and face.similarity(-1e6) == 100.0


def test_the_distance_between_faces_is_symmetric_and_zero_for_the_same_face():
    a, b = [0.1, 0.2, 0.3], [0.4, 0.0, 0.3]
    assert face._distance(a, a) == 0 and face._distance(a, b) == face._distance(b, a)
    assert face._distance([0, 0], [3, 4]) == 5 and math.isfinite(face._distance([], []))


# ------------------------------------------------------------------ what a capture may look like

def frame(size=2000):
    return base64.b64encode(b"x" * size).decode()


def test_a_capture_must_have_the_allowed_number_of_frames():
    offsets = lambda n: [i * 300 for i in range(n)]  # noqa: E731
    assert len(face._decode([frame()] * face.MIN_FRAMES, offsets(face.MIN_FRAMES))[0]) == face.MIN_FRAMES
    with pytest.raises(face.BadCapture):
        face._decode([frame()] * (face.MIN_FRAMES - 1), offsets(face.MIN_FRAMES - 1))
    with pytest.raises(face.BadCapture):
        face._decode([frame()] * (face.MAX_FRAMES + 1), offsets(face.MAX_FRAMES + 1))


def test_frame_times_must_match_the_frames_and_never_go_backwards():
    frames = [frame()] * face.MIN_FRAMES
    with pytest.raises(face.BadCapture):
        face._decode(frames, None)
    with pytest.raises(face.BadCapture):
        face._decode(frames, [0] * (face.MIN_FRAMES - 1))
    with pytest.raises(face.BadCapture):
        face._decode(frames, [300 * i for i in reversed(range(face.MIN_FRAMES))])
    assert face._decode(frames, [0] * face.MIN_FRAMES)  # equal times are allowed


def test_an_empty_or_oversized_frame_is_refused():
    frames = [frame()] * face.MIN_FRAMES
    offsets = [i * 300 for i in range(face.MIN_FRAMES)]
    with pytest.raises(face.BadCapture):
        face._decode([""] + frames[1:], offsets)
    with pytest.raises(face.BadCapture):
        face._decode([frame(face.MAX_FRAME_BYTES + 1)] + frames[1:], offsets)


def test_raw_bytes_are_accepted_as_well_as_base64():
    frames = [b"raw-bytes-" * 100] * face.MIN_FRAMES
    assert face._decode(frames, [i * 300 for i in range(face.MIN_FRAMES)])[0][0].startswith(b"raw-bytes-")


# ------------------------------------------------------------------ refreshing the stored templates

def test_a_new_face_sample_is_added_and_counted(boot):
    base = [[0.0] * 128, [0.01] * 128, [0.02] * 128]
    updated = face.load_templates(face.refresh(sealed(base), [0.5] * 128))
    assert len(updated["embeddings"]) == 4 and updated["refreshed"] == 1


def test_a_near_copy_of_a_stored_sample_is_not_added_again(boot):
    base = [[0.0] * 128, [0.5] * 128]
    near_copy = [0.001] * 128  # a distance of about 0.011 from the first sample, under the 0.02 floor
    assert face.refresh(sealed(base), near_copy) is None


def test_the_first_three_samples_are_never_pushed_out_and_the_oldest_refreshed_one_goes_first(boot):
    first_three = [[float(i)] * 128 for i in range(face.FIXED_TEMPLATES)]
    refreshed = [[10.0 + i] * 128 for i in range(face.MAX_TEMPLATES - face.FIXED_TEMPLATES)]
    full = sealed(first_three + refreshed)
    updated = face.load_templates(face.refresh(full, [99.0] * 128))
    assert len(updated["embeddings"]) == face.MAX_TEMPLATES
    assert updated["embeddings"][: face.FIXED_TEMPLATES] == first_three
    assert [10.0] * 128 not in updated["embeddings"] and updated["embeddings"][-1] == [99.0] * 128


def test_nothing_is_refreshed_from_missing_templates_or_an_empty_sample(boot):
    assert face.refresh(sealed([[0.0] * 128]), []) is None
    assert face.refresh(encrypt("not json", boot.settings.encryption_key), [0.1] * 128) is None


def test_an_older_record_holding_one_picture_is_still_read_when_the_engine_is_missing(boot):
    face.set_engine(None)
    old = encrypt(base64.b64encode(b"one old jpeg").decode(), boot.settings.encryption_key)
    try:
        original = face._engine_error
        face._engine_error = "forced off for this test"
        assert face.load_templates(old) is None  # no engine to compute its embedding: treated as no face on file
    finally:
        face._engine_error = original
        face.set_engine(None)
