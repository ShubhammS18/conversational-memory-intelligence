"""Pure trusted-time validation and expiration-transition policy."""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import IndexingState, LifecycleStatus, MemoryRecord


def validate_trusted_utc(value: object) -> datetime:
    """Return a trusted aware UTC datetime unchanged, rejecting other values."""
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("trusted clock must return an aware UTC datetime")
    return value


def is_expiration_transition_eligible(
    memory: MemoryRecord,
    *,
    user_id: str,
    now: datetime,
) -> bool:
    """Return whether an otherwise-current memory has reached its exact end."""
    trusted_now = validate_trusted_utc(now)
    return (
        memory.user_id == user_id
        and memory.indexing_state is IndexingState.INDEXED
        and memory.deleted_at is None
        and memory.lifecycle_status is LifecycleStatus.ACTIVE
        and memory.superseded_by is None
        and memory.valid_until is not None
        and trusted_now >= memory.valid_until
    )
