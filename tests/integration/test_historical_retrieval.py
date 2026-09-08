from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conversational_memory.application import (
    Embedding,
    RequestContext,
    RetrievalIntent,
    RetrievalRequest,
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
from conversational_memory.entrypoints.cli import _demo_history, build_parser
from conversational_memory.infrastructure import FaissVectorIndex
from conversational_memory.infrastructure.sqlite import SQLiteMemoryRepository

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
EMBEDDING = Embedding(values=(1.0, 0.0), model_id="m5-test-model", dimension=2)


class MappingEmbedder:
    def embed(self, content: str) -> Embedding:
        if content == "vector database history":
            return EMBEDDING
        raise AssertionError(f"unexpected embedding input: {content}")


class FixedClock:
    def now(self) -> datetime:
        return NOW


class ExplodingClock:
    def now(self) -> datetime:
        raise AssertionError("historical retrieval must not consult the clock")


class CharacterCounter:
    tokenizer_id = "cl100k_base"

    @staticmethod
    def count_tokens(text: str) -> int:
        return len(text)


class UnusedMemoryIds:
    def new_id(self) -> str:
        raise AssertionError("retrieval must not allocate a memory ID")


def test_cli_exposes_the_m5_history_demo() -> None:
    assert build_parser().parse_args(["demo-history"]).command == "demo-history"
    assert callable(_demo_history)


def _memory(
    memory_id: str,
    *,
    user_id: str = "user-1",
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE,
    superseded_by: str | None = None,
    deleted_at: datetime | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        content=f"Memory {memory_id}",
        memory_type=MemoryType.PREFERENCE,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id=f"turn-{memory_id}",
        ),
        created_at=NOW,
        lifecycle_status=lifecycle_status,
        indexing_state=IndexingState.PENDING,
        subject="vector database",
        value=memory_id,
        valid_from=valid_from,
        valid_until=valid_until,
        superseded_by=superseded_by,
        deleted_at=deleted_at,
    )


def _persist(repository: SQLiteMemoryRepository, memory: MemoryRecord) -> int:
    return repository.persist_pending(
        memory=memory,
        embedding=EMBEDDING,
        idempotency_key=f"key-{memory.memory_id}",
        request_fingerprint=memory.memory_id,
    ).vector_id


def _persist_indexed(repository: SQLiteMemoryRepository, memory: MemoryRecord) -> int:
    vector_id = _persist(repository, memory)
    repository.mark_indexed(user_id=memory.user_id, memory_id=memory.memory_id)
    return vector_id


