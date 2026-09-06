from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from conversational_memory.application import (
    Embedding,
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
MODEL = "m3-cold-start-model"


class QueryEmbedder:
    def embed(self, content: str) -> Embedding:
        assert content == "What is my favorite programming language?"
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


def test_cold_start_does_not_force_the_nearest_unrelated_memory(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=MODEL,
        vector_dimension=2,
        create_if_missing=True,
    )
    for memory_id, score in (("faiss", 0.31), ("fastapi", 0.28), ("docker", 0.25)):
        memory = MemoryRecord(
            memory_id=memory_id,
            user_id="new-user",
            content=memory_id,
            memory_type=MemoryType.FACT,
            provenance=Provenance(
                authority=EvidenceAuthority.EXPLICIT_USER,
                source_type="explicit_user",
                conversation_id="machine-learning-project",
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
        repository.mark_indexed(user_id="new-user", memory_id=memory_id)
    service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=QueryEmbedder(),
        token_counter=CharacterCounter(),
        clock=FixedClock(),
        memory_ids=UnusedMemoryIds(),
        relevance_threshold=0.50,
    )

    result = service.retrieve(
        RequestContext(user_id="new-user", request_id="cold-start"),
        RetrievalRequest(
            query="What is my favorite programming language?",
            limit=3,
            token_budget=1000,
        ),
    )

    assert result.outcome is RetrievalOutcome.NO_RELEVANT_MEMORY
    assert result.memories == ()
    assert result.context == ""
    assert {item.memory_id for item in result.exclusions} == {
        "faiss",
        "fastapi",
        "docker",
    }
    assert all(
        item.reason is ContextExclusionReason.BELOW_RELEVANCE_THRESHOLD
        for item in result.exclusions
    )
