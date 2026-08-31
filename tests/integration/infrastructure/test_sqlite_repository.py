from __future__ import annotations

import sqlite3
import struct
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conversational_memory.application import Embedding, StorageError
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.infrastructure.sqlite import SQLiteMemoryRepository

NOW = datetime(2026, 8, 31, 7, 30, tzinfo=UTC)
EMBEDDING = Embedding(values=(0.25, -0.5), model_id="test-model", dimension=2)


def _memory(memory_id: str, user_id: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        content="I prefer FAISS.",
        memory_type=MemoryType.PREFERENCE,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id=f"turn-{memory_id}",
        ),
        created_at=NOW,
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.PENDING,
        subject="vector database",
        value={"choice": "FAISS"},
        valid_from=NOW,
    )


def _counts(database_path: Path) -> dict[str, int]:
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


def _persist(
    repository: SQLiteMemoryRepository,
    memory: MemoryRecord,
    *,
    key: str,
    fingerprint: str,
):
    return repository.persist_pending(
        memory=memory,
        embedding=EMBEDDING,
        idempotency_key=key,
        request_fingerprint=fingerprint,
    )


def test_empty_database_bootstraps_numbered_schema_and_persists_one_transaction(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)

    persisted = _persist(
        repository,
        _memory("memory-1", "user-1"),
        key="turn-1",
        fingerprint="a" * 64,
    )

    assert database_path.is_file()
    assert _counts(database_path) == {
        "memories": 1,
        "admission_idempotency": 1,
        "memory_embeddings": 1,
        "memory_vector_mappings": 1,
    }
    assert 0 < persisted.vector_id <= 2**63 - 1
    with sqlite3.connect(database_path) as connection:
        migration = connection.execute(
            "SELECT version, length(checksum) FROM schema_migrations"
        ).fetchone()
        embedding = connection.execute(
            """
            SELECT embedding_blob, embedding_model, embedding_dimension
            FROM memory_embeddings WHERE memory_id = 'memory-1'
            """
        ).fetchone()
    assert migration == (1, 64)
    assert embedding[1:] == ("test-model", 2)
    assert struct.unpack("<2f", embedding[0]) == pytest.approx(EMBEDDING.values)


def test_restart_preserves_idempotency_state_and_stable_vector_mapping(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    first = SQLiteMemoryRepository(database_path)
    persisted = _persist(
        first,
        _memory("memory-1", "user-1"),
        key="turn-1",
        fingerprint="b" * 64,
    )
    first.mark_indexed(user_id="user-1", memory_id="memory-1")

    restarted = SQLiteMemoryRepository(database_path)
    existing = restarted.find(user_id="user-1", idempotency_key="turn-1")
    with sqlite3.connect(database_path) as connection:
        vector_id = connection.execute(
            "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = 'memory-1'"
        ).fetchone()[0]

    assert existing is not None
    assert existing.request_fingerprint == "b" * 64
    assert existing.result.memory_id == "memory-1"
    assert existing.result.indexing_state is IndexingState.INDEXED
    assert existing.result.retrievable is True
    assert vector_id == persisted.vector_id


def test_duplicate_owner_key_rolls_back_every_record(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    _persist(
        repository,
        _memory("memory-1", "user-1"),
        key="same-key",
        fingerprint="c" * 64,
    )

    with pytest.raises(StorageError, match="pending-memory transaction failed"):
        _persist(
            repository,
            _memory("memory-2", "user-1"),
            key="same-key",
            fingerprint="d" * 64,
        )

    assert _counts(database_path) == {
        "memories": 1,
        "admission_idempotency": 1,
        "memory_embeddings": 1,
        "memory_vector_mappings": 1,
    }
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM memories WHERE memory_id = 'memory-2'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("memory", "embedding"),
    [
        (replace(_memory("memory-json", "user-1"), value=object()), EMBEDDING),
        (
            _memory("memory-float32", "user-1"),
            Embedding(values=(1e300,), model_id="test-model", dimension=1),
        ),
    ],
)
def test_serialization_failure_rolls_back_every_record(
    tmp_path: Path,
    memory: MemoryRecord,
    embedding: Embedding,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)

    with pytest.raises(StorageError, match="pending-memory serialization failed"):
        repository.persist_pending(
            memory=memory,
            embedding=embedding,
            idempotency_key="turn-serialization",
            request_fingerprint="9" * 64,
        )

    assert _counts(database_path) == {
        "memories": 0,
        "admission_idempotency": 0,
        "memory_embeddings": 0,
        "memory_vector_mappings": 0,
    }


def test_owner_scope_isolation_applies_to_lookup_and_state_changes(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    _persist(
        repository,
        _memory("memory-user-1", "user-1"),
        key="shared-key",
        fingerprint="e" * 64,
    )
    _persist(
        repository,
        _memory("memory-user-2", "user-2"),
        key="shared-key",
        fingerprint="f" * 64,
    )

    user_1 = repository.find(user_id="user-1", idempotency_key="shared-key")
    user_2 = repository.find(user_id="user-2", idempotency_key="shared-key")
    assert user_1 is not None
    assert user_2 is not None
    assert user_1.result.memory_id == "memory-user-1"
    assert user_2.result.memory_id == "memory-user-2"
    assert repository.find(user_id="user-3", idempotency_key="shared-key") is None
    with pytest.raises(StorageError, match="transition rejected"):
        repository.mark_indexed(user_id="user-2", memory_id="memory-user-1")
    unchanged = repository.find(user_id="user-1", idempotency_key="shared-key")
    assert unchanged is not None
    assert unchanged.result.indexing_state is IndexingState.PENDING


def test_pending_can_transition_to_indexed_but_not_transition_again(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    _persist(
        repository,
        _memory("memory-1", "user-1"),
        key="turn-1",
        fingerprint="1" * 64,
    )

    repository.mark_indexed(user_id="user-1", memory_id="memory-1")

    existing = repository.find(user_id="user-1", idempotency_key="turn-1")
    assert existing is not None
    assert existing.result.indexing_state is IndexingState.INDEXED
    assert existing.result.retrievable is True
    with pytest.raises(StorageError, match="transition rejected"):
        repository.mark_failed(user_id="user-1", memory_id="memory-1", reason="late failure")


def test_pending_can_transition_to_failed_and_remains_nonretrievable(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    _persist(
        repository,
        _memory("memory-1", "user-1"),
        key="turn-1",
        fingerprint="2" * 64,
    )

    repository.mark_failed(
        user_id="user-1",
        memory_id="memory-1",
        reason="durable index failed",
    )

    existing = repository.find(user_id="user-1", idempotency_key="turn-1")
    assert existing is not None
    assert existing.result.indexing_state is IndexingState.FAILED
    assert existing.result.retrievable is False
    assert existing.result.retryable_error == "durable index failed"
