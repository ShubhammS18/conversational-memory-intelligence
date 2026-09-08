from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from conversational_memory.domain.expiration import (
    is_expiration_transition_eligible,
    validate_trusted_utc,
)
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def _memory() -> MemoryRecord:
    return MemoryRecord(
        memory_id="memory-1",
        user_id="user-1",
        content="The deployment window ends at noon.",
        memory_type=MemoryType.CONSTRAINT,
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
        valid_until=NOW,
    )


def test_trusted_utc_validation_returns_original_utc_datetime() -> None:
    assert validate_trusted_utc(NOW) is NOW


@pytest.mark.parametrize(
    "value",
    [
        None,
        "2026-09-08T12:00:00Z",
        datetime(2026, 9, 8, 12),  # noqa: DTZ001 - intentionally invalid input
        datetime(2026, 9, 8, 12, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_trusted_utc_validation_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="trusted clock must return an aware UTC datetime"):
        validate_trusted_utc(value)


@pytest.mark.parametrize(
    ("case", "memory", "user_id", "now", "expected"),
    [
        ("before exact end", _memory(), "user-1", NOW - timedelta(microseconds=1), False),
        ("at exact end", _memory(), "user-1", NOW, True),
        ("after exact end", _memory(), "user-1", NOW + timedelta(microseconds=1), True),
        ("other owner", _memory(), "user-2", NOW, False),
        (
            "pending",
            replace(_memory(), indexing_state=IndexingState.PENDING),
            "user-1",
            NOW,
            False,
        ),
        (
            "failed",
            replace(_memory(), indexing_state=IndexingState.FAILED),
            "user-1",
            NOW,
            False,
        ),
        (
            "deleted",
            replace(_memory(), deleted_at=NOW - timedelta(hours=1)),
            "user-1",
            NOW,
            False,
        ),
        (
            "superseded lifecycle",
            replace(
                _memory(),
                lifecycle_status=LifecycleStatus.SUPERSEDED,
                superseded_by="memory-2",
            ),
            "user-1",
            NOW,
            False,
        ),
        (
            "expired lifecycle",
            replace(_memory(), lifecycle_status=LifecycleStatus.EXPIRED),
            "user-1",
            NOW,
            False,
        ),
        (
            "active relationship",
            replace(_memory(), superseded_by="memory-2"),
            "user-1",
            NOW,
            False,
        ),
        ("no end", replace(_memory(), valid_until=None), "user-1", NOW, False),
        (
            "start at end does not override elapsed end",
            replace(
                _memory(),
                valid_from=NOW,
                valid_until=NOW,
            ),
            "user-1",
            NOW,
            True,
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_expiration_transition_eligibility_truth_table(
    case: str,
    memory: MemoryRecord,
    user_id: str,
    now: datetime,
    expected: bool,
) -> None:
    del case

    assert (
        is_expiration_transition_eligible(memory, user_id=user_id, now=now)
        is expected
    )


def test_expiration_eligibility_is_pure_and_preserves_record_state() -> None:
    memory = replace(
        _memory(),
        supersedes=("memory-0",),
    )

    before = memory

    assert is_expiration_transition_eligible(memory, user_id="user-1", now=NOW)
    assert memory == before
    assert memory.supersedes == ("memory-0",)
