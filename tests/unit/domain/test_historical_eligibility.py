from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from conversational_memory.application import RetrievalIntent, RetrievalRequest
from conversational_memory.domain.eligibility import is_historical_eligible
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def _active_memory() -> MemoryRecord:
    return MemoryRecord(
        memory_id="memory-1",
        user_id="user-1",
        content="I prefer Qdrant.",
        memory_type=MemoryType.PREFERENCE,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id="turn-1",
        ),
        created_at=NOW - timedelta(days=1),
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.INDEXED,
        valid_from=NOW - timedelta(days=2),
        valid_until=NOW + timedelta(days=2),
    )


def _request(**changes: object) -> RetrievalRequest:
    values: dict[str, object] = {
        "query": "What did I use before Qdrant?",
        "limit": 5,
        "token_budget": 128,
    }
    values.update(changes)
    return RetrievalRequest(**values)  # type: ignore[arg-type]


def test_retrieval_intent_defaults_to_current() -> None:
    assert _request().intent is RetrievalIntent.CURRENT


def test_explicit_historical_intent_is_retained_exactly() -> None:
    assert _request(intent=RetrievalIntent.HISTORICAL).intent is RetrievalIntent.HISTORICAL


@pytest.mark.parametrize("value", ["historical", "current", "unknown", True, None])
def test_retrieval_intent_rejects_non_enum_inputs(value: object) -> None:
    with pytest.raises(PydanticValidationError):
        _request(intent=value)


@pytest.mark.parametrize(
    ("case", "memory", "user_id", "expected"),
    [
        ("active", _active_memory(), "user-1", True),
        (
            "superseded",
            replace(
                _active_memory(),
                lifecycle_status=LifecycleStatus.SUPERSEDED,
                superseded_by="memory-2",
            ),
            "user-1",
            True,
        ),
        ("wrong owner", _active_memory(), "user-2", False),
        (
            "pending",
            replace(_active_memory(), indexing_state=IndexingState.PENDING),
            "user-1",
            False,
        ),
        (
            "failed",
            replace(_active_memory(), indexing_state=IndexingState.FAILED),
            "user-1",
            False,
        ),
        (
            "deleted",
            replace(_active_memory(), deleted_at=NOW),
            "user-1",
            False,
        ),
        (
            "active with superseded-by",
            replace(_active_memory(), superseded_by="memory-2"),
            "user-1",
            False,
        ),
        (
            "superseded without superseded-by",
            replace(_active_memory(), lifecycle_status=LifecycleStatus.SUPERSEDED),
            "user-1",
            False,
        ),
        (
            "expired lifecycle",
            replace(_active_memory(), lifecycle_status=LifecycleStatus.EXPIRED),
            "user-1",
            False,
        ),
        (
            "future validity",
            replace(
                _active_memory(),
                valid_from=NOW + timedelta(days=1),
                valid_until=NOW + timedelta(days=2),
            ),
            "user-1",
            True,
        ),
        (
            "past validity",
            replace(
                _active_memory(),
                valid_from=NOW - timedelta(days=2),
                valid_until=NOW - timedelta(days=1),
            ),
            "user-1",
            True,
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_historical_eligibility_truth_table(
    case: str,
    memory: MemoryRecord,
    user_id: str,
    expected: bool,
) -> None:
    del case

    assert is_historical_eligible(memory, user_id=user_id) is expected
