from __future__ import annotations

import math

import pytest

from conversational_memory.domain.relevance import (
    is_relevant,
    validate_relevance_threshold,
)

THRESHOLD = 0.50


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (math.nextafter(THRESHOLD, -math.inf), False),
        (THRESHOLD, True),
        (math.nextafter(THRESHOLD, math.inf), True),
    ],
    ids=("immediately-below", "equal", "immediately-above"),
)
def test_relevance_threshold_has_an_exact_inclusive_boundary(
    score: float,
    expected: bool,
) -> None:
    assert is_relevant(eligible=True, score=score, threshold=THRESHOLD) is expected


def test_m2_ineligibility_precedes_a_high_relevance_score() -> None:
    assert not is_relevant(eligible=False, score=1.0, threshold=THRESHOLD)


def test_approved_threshold_is_returned_unchanged() -> None:
    assert validate_relevance_threshold(THRESHOLD) is THRESHOLD


@pytest.mark.parametrize(
    "value",
    [None, True, False, math.nan, math.inf, -math.inf],
    ids=("missing", "true", "false", "nan", "positive-infinity", "negative-infinity"),
)
def test_missing_boolean_and_nonfinite_thresholds_fail_closed(value: object) -> None:
    with pytest.raises(ValueError, match="invalid relevance threshold"):
        validate_relevance_threshold(value)
