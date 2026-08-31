from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import faiss
import pytest

from conversational_memory.application import (
    AdmissionRequest,
    Embedding,
    IndexingError,
    MemoryService,
    RequestContext,
    StorageError,
    ValidationError,
)
from conversational_memory.composition import compose_memory_service
from conversational_memory.domain.models import AdmissionDecision, IndexingState
from conversational_memory.infrastructure import FaissVectorIndex, SQLiteMemoryRepository

MODEL = "test-model"
DIMENSION = 3
NOW = datetime(2026, 8, 31, 9, 30, tzinfo=UTC)
CONTEXT = RequestContext(user_id="user-1", request_id="request-1")


def _request(**changes: object) -> AdmissionRequest:
    values: dict[str, object] = {
        "idempotency_key": " turn-1 ",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "content": "I prefer FAISS.",
        "memory_type": "preference",
        "subject": "vector database",
        "value": "FAISS",
        "source_type": "explicit_user",
    }
    values.update(changes)
    return AdmissionRequest(**values)  # type: ignore[arg-type]


class DeterministicEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, content: str) -> Embedding:
        self.calls += 1
        assert content
        return Embedding(values=(0.25, -0.5, 0.75), model_id=MODEL, dimension=DIMENSION)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class DeterministicMemoryIds:
    def __init__(self) -> None:
        self.calls = 0

    def new_id(self) -> str:
        self.calls += 1
        return f"memory-{self.calls}"


class ObservingFaissVectorIndex(FaissVectorIndex):
    def __init__(self, index_directory: Path, database_path: Path) -> None:
        self._database_path_for_test = database_path
        self.observed_pending: list[tuple[int, int]] = []
        super().__init__(
            index_directory,
            embedding_model=MODEL,
            vector_dimension=DIMENSION,
            create_if_missing=True,
        )

    def add(self, *, vector_id: int, embedding: Embedding) -> None:
        with sqlite3.connect(self._database_path_for_test) as connection:
            row = connection.execute(
                """
                SELECT m.indexing_state, v.vector_id
                FROM memories AS m
                JOIN memory_vector_mappings AS v ON v.memory_id = m.memory_id
                """
            ).fetchone()
        assert row is not None
        assert row[0] == "pending"
        self.observed_pending.append((int(row[1]), vector_id))
        super().add(vector_id=vector_id, embedding=embedding)


class FailOnceFaissVectorIndex(FaissVectorIndex):
    def __init__(self, index_directory: Path) -> None:
        self.failures_remaining = 1
        super().__init__(
            index_directory,
            embedding_model=MODEL,
            vector_dimension=DIMENSION,
            create_if_missing=True,
        )

    def add(self, *, vector_id: int, embedding: Embedding) -> None:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise IndexingError("confirmed FAISS failure")
        super().add(vector_id=vector_id, embedding=embedding)


class FailFirstAcknowledgementRepository(SQLiteMemoryRepository):
    def __init__(self, database_path: Path) -> None:
        self.failures_remaining = 1
        super().__init__(database_path)

    def mark_indexed(self, *, user_id: str, memory_id: str) -> None:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise StorageError("simulated indexed acknowledgement failure")
        super().mark_indexed(user_id=user_id, memory_id=memory_id)


def _compose(
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    embedder: DeterministicEmbedder,
    memory_ids: DeterministicMemoryIds,
) -> MemoryService:
    return compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=embedder,
        clock=FixedClock(),
        memory_ids=memory_ids,
    )


def _database_counts(database_path: Path) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "memories",
                "admission_idempotency",
                "memory_embeddings",
                "memory_vector_mappings",
            )
        }


def _database_snapshot(database_path: Path) -> tuple[str, ...]:
    with sqlite3.connect(database_path) as connection:
        return tuple(connection.iterdump())


def _faiss_snapshot(index_directory: Path) -> tuple[bytes, bytes]:
    return (
        (index_directory / "memory.faiss").read_bytes(),
        (index_directory / "memory.faiss.meta.json").read_bytes(),
    )


def _faiss_ids(index_directory: Path) -> list[int]:
    index = faiss.read_index(str(index_directory / "memory.faiss"))
    return [int(value) for value in faiss.vector_to_array(index.id_map)]


def test_admission_persists_pending_before_same_stable_id_is_indexed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = ObservingFaissVectorIndex(index_directory, database_path)
    embedder = DeterministicEmbedder()
    memory_ids = DeterministicMemoryIds()
    service = _compose(repository, vector_index, embedder, memory_ids)

    result = service.admit(CONTEXT, _request())
    existing = repository.find(user_id="user-1", idempotency_key="turn-1")

    assert result.decision is AdmissionDecision.ACCEPTED
    assert result.indexing_state is IndexingState.INDEXED
    assert result.retrievable is True
    assert existing is not None
    assert existing.result == result
    assert existing.indexing_work is not None
    assert vector_index.observed_pending == [
        (existing.indexing_work.vector_id, existing.indexing_work.vector_id)
    ]
    assert _faiss_ids(index_directory) == [existing.indexing_work.vector_id]
    assert embedder.calls == 1
    assert memory_ids.calls == 1


