"""The edges of the face similarity tiers. These numbers decide who is let through, so each edge is pinned."""
import pytest

from app import face


@pytest.mark.parametrize("score,expected", [
    (100.0, "match"), (80.0, "match"), (79.99, "likely"), (60.0, "likely"), (59.99, "uncertain"),
    (30.0, "uncertain"), (29.99, "no_match"), (0.0, "no_match"), (-5.0, "no_match"),
])
def test_each_tier_edge(score, expected):
    assert face.tier(score) == expected


def test_the_published_floors_are_80_60_and_30():
    assert (face.MATCH, face.LIKELY, face.UNCERTAIN) == (80.0, 60.0, 30.0)


def test_a_higher_score_never_gives_a_lower_tier():
    order = {"no_match": 0, "uncertain": 1, "likely": 2, "match": 3}
    previous = -1
    for tenth in range(0, 1001):
        rank = order[face.tier(tenth / 10)]
        assert rank >= previous
        previous = rank
