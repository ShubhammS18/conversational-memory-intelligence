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
from conversational_memory.entrypoints.cli import build_parser
from conversational_memory.infrastructure import FaissVectorIndex, SQLiteMemoryRepository

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
MODEL = "m3-test-model"
THRESHOLD = 0.50


class MappingEmbedder:
    def __init__(self, vectors: dict[str, tuple[float, float]]) -> None:
        self._vectors = vectors
        self.calls: list[str] = []

    def embed(self, content: str) -> Embedding:
        self.calls.append(content)
        return Embedding(values=self._vectors[content], model_id=MODEL, dimension=2)


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


def test_cli_exposes_the_m3_no_memory_demo() -> None:
    assert build_parser().parse_args(["demo-no-memory"]).command == "demo-no-memory"


def _seed(
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    *,
    memory_id: str,
    score: float,
    user_id: str = "user-1",
    indexed: bool = True,
) -> None:
    memory = MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        content=f"eligible {memory_id}",
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
    if indexed:
        repository.mark_indexed(user_id=user_id, memory_id=memory_id)


def test_below_threshold_candidates_return_successful_no_relevant_memory(
    tmp_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=MODEL,
        vector_dimension=2,
        create_if_missing=True,
    )
    for memory_id, score in (("nearest", 0.49), ("farther", 0.25)):
        _seed(repository, vector_index, memory_id=memory_id, score=score)
    _seed(repository, vector_index, memory_id="pending", score=0.99, indexed=False)
    _seed(
        repository,
        vector_index,
        memory_id="other-owner",
        score=1.0,
        user_id="user-2",
    )
    embedder = MappingEmbedder({"unrelated query": (1.0, 0.0)})
    service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=embedder,
        token_counter=CharacterCounter(),
        clock=FixedClock(),
        memory_ids=UnusedMemoryIds(),
        relevance_threshold=THRESHOLD,
    )

    result = service.retrieve(
        RequestContext(user_id="user-1", request_id="retrieve-unrelated"),
        RetrievalRequest(query="unrelated query", limit=10, token_budget=1000),
    )

    assert result.outcome is RetrievalOutcome.NO_RELEVANT_MEMORY
    assert result.memories == ()
    assert result.context == ""
    assert result.tokens_used == 0
    assert result.included_memory_ids == ()
    assert [(item.memory_id, item.reason) for item in result.exclusions] == [
        ("nearest", ContextExclusionReason.BELOW_RELEVANCE_THRESHOLD),
        ("farther", ContextExclusionReason.BELOW_RELEVANCE_THRESHOLD),
    ]
