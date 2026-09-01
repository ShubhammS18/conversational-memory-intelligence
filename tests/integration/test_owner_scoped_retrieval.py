from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conversational_memory.application import (
    AdmissionRequest,
    AuthorizationError,
    Embedding,
    HydratedMemory,
    MemoryService,
    RequestContext,
    RetrievalRequest,
    ServiceUnavailableError,
    ValidationError,
    VectorSearchHit,
)
from conversational_memory.composition import compose_memory_service
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.infrastructure import FaissVectorIndex, SQLiteMemoryRepository

MODEL = "test-model"
DIMENSION = 2
NOW = datetime(2026, 9, 1, 9, 30, tzinfo=UTC)


class MappingEmbedder:
    def __init__(self, vectors: dict[str, tuple[float, float]]) -> None:
        self._vectors = vectors
        self.calls: list[str] = []

    def embed(self, content: str) -> Embedding:
        self.calls.append(content)
        return Embedding(values=self._vectors[content], model_id=MODEL, dimension=DIMENSION)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequentialMemoryIds:
    def __init__(self) -> None:
        self._next = 1

    def new_id(self) -> str:
        memory_id = f"memory-{self._next}"
        self._next += 1
        return memory_id


class SearchObservingFaissIndex(FaissVectorIndex):
    def __init__(self, directory: Path) -> None:
        self.search_calls = 0
        self.allowed_ids: list[tuple[int, ...]] = []
        super().__init__(
            directory,
            embedding_model=MODEL,
            vector_dimension=DIMENSION,
            create_if_missing=True,
        )

    def search(
        self,
        *,
        embedding: Embedding,
        allowed_vector_ids: tuple[int, ...],
        limit: int,
    ) -> tuple[VectorSearchHit, ...]:
        self.search_calls += 1
        self.allowed_ids.append(allowed_vector_ids)
        return super().search(
            embedding=embedding,
            allowed_vector_ids=allowed_vector_ids,
            limit=limit,
        )


class UnexpectedIdFaissIndex(SearchObservingFaissIndex):
    unexpected_vector_id: int | None = None

    def search(
        self,
        *,
        embedding: Embedding,
        allowed_vector_ids: tuple[int, ...],
        limit: int,
    ) -> tuple[VectorSearchHit, ...]:
        self.search_calls += 1
        self.allowed_ids.append(allowed_vector_ids)
        assert self.unexpected_vector_id is not None
        return (VectorSearchHit(vector_id=self.unexpected_vector_id, score=1.0),)


class NonIndexedBeforeHydrationRepository(SQLiteMemoryRepository):
    memory_id_to_exclude: str | None = None

    def __init__(self, database_path: Path) -> None:
        self._database_path_for_test = database_path
        super().__init__(database_path)

    def hydrate_indexed(
        self,
        *,
        user_id: str,
        vector_ids: tuple[int, ...],
    ) -> tuple[HydratedMemory, ...]:
        assert self.memory_id_to_exclude is not None
        with sqlite3.connect(self._database_path_for_test) as connection:
            connection.execute(
                """
                UPDATE memories
                SET indexing_state = 'failed', indexing_error = 'simulated concurrent change'
                WHERE user_id = ? AND memory_id = ?
                """,
                (user_id, self.memory_id_to_exclude),
            )
        return super().hydrate_indexed(user_id=user_id, vector_ids=vector_ids)


def _service(
    tmp_path: Path,
    *,
    embedder: MappingEmbedder,
    vector_index_type: type[SearchObservingFaissIndex] = SearchObservingFaissIndex,
    repository_type: type[SQLiteMemoryRepository] = SQLiteMemoryRepository,
) -> tuple[MemoryService, SQLiteMemoryRepository, SearchObservingFaissIndex]:
    repository = repository_type(tmp_path / "memory.sqlite3")
    vector_index = vector_index_type(tmp_path / "index")
    service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=embedder,
        clock=FixedClock(),
        memory_ids=SequentialMemoryIds(),
    )
    return service, repository, vector_index


def _admit(
    service: MemoryService,
    *,
    user_id: str,
    key: str,
    content: str,
) -> None:
    result = service.admit(
        RequestContext(user_id=user_id, request_id=f"admit-{key}"),
        AdmissionRequest(
            idempotency_key=key,
            conversation_id="conversation-1",
            turn_id=key,
            content=content,
            memory_type="preference",
            subject="database",
            value=content,
            source_type="explicit_user",
        ),
    )
    assert result.indexing_state is IndexingState.INDEXED


def _memory(memory_id: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id="user-1",
        content=content,
        memory_type=MemoryType.PREFERENCE,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id=memory_id,
        ),
        created_at=NOW,
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.PENDING,
        subject="database",
        value=content,
        valid_from=NOW,
    )