def test_confirmed_faiss_failure_is_failed_and_nonretrievable_until_retry(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FailOnceFaissVectorIndex(index_directory)
    embedder = DeterministicEmbedder()
    memory_ids = DeterministicMemoryIds()
    service = _compose(repository, vector_index, embedder, memory_ids)
    empty_generation = _faiss_snapshot(index_directory)

    failed = service.admit(CONTEXT, _request())
    stored_failed = repository.find(user_id="user-1", idempotency_key="turn-1")

    assert failed.indexing_state is IndexingState.FAILED
    assert failed.retrievable is False
    assert stored_failed is not None
    assert stored_failed.result.indexing_state is IndexingState.FAILED
    assert stored_failed.result.retrievable is False
    assert stored_failed.indexing_work is not None
    assert _faiss_ids(index_directory) == []
    assert _faiss_snapshot(index_directory) == empty_generation

    retried = service.admit(CONTEXT, _request())
    stored_indexed = repository.find(user_id="user-1", idempotency_key="turn-1")

    assert retried.indexing_state is IndexingState.INDEXED
    assert retried.retrievable is True
    assert stored_indexed is not None
    assert stored_indexed.result.indexing_state is IndexingState.INDEXED
    assert stored_indexed.indexing_work is not None
    assert stored_indexed.indexing_work.vector_id == stored_failed.indexing_work.vector_id
    assert _faiss_ids(index_directory) == [stored_failed.indexing_work.vector_id]
    assert embedder.calls == 1
    assert memory_ids.calls == 1


def test_faiss_success_with_failed_sqlite_ack_retries_same_id_without_duplicate(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = FailFirstAcknowledgementRepository(database_path)
    vector_index = FaissVectorIndex(
        index_directory,
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )
    embedder = DeterministicEmbedder()
    memory_ids = DeterministicMemoryIds()
    service = _compose(repository, vector_index, embedder, memory_ids)

    pending = service.admit(CONTEXT, _request())
    stored_pending = repository.find(user_id="user-1", idempotency_key="turn-1")

    assert pending.indexing_state is IndexingState.PENDING
    assert pending.retrievable is False
    assert stored_pending is not None
    assert stored_pending.result.indexing_state is IndexingState.PENDING
    assert stored_pending.result.retrievable is False
    assert stored_pending.indexing_work is not None
    stable_vector_id = stored_pending.indexing_work.vector_id
    assert _faiss_ids(index_directory) == [stable_vector_id]

    indexed = service.admit(CONTEXT, _request())
    stored_indexed = repository.find(user_id="user-1", idempotency_key="turn-1")

    assert indexed.indexing_state is IndexingState.INDEXED
    assert indexed.retrievable is True
    assert stored_indexed is not None
    assert stored_indexed.result.indexing_state is IndexingState.INDEXED
    assert stored_indexed.indexing_work is not None
    assert stored_indexed.indexing_work.vector_id == stable_vector_id
    assert _faiss_ids(index_directory) == [stable_vector_id]
    assert embedder.calls == 1
    assert memory_ids.calls == 1


def test_indexed_idempotent_replay_returns_stored_result_without_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        index_directory,
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )
    embedder = DeterministicEmbedder()
    memory_ids = DeterministicMemoryIds()
    service = _compose(repository, vector_index, embedder, memory_ids)
    original = service.admit(CONTEXT, _request())
    database_before = _database_snapshot(database_path)
    faiss_before = _faiss_snapshot(index_directory)

    replayed = service.admit(
        RequestContext(user_id="user-1", request_id="request-2"),
        _request(),
    )

    assert replayed == original
    assert _database_snapshot(database_path) == database_before
    assert _faiss_snapshot(index_directory) == faiss_before
    assert embedder.calls == 1
    assert memory_ids.calls == 1


def test_idempotency_conflict_fails_before_sqlite_or_faiss_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        index_directory,
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )
    embedder = DeterministicEmbedder()
    memory_ids = DeterministicMemoryIds()
    service = _compose(repository, vector_index, embedder, memory_ids)
    service.admit(CONTEXT, _request())
    database_before = _database_snapshot(database_path)
    faiss_before = _faiss_snapshot(index_directory)

    with pytest.raises(ValidationError, match="idempotency_key_conflict"):
        service.admit(CONTEXT, _request(content="I prefer SQLite."))

    assert _database_snapshot(database_path) == database_before
    assert _faiss_snapshot(index_directory) == faiss_before
    assert embedder.calls == 1
    assert memory_ids.calls == 1


def test_credential_rejection_creates_no_sqlite_or_faiss_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        index_directory,
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )
    embedder = DeterministicEmbedder()
    memory_ids = DeterministicMemoryIds()
    service = _compose(repository, vector_index, embedder, memory_ids)
    empty_generation = _faiss_snapshot(index_directory)

    rejected = service.admit(CONTEXT, _request(content="password=hunter2"))

    assert rejected.decision is AdmissionDecision.REJECTED
    assert rejected.retrievable is False
    assert _database_counts(database_path) == {
        "memories": 0,
        "admission_idempotency": 0,
        "memory_embeddings": 0,
        "memory_vector_mappings": 0,
    }
    assert _faiss_ids(index_directory) == []
    assert _faiss_snapshot(index_directory) == empty_generation
    assert embedder.calls == 0
    assert memory_ids.calls == 0