def test_historical_allowlist_applies_complete_owner_and_state_policy(
    tmp_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")

    superseded_id = _persist_indexed(repository, _memory("superseded"))
    replacement_id = _persist(repository, _memory("replacement"))
    repository.acknowledge_supersession(
        user_id="user-1",
        replacement_memory_id="replacement",
        target_memory_id="superseded",
    )
    active_id = _persist_indexed(
        repository,
        _memory(
            "active",
            valid_from=NOW + timedelta(days=1),
            valid_until=NOW + timedelta(days=2),
        ),
    )
    pending_id = _persist(repository, _memory("pending"))
    failed_id = _persist(repository, _memory("failed"))
    repository.mark_failed(
        user_id="user-1", memory_id="failed", reason="seeded failure"
    )
    deleted_id = _persist_indexed(
        repository,
        _memory("deleted", deleted_at=NOW - timedelta(seconds=1)),
    )
    inconsistent_active_id = _persist_indexed(
        repository,
        _memory("inconsistent-active", superseded_by="replacement"),
    )
    inconsistent_superseded_id = _persist_indexed(
        repository,
        _memory("inconsistent-superseded", lifecycle_status=LifecycleStatus.SUPERSEDED),
    )
    expired_id = _persist_indexed(
        repository,
        _memory("expired", lifecycle_status=LifecycleStatus.EXPIRED),
    )
    deleted_expired_id = _persist_indexed(
        repository,
        _memory(
            "deleted-expired",
            lifecycle_status=LifecycleStatus.EXPIRED,
            deleted_at=NOW - timedelta(seconds=1),
        ),
    )
    inconsistent_expired_id = _persist_indexed(
        repository,
        _memory(
            "inconsistent-expired",
            lifecycle_status=LifecycleStatus.EXPIRED,
            superseded_by="replacement",
        ),
    )
    other_owner_expired_id = _persist_indexed(
        repository,
        _memory(
            "other-owner-expired",
            user_id="user-2",
            lifecycle_status=LifecycleStatus.EXPIRED,
        ),
    )
    other_owner_id = _persist_indexed(
        repository,
        _memory("other-owner", user_id="user-2"),
    )

    assert repository.historical_vector_ids(user_id="user-1") == (
        superseded_id,
        replacement_id,
        active_id,
        expired_id,
    )
    assert {
        pending_id,
        failed_id,
        deleted_id,
        inconsistent_active_id,
        inconsistent_superseded_id,
        deleted_expired_id,
        inconsistent_expired_id,
        other_owner_expired_id,
        other_owner_id,
    }.isdisjoint(repository.historical_vector_ids(user_id="user-1"))

    assert repository.current_state_vector_ids(user_id="user-1", now=NOW) == (
        replacement_id,
    )


def test_historical_hydration_defensively_rechecks_authoritative_state(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    active_id = _persist_indexed(repository, _memory("active"))
    removed_id = _persist_indexed(repository, _memory("removed"))
    allowed = repository.historical_vector_ids(user_id="user-1")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE memories SET deleted_at = ? WHERE memory_id = ?",
            (NOW.isoformat(), "removed"),
        )

    hydrated = repository.hydrate_historical(
        user_id="user-1",
        vector_ids=allowed,
    )

    assert tuple(item.vector_id for item in hydrated) == (active_id,)
    assert removed_id in allowed


def test_historical_hydration_excludes_unrequested_and_other_owner_vectors(
    tmp_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    requested_id = _persist_indexed(repository, _memory("requested"))
    unrequested_id = _persist_indexed(repository, _memory("unrequested"))
    other_owner_id = _persist_indexed(
        repository,
        _memory("other-owner", user_id="user-2"),
    )

    hydrated = repository.hydrate_historical(
        user_id="user-1",
        vector_ids=(requested_id, other_owner_id),
    )

    assert tuple(item.vector_id for item in hydrated) == (requested_id,)
    assert unrequested_id not in tuple(item.vector_id for item in hydrated)


def test_service_dispatches_only_explicit_historical_intent_without_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=EMBEDDING.model_id,
        vector_dimension=EMBEDDING.dimension,
        create_if_missing=True,
    )

    original_id = _persist_indexed(repository, _memory("original"))
    vector_index.add(vector_id=original_id, embedding=EMBEDDING)
    replacement_id = _persist(repository, _memory("replacement"))
    vector_index.add(vector_id=replacement_id, embedding=EMBEDDING)
    repository.acknowledge_supersession(
        user_id="user-1",
        replacement_memory_id="replacement",
        target_memory_id="original",
    )
    deleted_id = _persist_indexed(
        repository,
        _memory("deleted", deleted_at=NOW - timedelta(seconds=1)),
    )
    vector_index.add(vector_id=deleted_id, embedding=EMBEDDING)
    other_owner_id = _persist_indexed(
        repository,
        _memory("other-owner", user_id="user-2"),
    )
    vector_index.add(vector_id=other_owner_id, embedding=EMBEDDING)

    service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=MappingEmbedder(),
        token_counter=CharacterCounter(),
        clock=FixedClock(),
        memory_ids=UnusedMemoryIds(),
        relevance_threshold=0.50,
    )
    request_values = {
        "query": "vector database history",
        "limit": 10,
        "token_budget": 1000,
    }
    with sqlite3.connect(database_path) as connection:
        before = connection.execute(
            """
            SELECT memory_id, lifecycle_status, supersedes_json, superseded_by,
                   deleted_at
            FROM memories ORDER BY memory_id
            """
        ).fetchall()

    default_current = service.retrieve(
        RequestContext(user_id="user-1", request_id="default-current"),
        RetrievalRequest(**request_values),
    )
    explicit_current = service.retrieve(
        RequestContext(user_id="user-1", request_id="explicit-current"),
        RetrievalRequest(**request_values, intent=RetrievalIntent.CURRENT),
    )
    historical = service.retrieve(
        RequestContext(user_id="user-1", request_id="historical"),
        RetrievalRequest(**request_values, intent=RetrievalIntent.HISTORICAL),
    )

    assert default_current.included_memory_ids == ("replacement",)
    assert explicit_current.included_memory_ids == default_current.included_memory_ids
    assert set(historical.included_memory_ids) == {"original", "replacement"}
    assert "deleted" not in historical.included_memory_ids
    assert "other-owner" not in historical.included_memory_ids
    assert {
        memory.memory.memory_id: memory.memory.lifecycle_status
        for memory in historical.memories
    } == {
        "original": LifecycleStatus.SUPERSEDED,
        "replacement": LifecycleStatus.ACTIVE,
    }

    with sqlite3.connect(database_path) as connection:
        after = connection.execute(
            """
            SELECT memory_id, lifecycle_status, supersedes_json, superseded_by,
                   deleted_at
            FROM memories ORDER BY memory_id
            """
        ).fetchall()
    assert after == before


