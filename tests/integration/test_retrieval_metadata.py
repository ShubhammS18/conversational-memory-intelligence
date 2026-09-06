from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conversational_memory.application import (
    ConfigurationError,
    Embedding,
    MemoryService,
    RequestContext,
    RetrievalOutcome,
    RetrievalRequest,
)
from conversational_memory.composition import compose_memory_service
from conversational_memory.domain.context import ContextExclusionReason
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.infrastructure import FaissVectorIndex, SQLiteMemoryRepository

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
MODEL = "m3-metadata-model"
THRESHOLD = 0.50


class MappingEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, content: str) -> Embedding:
        self.calls.append(content)
        return Embedding(values=(1.0, 0.0), model_id=MODEL, dimension=2)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class CharacterCounter:
    tokenizer_id = "cl100k_base"

    @staticmethod
    def count_tokens(text: str) -> int:
        return len(text)


class UnusedMemoryIds:
    def new_id(self) -> str:
        raise AssertionError("retrieval must not allocate a memory ID")


def _components(tmp_path: Path) -> tuple[SQLiteMemoryRepository, FaissVectorIndex]:
    return (
        SQLiteMemoryRepository(tmp_path / "memory.sqlite3"),
        FaissVectorIndex(
            tmp_path / "index",
            embedding_model=MODEL,
            vector_dimension=2,
            create_if_missing=True,
        ),
    )


def _compose(
    tmp_path: Path,
    *,
    threshold: object = THRESHOLD,
) -> tuple[MemoryService, MappingEmbedder, SQLiteMemoryRepository, FaissVectorIndex]:
    repository, vector_index = _components(tmp_path)
    embedder = MappingEmbedder()
    service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=embedder,
        token_counter=CharacterCounter(),
        clock=FixedClock(),
        memory_ids=UnusedMemoryIds(),
        relevance_threshold=threshold,
    )
    return service, embedder, repository, vector_index


def _seed(
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    *,
    memory_id: str,
    score: float,
) -> None:
    memory = MemoryRecord(
        memory_id=memory_id,
        user_id="user-1",
        content=memory_id,
        memory_type=MemoryType.FACT,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id=memory_id,
        ),
        created_at=NOW,
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.PENDING,
        valid_from=NOW,
    )
    embedding = Embedding(values=(score, 0.0), model_id=MODEL, dimension=2)
    persisted = repository.persist_pending(
        memory=memory,
        embedding=embedding,
        idempotency_key=memory_id,
        request_fingerprint=memory_id,
    )
    vector_index.add(vector_id=persisted.vector_id, embedding=embedding)
    repository.mark_indexed(user_id="user-1", memory_id=memory_id)


def test_empty_allowlist_has_distinct_no_eligible_metadata(tmp_path: Path) -> None:
    service, embedder, _, _ = _compose(tmp_path)

    result = service.retrieve(
        RequestContext(user_id="user-1", request_id="no-eligible"),
        RetrievalRequest(query="query", limit=5, token_budget=100),
    )

    assert result.outcome is RetrievalOutcome.NO_ELIGIBLE_MEMORY
    assert result.exclusions == ()
    assert embedder.calls == []


def test_relevance_and_budget_exclusions_remain_structurally_distinct(
    tmp_path: Path,
) -> None:
    service, _, repository, vector_index = _compose(tmp_path)
    _seed(repository, vector_index, memory_id="relevant", score=THRESHOLD)
    _seed(repository, vector_index, memory_id="irrelevant", score=0.49)

    result = service.retrieve(
        RequestContext(user_id="user-1", request_id="mixed"),
        RetrievalRequest(query="query", limit=5, token_budget=0),
    )

    assert result.outcome is RetrievalOutcome.BUDGET_EXCLUDED
    assert result.memories == ()
    assert result.context == ""
    assert {(item.memory_id, item.reason) for item in result.exclusions} == {
        ("relevant", ContextExclusionReason.BUDGET_EXCEEDED),
        ("irrelevant", ContextExclusionReason.BELOW_RELEVANCE_THRESHOLD),
    }


@pytest.mark.parametrize("threshold", [None, True, math.nan, 0.49])
def test_missing_or_invalid_injected_threshold_fails_closed(
    tmp_path: Path,
    threshold: object,
) -> None:
    repository, vector_index = _components(tmp_path)

    with pytest.raises(ConfigurationError, match="invalid_relevance_threshold"):
        compose_memory_service(
            repository=repository,
            vector_index=vector_index,
            embedder=MappingEmbedder(),
            token_counter=CharacterCounter(),
            clock=FixedClock(),
            memory_ids=UnusedMemoryIds(),
            relevance_threshold=threshold,
        )


def test_omitted_threshold_fails_closed_without_a_fallback(tmp_path: Path) -> None:
    repository, vector_index = _components(tmp_path)

    with pytest.raises(ConfigurationError, match="invalid_relevance_threshold"):
        compose_memory_service(
            repository=repository,
            vector_index=vector_index,
            embedder=MappingEmbedder(),
            token_counter=CharacterCounter(),
            clock=FixedClock(),
            memory_ids=UnusedMemoryIds(),
        )