def _persist_with_state(
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    *,
    memory_id: str,
    content: str,
    embedding: Embedding,
    state: IndexingState,
) -> int:
    persisted = repository.persist_pending(
        memory=_memory(memory_id, content),
        embedding=embedding,
        idempotency_key=memory_id,
        request_fingerprint=memory_id,
    )
    vector_index.add(vector_id=persisted.vector_id, embedding=embedding)
    if state is IndexingState.INDEXED:
        repository.mark_indexed(user_id="user-1", memory_id=memory_id)
    elif state is IndexingState.FAILED:
        repository.mark_failed(
            user_id="user-1",
            memory_id=memory_id,
            reason="confirmed failure",
        )
    return persisted.vector_id


def test_retrieval_searches_only_trusted_owner_indexed_ids(tmp_path: Path) -> None:
    embedder = MappingEmbedder(
        {
            "owner memory": (0.5, 0.5),
            "other user's closer memory": (1.0, 0.0),
            "database query": (1.0, 0.0),
        }
    )
    service, repository, vector_index = _service(tmp_path, embedder=embedder)
    _admit(service, user_id="user-1", key="owner", content="owner memory")
    _admit(
        service,
        user_id="user-2",
        key="other",
        content="other user's closer memory",
    )
    owner = repository.find(user_id="user-1", idempotency_key="owner")
    other = repository.find(user_id="user-2", idempotency_key="other")
    assert owner is not None and owner.indexing_work is not None
    assert other is not None and other.indexing_work is not None

    result = service.retrieve(
        RequestContext(user_id="user-1", request_id="retrieve-1"),
        RetrievalRequest(query=" database query ", limit=5),
    )

    assert [item.memory.memory_id for item in result.memories] == [owner.result.memory_id]
    assert all(item.memory.user_id == "user-1" for item in result.memories)
    assert vector_index.allowed_ids == [(owner.indexing_work.vector_id,)]
    assert other.indexing_work.vector_id not in vector_index.allowed_ids[0]


def test_pending_and_failed_vectors_are_excluded_before_faiss_search(tmp_path: Path) -> None:
    embedder = MappingEmbedder({"query": (1.0, 0.0)})
    service, repository, vector_index = _service(tmp_path, embedder=embedder)
    indexed_id = _persist_with_state(
        repository,
        vector_index,
        memory_id="indexed-memory",
        content="indexed",
        embedding=Embedding(values=(0.5, 0.0), model_id=MODEL, dimension=DIMENSION),
        state=IndexingState.INDEXED,
    )
    pending_id = _persist_with_state(
        repository,
        vector_index,
        memory_id="pending-memory",
        content="pending",
        embedding=Embedding(values=(1.0, 0.0), model_id=MODEL, dimension=DIMENSION),
        state=IndexingState.PENDING,
    )
    failed_id = _persist_with_state(
        repository,
        vector_index,
        memory_id="failed-memory",
        content="failed",
        embedding=Embedding(values=(0.9, 0.0), model_id=MODEL, dimension=DIMENSION),
        state=IndexingState.FAILED,
    )

    result = service.retrieve(
        RequestContext(user_id="user-1", request_id="retrieve-1"),
        RetrievalRequest(query="query", limit=3),
    )

    assert [item.memory.memory_id for item in result.memories] == ["indexed-memory"]
    assert vector_index.allowed_ids == [(indexed_id,)]
    assert pending_id not in vector_index.allowed_ids[0]
    assert failed_id not in vector_index.allowed_ids[0]


def test_empty_owner_allowlist_skips_embedding_and_faiss_search(tmp_path: Path) -> None:
    embedder = MappingEmbedder({"query": (1.0, 0.0)})
    service, _, vector_index = _service(tmp_path, embedder=embedder)

    result = service.retrieve(
        RequestContext(user_id="user-with-no-memory", request_id="retrieve-empty"),
        RetrievalRequest(query="query", limit=5),
    )

    assert result.memories == ()
    assert embedder.calls == []
    assert vector_index.search_calls == 0


def test_unexpected_faiss_id_is_rejected_before_hydration(tmp_path: Path) -> None:
    embedder = MappingEmbedder(
        {
            "owner memory": (0.5, 0.5),
            "other memory": (1.0, 0.0),
            "query": (1.0, 0.0),
        }
    )
    service, repository, vector_index = _service(
        tmp_path,
        embedder=embedder,
        vector_index_type=UnexpectedIdFaissIndex,
    )
    _admit(service, user_id="user-1", key="owner", content="owner memory")
    _admit(service, user_id="user-2", key="other", content="other memory")
    owner = repository.find(user_id="user-1", idempotency_key="owner")
    other = repository.find(user_id="user-2", idempotency_key="other")
    assert owner is not None and owner.indexing_work is not None
    assert other is not None and other.indexing_work is not None
    assert isinstance(vector_index, UnexpectedIdFaissIndex)
    vector_index.unexpected_vector_id = other.indexing_work.vector_id

    with pytest.raises(AuthorizationError, match="unauthorized_retrieval_result"):
        service.retrieve(
            RequestContext(user_id="user-1", request_id="retrieve-1"),
            RetrievalRequest(query="query", limit=5),
        )

    assert vector_index.allowed_ids == [(owner.indexing_work.vector_id,)]


