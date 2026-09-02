from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest

from conversational_memory.application import (
    Embedding,
    IndexingError,
    MemoryService,
    StorageError,
    ValidationError,
)
from conversational_memory.composition import compose_memory_service
from conversational_memory.infrastructure import FaissVectorIndex, SQLiteMemoryRepository
from tests.integration.test_memory_service_sqlite_faiss import (
    CONTEXT,
    DIMENSION,
    MODEL,
    DeterministicEmbedder,
    DeterministicMemoryIds,
    FixedClock,
    _faiss_ids,
    _request,
    _scenario_idempotency_conflict_fails_before_sqlite_or_faiss_mutation,
    _scenario_indexed_idempotent_replay_returns_stored_result_without_mutation,
)


class UnusedTokenCounter:
    tokenizer_id = "cl100k_base"

    def count_tokens(self, text: str) -> int:
        raise AssertionError("admission must not count tokens")


def _compose_for_concurrency(
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    embedder: object,
    memory_ids: DeterministicMemoryIds,
) -> MemoryService:
    return compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=embedder,  # type: ignore[arg-type]
        token_counter=UnusedTokenCounter(),
        clock=FixedClock(),
        memory_ids=memory_ids,
    )


def test_indexed_idempotent_replay_returns_stored_result_without_mutation(
    tmp_path: Path,
) -> None:
    _scenario_indexed_idempotent_replay_returns_stored_result_without_mutation(tmp_path)


def test_idempotency_conflict_fails_before_sqlite_or_faiss_mutation(
    tmp_path: Path,
) -> None:
    _scenario_idempotency_conflict_fails_before_sqlite_or_faiss_mutation(tmp_path)


class CoordinatedEmbedder:
    """Hold the first embedding until a concurrent caller has started."""

    def __init__(self) -> None:
        self.calls = 0
        self.max_active = 0
        self._active = 0
        self._guard = Lock()
        self.first_entered = Event()
        self.second_entered = Event()
        self.release_first = Event()

    def embed(self, content: str) -> Embedding:
        with self._guard:
            self.calls += 1
            call = self.calls
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        if call == 1:
            self.first_entered.set()
            self.release_first.wait(timeout=5)
        else:
            self.second_entered.set()
        with self._guard:
            self._active -= 1
        return Embedding(values=(0.25, -0.5, 0.75), model_id=MODEL, dimension=DIMENSION)


class CountingFaissVectorIndex(FaissVectorIndex):
    def __init__(self, index_directory: Path) -> None:
        self.add_calls = 0
        super().__init__(
            index_directory,
            embedding_model=MODEL,
            vector_dimension=DIMENSION,
            create_if_missing=True,
        )

    def add(self, *, vector_id: int, embedding: Embedding) -> None:
        self.add_calls += 1
        super().add(vector_id=vector_id, embedding=embedding)


class CoordinatedRetryFaissVectorIndex(CountingFaissVectorIndex):
    def __init__(self, index_directory: Path) -> None:
        self._guard = Lock()
        self._active = 0
        self.max_active = 0
        self.retry_entered = Event()
        self.competing_entered = Event()
        self.release_retry = Event()
        super().__init__(index_directory)

    def add(self, *, vector_id: int, embedding: Embedding) -> None:
        with self._guard:
            self.add_calls += 1
            call = self.add_calls
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            if call == 1:
                raise IndexingError("simulated_initial_indexing_failure")
            if call == 2:
                self.retry_entered.set()
                self.release_retry.wait(timeout=5)
            else:
                self.competing_entered.set()
            FaissVectorIndex.add(self, vector_id=vector_id, embedding=embedding)
        finally:
            with self._guard:
                self._active -= 1


def _run_concurrent_admissions(
    service: object,
    embedder: CoordinatedEmbedder,
    second_request: object,
) -> list[object]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.admit, CONTEXT, _request())  # type: ignore[attr-defined]
        assert embedder.first_entered.wait(timeout=5)
        second_started = Event()

        def call_second() -> object:
            second_started.set()
            return service.admit(CONTEXT, second_request)  # type: ignore[attr-defined]

        second = executor.submit(call_second)
        assert second_started.wait(timeout=5)
        embedder.second_entered.wait(timeout=1)
        embedder.release_first.set()
        results: list[object] = []
        for future in (first, second):
            try:
                results.append(future.result(timeout=5))
            except Exception as error:  # noqa: BLE001 - assertions inspect public failures
                results.append(error)
        return results


