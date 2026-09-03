from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

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


def _eligible_memory() -> MemoryRecord:
    return MemoryRecord(
        memory_id="memory-1",
        user_id="user-1",
        content="I prefer FAISS.",
        memory_type=MemoryType.PREFERENCE,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id="turn-1",
        ),
        created_at=NOW - timedelta(days=2),
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.INDEXED,
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=1),
    )


@pytest.mark.parametrize(
    ("case", "memory", "user_id", "expected"),
    [
        ("all requirements satisfied", _eligible_memory(), "user-1", True),
        ("wrong owner", _eligible_memory(), "user-2", False),
        (
            "pending",
            replace(_eligible_memory(), indexing_state=IndexingState.PENDING),
            "user-1",
            False,
        ),
        (
            "failed",
            replace(_eligible_memory(), indexing_state=IndexingState.FAILED),
            "user-1",
            False,
        ),
        (
            "superseded lifecycle",
            replace(_eligible_memory(), lifecycle_status=LifecycleStatus.SUPERSEDED),
            "user-1",
            False,
        ),
        (
            "expired lifecycle",
            replace(_eligible_memory(), lifecycle_status=LifecycleStatus.EXPIRED),
            "user-1",
            False,
        ),
        (
            "deleted tombstone",
            replace(_eligible_memory(), deleted_at=NOW - timedelta(seconds=1)),
            "user-1",
            False,
        ),
        (
            "superseded relationship",
            replace(_eligible_memory(), superseded_by="memory-2"),
            "user-1",
            False,
        ),
        (
            "no start boundary",
            replace(_eligible_memory(), valid_from=None),
            "user-1",
            True,
        ),
        (
            "at inclusive start",
            replace(_eligible_memory(), valid_from=NOW),
            "user-1",
            True,
        ),
        (
            "before start",
            replace(_eligible_memory(), valid_from=NOW + timedelta(microseconds=1)),
            "user-1",
            False,
        ),
        (
            "no end boundary",
            replace(_eligible_memory(), valid_until=None),
            "user-1",
            True,
        ),
        (
            "before exclusive end",
            replace(_eligible_memory(), valid_until=NOW + timedelta(microseconds=1)),
            "user-1",
            True,
        ),
        (
            "at exclusive end",
            replace(_eligible_memory(), valid_until=NOW),
            "user-1",
            False,
        ),
        (
            "after end",
            replace(_eligible_memory(), valid_until=NOW - timedelta(microseconds=1)),
            "user-1",
            False,
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_current_state_eligibility_truth_table(
    case: str,
    memory: MemoryRecord,
    user_id: str,
    expected: bool,
) -> None:
    del case

    assert is_current_state_eligible(memory, user_id=user_id, now=NOW) is expected