def test_wholly_missing_authorized_faiss_id_fails_closed(tmp_path: Path) -> None:
    embedder = MappingEmbedder({"query": (1.0, 0.0)})
    service, repository, _ = _service(tmp_path, embedder=embedder)
    persisted = repository.persist_pending(
        memory=_memory("missing-memory", "missing"),
        embedding=Embedding(values=(1.0, 0.0), model_id=MODEL, dimension=DIMENSION),
        idempotency_key="missing-memory",
        request_fingerprint="missing-memory",
    )
    repository.mark_indexed(user_id="user-1", memory_id=persisted.memory.memory_id)

    with pytest.raises(ServiceUnavailableError, match="missing an authorized vector ID"):
        service.retrieve(
            RequestContext(user_id="user-1", request_id="retrieve-missing"),
            RetrievalRequest(query="query", limit=5),
        )


def test_partly_missing_authorized_faiss_ids_fail_closed(tmp_path: Path) -> None:
    embedder = MappingEmbedder({"query": (1.0, 0.0)})
    service, repository, vector_index = _service(tmp_path, embedder=embedder)
    _persist_with_state(
        repository,
        vector_index,
        memory_id="present-memory",
        content="present",
        embedding=Embedding(values=(0.5, 0.0), model_id=MODEL, dimension=DIMENSION),
        state=IndexingState.INDEXED,
    )
    missing = repository.persist_pending(
        memory=_memory("missing-memory", "missing"),
        embedding=Embedding(values=(1.0, 0.0), model_id=MODEL, dimension=DIMENSION),
        idempotency_key="missing-memory",
        request_fingerprint="missing-memory",
    )
    repository.mark_indexed(user_id="user-1", memory_id=missing.memory.memory_id)

    with pytest.raises(ServiceUnavailableError, match="missing an authorized vector ID"):
        service.retrieve(
            RequestContext(user_id="user-1", request_id="retrieve-partial"),
            RetrievalRequest(query="query", limit=5),
        )


def test_memory_made_nonindexed_between_allowlist_and_hydration_is_rejected(
    tmp_path: Path,
) -> None:
    embedder = MappingEmbedder({"memory": (1.0, 0.0), "query": (1.0, 0.0)})
    service, repository, _ = _service(
        tmp_path,
        embedder=embedder,
        repository_type=NonIndexedBeforeHydrationRepository,
    )
    assert isinstance(repository, NonIndexedBeforeHydrationRepository)
    _admit(service, user_id="user-1", key="memory", content="memory")
    existing = repository.find(user_id="user-1", idempotency_key="memory")
    assert existing is not None and existing.result.memory_id is not None
    repository.memory_id_to_exclude = existing.result.memory_id

    with pytest.raises(AuthorizationError, match="unauthorized_retrieval_result"):
        service.retrieve(
            RequestContext(user_id="user-1", request_id="retrieve-race"),
            RetrievalRequest(query="query", limit=5),
        )


@pytest.mark.parametrize("query", ["", "   ", None, 7])
def test_invalid_query_is_rejected_before_embedding_or_search(
    tmp_path: Path,
    query: object,
) -> None:
    embedder = MappingEmbedder({})
    service, _, vector_index = _service(tmp_path, embedder=embedder)

    with pytest.raises(ValidationError, match="invalid_retrieval_query"):
        service.retrieve(
            RequestContext(user_id="user-1", request_id="retrieve-1"),
            RetrievalRequest(query=query, limit=5),  # type: ignore[arg-type]
        )

    assert embedder.calls == []
    assert vector_index.search_calls == 0


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5, "1", None])
def test_invalid_limit_is_rejected_before_embedding_or_search(
    tmp_path: Path,
    limit: object,
) -> None:
    embedder = MappingEmbedder({})
    service, _, vector_index = _service(tmp_path, embedder=embedder)

    with pytest.raises(ValidationError, match="invalid_retrieval_limit"):
        service.retrieve(
            RequestContext(user_id="user-1", request_id="retrieve-1"),
            RetrievalRequest(query="query", limit=limit),  # type: ignore[arg-type]
        )

    assert embedder.calls == []
    assert vector_index.search_calls == 0
