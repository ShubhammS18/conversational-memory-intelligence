from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conversational_memory.application import (
    Embedding,
    MemoryService,
    RequestContext,
    RetrievalIntent,
    RetrievalOutcome,
    RetrievalRequest,
    ServiceUnavailableError,
)
from conversational_memory.composition import compose_recovered_memory_service
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.infrastructure.sqlite import SQLiteMemoryRepository

NOW = datetime(2026, 9, 9, tzinfo=UTC)
EMBEDDING = Embedding(values=(1.0, 0.0), model_id="test-model", dimension=2)


class _Embedder:
    def embed(self, content: str) -> Embedding:
        return EMBEDDING


class _Counter:
    tokenizer_id = "cl100k_base"

    def count_tokens(self, text: str) -> int:
        return len(text)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Ids:
    def new_id(self) -> str:
        return "unused"


def _persist(repository: SQLiteMemoryRepository, memory_id: str) -> None:
    repository.persist_pending(
        memory=MemoryRecord(
            memory_id=memory_id,
            user_id="user-1",
            content="recovery memory",
            memory_type=MemoryType.FACT,
            provenance=Provenance(
                authority=EvidenceAuthority.EXPLICIT_USER,
                source_type="explicit_user",
                conversation_id="conversation-1",
                turn_id=f"turn-{memory_id}",
            ),
            created_at=NOW,
            lifecycle_status=LifecycleStatus.ACTIVE,
            indexing_state=IndexingState.PENDING,
        ),
        embedding=EMBEDDING,
        idempotency_key=f"key-{memory_id}",
        request_fingerprint=memory_id.ljust(64, "0"),
    )


def _compose(repository: SQLiteMemoryRepository, index: Path):
    return compose_recovered_memory_service(
        repository=repository,
        index_directory=index,
        embedding_model="test-model",
        vector_dimension=2,
        embedder=_Embedder(),
        token_counter=_Counter(),
        clock=_Clock(),
        memory_ids=_Ids(),
        relevance_threshold=0.50,
    )


def test_ready_startup_returns_service_and_readiness(tmp_path: Path) -> None:
    runtime = _compose(SQLiteMemoryRepository(tmp_path / "memory.sqlite3"), tmp_path / "index")

    assert isinstance(runtime.service, MemoryService)
    assert runtime.recovery.readiness.value == "ready"


def test_degraded_startup_returns_usable_service(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    _persist(repository, "pending")

    runtime = _compose(repository, tmp_path / "index")

    assert isinstance(runtime.service, MemoryService)
    assert runtime.recovery.readiness.value == "degraded"
    assert runtime.recovery.pending_count == 1


def test_null_id_degraded_startup_exposes_usable_service_operations(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "forgotten")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM memory_vector_mappings WHERE memory_id = 'forgotten'"
        )
        connection.commit()
    repository.begin_forgetting(
        user_id="user-1", memory_id="forgotten", requested_at=NOW
    )

    runtime = _compose(repository, tmp_path / "index")
    current = runtime.service.retrieve(
        RequestContext(user_id="user-1", request_id="current"),
        RetrievalRequest(query="memory", limit=5, token_budget=128),
    )
    historical = runtime.service.retrieve(
        RequestContext(user_id="user-1", request_id="history"),
        RetrievalRequest(
            query="memory",
            limit=5,
            token_budget=128,
            intent=RetrievalIntent.HISTORICAL,
        ),
    )

    assert runtime.recovery.readiness.value == "degraded"
    assert runtime.recovery.cleanup_pending_count == 1
    assert current.outcome is RetrievalOutcome.NO_ELIGIBLE_MEMORY
    assert historical.outcome is RetrievalOutcome.NO_ELIGIBLE_MEMORY


def test_unavailable_startup_refuses_to_expose_service(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "memory-1")
    repository.mark_indexed(user_id="user-1", memory_id="memory-1")
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM memory_vector_mappings WHERE memory_id = 'memory-1'")
        connection.commit()

    with pytest.raises(ServiceUnavailableError, match="unavailable_authoritative_identity"):
        _compose(repository, tmp_path / "index")

    assert not (tmp_path / "index").exists()


def test_startup_rebuilds_missing_generation_before_service_is_exposed(
    tmp_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    _persist(repository, "memory-1")
    repository.mark_indexed(user_id="user-1", memory_id="memory-1")

    runtime = _compose(repository, tmp_path / "index")

    assert runtime.recovery.rebuilt is True
    assert runtime.recovery.vector_count == 1
    assert (tmp_path / "index" / "memory.faiss").is_file()


def test_second_startup_reaudits_and_reuses_generation_idempotently(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    _persist(repository, "memory-1")
    repository.mark_indexed(user_id="user-1", memory_id="memory-1")
    index = tmp_path / "index"
    first = _compose(repository, index)
    index_bytes = (index / "memory.faiss").read_bytes()
    metadata_bytes = (index / "memory.faiss.meta.json").read_bytes()

    second = _compose(SQLiteMemoryRepository(tmp_path / "memory.sqlite3"), index)

    assert first.recovery.rebuilt is True
    assert second.recovery.rebuilt is False
    assert second.recovery.reason == "ready_existing_generation"
    assert (index / "memory.faiss").read_bytes() == index_bytes
    assert (index / "memory.faiss.meta.json").read_bytes() == metadata_bytes
