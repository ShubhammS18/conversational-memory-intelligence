"""Controlled outcomes for explicit owner-scoped forgetting."""

from enum import StrEnum


class ForgetOutcome(StrEnum):
    """Mutually exclusive public outcomes of a forget operation."""

    FORGOTTEN = "forgotten"
    CLEANUP_PENDING = "cleanup_pending"
    NOT_FOUND = "not_found"


class ForgettingCleanupState(StrEnum):
    """Durable progress after a memory is logically forgotten."""

    CLEANUP_PENDING = "cleanup_pending"
    COMPLETE = "complete"