def test_persisted_expired_memory_is_historical_only_clock_free_and_read_only(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=EMBEDDING.model_id,
        vector_dimension=EMBEDDING.dimension,
        create_if_missing=True,
    )
    memories = (
        _memory("expired", lifecycle_status=LifecycleStatus.EXPIRED),
        _memory(
            "deleted-expired",
            lifecycle_status=LifecycleStatus.EXPIRED,
            deleted_at=NOW - timedelta(seconds=1),
        ),
        _memory(
            "inconsistent-expired",
            lifecycle_status=LifecycleStatus.EXPIRED,
            superseded_by="replacement",
        ),
        _memory(
            "other-owner-expired",
            user_id="user-2",
            lifecycle_status=LifecycleStatus.EXPIRED,
        ),
    )
    for memory in memories:
        vector_id = _persist_indexed(repository, memory)
        vector_index.add(vector_id=vector_id, embedding=EMBEDDING)

    current_service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=MappingEmbedder(),
        token_counter=CharacterCounter(),
        clock=FixedClock(),
        memory_ids=UnusedMemoryIds(),
        relevance_threshold=0.50,
    )
    historical_service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=MappingEmbedder(),
        token_counter=CharacterCounter(),
        clock=ExplodingClock(),
        memory_ids=UnusedMemoryIds(),
        relevance_threshold=0.50,
    )
    request = RetrievalRequest(
        query="vector database history",
        limit=10,
        token_budget=1000,
    )

    current = current_service.retrieve(
        RequestContext(user_id="user-1", request_id="current-expired"),
        request,
    )
    with sqlite3.connect(database_path) as connection:
        before_history = connection.execute(
            """
            SELECT memory_id, lifecycle_status, supersedes_json, superseded_by,
                   deleted_at
            FROM memories ORDER BY memory_id
            """
        ).fetchall()

    historical = historical_service.retrieve(
        RequestContext(user_id="user-1", request_id="historical-expired"),
        RetrievalRequest(
            query=request.query,
            limit=request.limit,
            token_budget=request.token_budget,
            intent=RetrievalIntent.HISTORICAL,
        ),
    )

    assert current.included_memory_ids == ()
    assert historical.included_memory_ids == ("expired",)
    assert historical.memories[0].memory.lifecycle_status is LifecycleStatus.EXPIRED
    with sqlite3.connect(database_path) as connection:
        after_history = connection.execute(
            """
            SELECT memory_id, lifecycle_status, supersedes_json, superseded_by,
                   deleted_at
            FROM memories ORDER BY memory_id
            """
        ).fetchall()
    assert after_history == before_history
