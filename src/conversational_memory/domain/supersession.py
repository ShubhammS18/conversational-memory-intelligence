"""Pure validation for an explicitly targeted supersession."""

from __future__ import annotations

from .idempotency import normalize_text
from .models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
)


def validate_supersession_target(
    *,
    target: MemoryRecord,
    user_id: str,
    replacement_memory_id: str,
    replacement_authority: EvidenceAuthority,
    replacement_subject: str | None,
    replacement_memory_type: MemoryType,
) -> None:
    """Reject a target that cannot be explicitly superseded under the M4 binding."""
    normalized_subject = (
        None if replacement_subject is None else normalize_text(replacement_subject)
    )
    target_subject = None if target.subject is None else normalize_text(target.subject)
    if (
        replacement_authority is not EvidenceAuthority.EXPLICIT_USER
        or not user_id.strip()
        or not replacement_memory_id.strip()
        or target.memory_id == replacement_memory_id
        or target.user_id != user_id
        or target.indexing_state is not IndexingState.INDEXED
        or target.lifecycle_status is not LifecycleStatus.ACTIVE
        or target.superseded_by is not None
        or normalized_subject is None
        or not normalized_subject
        or target_subject is None
        or not target_subject
        or normalized_subject != target_subject
        or replacement_memory_type is not target.memory_type
    ):
        raise ValueError("invalid supersession target")
