"""Only a distinction: the boundaries and the pass and fail branches are left untested on purpose."""

from weak.grade import grade


def test_a_high_score_is_a_distinction() -> None:
    assert grade(95) == "distinction"
