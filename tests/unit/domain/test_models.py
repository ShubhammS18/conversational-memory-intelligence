from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)


def test_memory_and_provenance_records_are_frozen() -> None:
    provenance = Provenance(
        authority=EvidenceAuthority.EXPLICIT_USER,
        source_type="conversation",
        conversation_id="conversation-1",
        turn_id="turn-1",
    )
    memory = MemoryRecord(
        memory_id="memory-1",
        user_id="user-1",
        content="I prefer FAISS.",
        memory_type=MemoryType.PREFERENCE,
        provenance=provenance,
        created_at=datetime(2026, 8, 30, tzinfo=UTC),
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.INDEXED,
    )

    with pytest.raises(FrozenInstanceError):
        memory.content = "mutated"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        provenance.source_type = "mutated"  # type: ignore[misc]


def test_memory_requires_aware_timestamps_and_valid_interval() -> None:
    provenance = Provenance(
        authority=EvidenceAuthority.INFERRED,
        source_type="conversation",
        conversation_id="conversation-1",
        turn_id="turn-1",
    )

    with pytest.raises(ValueError, match="created_at must be timezone-aware"):
        MemoryRecord(
            memory_id="memory-1",
            user_id="user-1",
            content="A fact.",
            memory_type=MemoryType.FACT,
            provenance=provenance,
            created_at=datetime(2026, 8, 30, tzinfo=UTC).replace(tzinfo=None),
            lifecycle_status=LifecycleStatus.ACTIVE,
            indexing_state=IndexingState.PENDING,
        )

    with pytest.raises(ValueError, match="valid_from must not be after valid_until"):
        MemoryRecord(
            memory_id="memory-1",
            user_id="user-1",
            content="A fact.",
            memory_type=MemoryType.FACT,
            provenance=provenance,
            created_at=datetime(2026, 8, 30, tzinfo=UTC),
            lifecycle_status=LifecycleStatus.ACTIVE,
            indexing_state=IndexingState.PENDING,
            valid_from=datetime(2026, 9, 2, tzinfo=UTC),
            valid_until=datetime(2026, 9, 1, tzinfo=UTC),
        )
