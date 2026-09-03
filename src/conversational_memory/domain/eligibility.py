"""Pure current-state retrieval eligibility policy."""

from __future__ import annotations

from datetime import datetime

from .models import IndexingState, LifecycleStatus, MemoryRecord


def is_current_state_eligible(
    memory: MemoryRecord,
    *,
    user_id: str,
    now: datetime,
) -> bool:
    """Return whether a memory satisfies every deterministic M2 read rule."""
    return (
        memory.user_id == user_id
        and memory.indexing_state is IndexingState.INDEXED
        and memory.deleted_at is None
        and memory.lifecycle_status is LifecycleStatus.ACTIVE
        and memory.superseded_by is None
        and (memory.valid_from is None or memory.valid_from <= now)
        and (memory.valid_until is None or now < memory.valid_until)
    )
