"""Pure relevance-threshold validation and selection policy."""

from __future__ import annotations

import math

_APPROVED_M3_THRESHOLD = 0.50


def validate_relevance_threshold(value: object) -> float:
    """Return the approved explicit threshold or fail closed."""
    if (
        type(value) is not float
        or not math.isfinite(value)
        or not -1.0 <= value <= 1.0
        or value != _APPROVED_M3_THRESHOLD
    ):
        raise ValueError("invalid relevance threshold")
    return value


def is_relevant(*, eligible: bool, score: float, threshold: object) -> bool:
    """Apply M2 eligibility before the exact inclusive M3 score boundary."""
    approved_threshold = validate_relevance_threshold(threshold)
    if type(eligible) is not bool:
        raise ValueError("eligibility must be boolean")
    if not eligible:
        return False
    if type(score) is not float or not math.isfinite(score):
        raise ValueError("relevance score must be finite")
    return score >= approved_threshold
