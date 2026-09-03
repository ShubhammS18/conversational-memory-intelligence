from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conversational_memory.domain.eligibility import is_current_state_eligible
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


def _memory(*, valid_from: datetime | None, valid_until: datetime | None) -> MemoryRecord:
    return MemoryRecord(
        memory_id="boundary-memory",
        user_id="user-1",
        content="A boundary memory.",
        memory_type=MemoryType.FACT,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id="turn-1",
        ),
        created_at=NOW - timedelta(days=1),
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.INDEXED,
        valid_from=valid_from,
        valid_until=valid_until,
    )


def test_current_state_validity_is_start_inclusive_and_end_exclusive() -> None:
    at_start = _memory(valid_from=NOW, valid_until=NOW + timedelta(microseconds=1))
    at_end = _memory(valid_from=NOW - timedelta(microseconds=1), valid_until=NOW)

    assert is_current_state_eligible(at_start, user_id="user-1", now=NOW)
    assert not is_current_state_eligible(at_end, user_id="user-1", now=NOW)