def test_concurrent_identical_admissions_are_one_successful_workflow(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = CountingFaissVectorIndex(index_directory)
    embedder = CoordinatedEmbedder()
    memory_ids = DeterministicMemoryIds()
    service = _compose_for_concurrency(repository, vector_index, embedder, memory_ids)

    results = _run_concurrent_admissions(service, embedder, _request())

    assert not any(isinstance(result, Exception) for result in results)
    assert results[0] == results[1]
    assert embedder.calls == 1
    assert memory_ids.calls == 1
    assert vector_index.add_calls == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memories").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM memory_vector_mappings").fetchone() == (1,)
    assert len(_faiss_ids(index_directory)) == 1


def test_concurrent_conflicting_admission_reports_idempotency_conflict(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        tmp_path / "index", embedding_model=MODEL, vector_dimension=DIMENSION, create_if_missing=True
    )
    embedder = CoordinatedEmbedder()
    service = _compose_for_concurrency(
        repository, vector_index, embedder, DeterministicMemoryIds()
    )

    results = _run_concurrent_admissions(
        service, embedder, _request(content="I prefer SQLite.")
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(failures) == 1
    assert isinstance(failures[0], ValidationError)
    assert str(failures[0]) == "idempotency_key_conflict"
    assert not isinstance(failures[0], StorageError)
    assert embedder.calls == 1


def test_failed_admission_releases_process_write_lock(tmp_path: Path) -> None:
    class FailOnceEmbedder:
        def __init__(self) -> None:
            self.calls = 0

        def embed(self, content: str) -> Embedding:
            self.calls += 1
            if self.calls == 1:
                raise ValidationError("simulated_embedding_failure")
            return Embedding(
                values=(0.25, -0.5, 0.75), model_id=MODEL, dimension=DIMENSION
            )

    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    vector_index = FaissVectorIndex(
        tmp_path / "index", embedding_model=MODEL, vector_dimension=DIMENSION, create_if_missing=True
    )
    service = _compose_for_concurrency(
        repository, vector_index, FailOnceEmbedder(), DeterministicMemoryIds()
    )

    with pytest.raises(ValidationError, match="simulated_embedding_failure"):
        service.admit(CONTEXT, _request())
    assert service.admit(CONTEXT, _request()).retrievable is True


def test_retry_and_new_admission_share_process_write_serialization(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    vector_index = CoordinatedRetryFaissVectorIndex(tmp_path / "index")
    service = _compose_for_concurrency(
        repository, vector_index, DeterministicEmbedder(), DeterministicMemoryIds()
    )
    failed = service.admit(CONTEXT, _request())
    assert failed.retrievable is False

    with ThreadPoolExecutor(max_workers=2) as executor:
        retry = executor.submit(service.admit, CONTEXT, _request())
        assert vector_index.retry_entered.wait(timeout=5)
        competing_started = Event()

        def admit_new() -> object:
            competing_started.set()
            return service.admit(
                CONTEXT,
                _request(idempotency_key="turn-2", turn_id="turn-2", content="I prefer SQLite."),
            )

        admission = executor.submit(admit_new)
        assert competing_started.wait(timeout=5)
        vector_index.competing_entered.wait(timeout=1)
        vector_index.release_retry.set()
        assert retry.result(timeout=5).retrievable is True
        assert admission.result(timeout=5).retrievable is True

    assert vector_index.max_active == 1
    assert vector_index.add_calls == 3


def test_two_service_instances_preserve_each_others_published_vectors(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    first_repository = SQLiteMemoryRepository(database_path)
    first_index = FaissVectorIndex(
        index_directory, embedding_model=MODEL, vector_dimension=DIMENSION, create_if_missing=True
    )
    second_repository = SQLiteMemoryRepository(database_path)
    second_index = FaissVectorIndex(
        index_directory, embedding_model=MODEL, vector_dimension=DIMENSION
    )
    memory_ids = DeterministicMemoryIds()
    first = _compose_for_concurrency(
        first_repository, first_index, DeterministicEmbedder(), memory_ids
    )
    second = _compose_for_concurrency(
        second_repository, second_index, DeterministicEmbedder(), memory_ids
    )
    first_result = first.admit(CONTEXT, _request(idempotency_key="first", turn_id="first"))
    second_result = second.admit(
        CONTEXT, _request(idempotency_key="second", turn_id="second", content="I prefer SQLite.")
    )

    assert first_result.retrievable is True
    assert second_result.retrievable is True
    assert len(_faiss_ids(index_directory)) == 2
