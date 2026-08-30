from datetime import UTC, datetime, timedelta

from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.domain.ranking import RetrievalCandidate, rank_candidates

NOW = datetime(2026, 8, 30, tzinfo=UTC)


def _candidate(
    memory_id: str,
    *,
    relevance: float,
    authority: EvidenceAuthority = EvidenceAuthority.EXPLICIT_USER,
    created_at: datetime = NOW,
    valid_from: datetime | None = None,
    eligible: bool = True,
) -> RetrievalCandidate:
    memory = MemoryRecord(
        memory_id=memory_id,
        user_id="user-1",
        content=f"Memory {memory_id}",
        memory_type=MemoryType.FACT,
        provenance=Provenance(
            authority=authority,
            source_type="conversation",
            conversation_id="conversation-1",
            turn_id=f"turn-{memory_id}",
        ),
        created_at=created_at,
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.INDEXED,
        valid_from=valid_from,
    )
    return RetrievalCandidate(memory=memory, relevance=relevance, eligible=eligible)


def test_eligibility_precedes_every_ranking_signal() -> None:
    ranked = rank_candidates(
        [
            _candidate("ineligible", relevance=1.0, eligible=False),
            _candidate("eligible", relevance=0.1),
        ]
    )

    assert [candidate.memory.memory_id for candidate in ranked] == ["eligible"]


def test_ranking_is_relevance_then_authority_then_recency_then_stable_id() -> None:
    ranked = rank_candidates(
        [
            _candidate("z", relevance=0.8, created_at=NOW),
            _candidate("a", relevance=0.8, created_at=NOW),
            _candidate("older", relevance=0.8, created_at=NOW - timedelta(days=1)),
            _candidate(
                "inferred",
                relevance=0.8,
                authority=EvidenceAuthority.INFERRED,
                created_at=NOW + timedelta(days=1),
            ),
            _candidate("most-relevant", relevance=0.9, created_at=NOW - timedelta(days=10)),
        ]
    )

    assert [candidate.memory.memory_id for candidate in ranked] == [
        "most-relevant",
        "a",
        "z",
        "older",
        "inferred",
    ]


def test_recency_uses_valid_from_when_creation_and_validity_order_disagree() -> None:
    ranked = rank_candidates(
        [
            _candidate(
                "created-later",
                relevance=0.8,
                created_at=NOW,
                valid_from=NOW - timedelta(days=2),
            ),
            _candidate(
                "valid-later",
                relevance=0.8,
                created_at=NOW - timedelta(days=1),
                valid_from=NOW - timedelta(days=1),
            ),
        ]
    )

    assert [candidate.memory.memory_id for candidate in ranked] == [
        "valid-later",
        "created-later",
    ]
