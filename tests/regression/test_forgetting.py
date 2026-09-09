from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from conversational_memory.application import (
    Embedding,
    ForgetRequest,
    MemoryService,
    RequestContext,
    RetrievalIntent,
    RetrievalRequest,
)
from conversational_memory.domain.forgetting import ForgetOutcome
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.infrastructure import FaissVectorIndex
from conversational_memory.infrastructure.sqlite import SQLiteMemoryRepository

NOW = datetime(2026, 9, 9, 16, tzinfo=UTC)
EMBEDDING = Embedding(values=(1.0, 0.0), model_id="regression-model", dimension=2)


class _Embedder:
    def embed(self, content: str) -> Embedding:
        assert content
        return EMBEDDING


class _TokenCounter:
    tokenizer_id = "cl100k_base"

    @staticmethod
    def count_tokens(text: str) -> int:
        return len(text)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _UnusedMemoryIds:
    def new_id(self) -> str:
        raise AssertionError("forgetting and retrieval must not allocate IDs")


def _memory(memory_id: str, *, content: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id="user-1",
        content=content,
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
    )


def _seed(
    repository: SQLiteMemoryRepository,
    index: FaissVectorIndex,
    memory: MemoryRecord,
) -> int:
    persisted = repository.persist_pending(
        memory=memory,
        embedding=EMBEDDING,
        idempotency_key=f"key-{memory.memory_id}",
        request_fingerprint=hashlib.sha256(memory.memory_id.encode()).hexdigest(),
    )
    index.add(vector_id=persisted.vector_id, embedding=EMBEDDING)
    repository.mark_indexed(user_id=memory.user_id, memory_id=memory.memory_id)
    return persisted.vector_id


def _service(
    repository: SQLiteMemoryRepository,
    index: FaissVectorIndex,
) -> MemoryService:
    return MemoryService(
        idempotency=repository,
        embedder=_Embedder(),
        repository=repository,
        vector_index=index,
        token_counter=_TokenCounter(),
        clock=_Clock(),
        memory_ids=_UnusedMemoryIds(),
        relevance_threshold=0.50,
    )


def test_forgetting_survives_restart_without_reactivation_or_unrelated_loss(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = SQLiteMemoryRepository(database_path)
    index = FaissVectorIndex(
        index_directory,
        embedding_model="regression-model",
        vector_dimension=2,
        create_if_missing=True,
    )
    original_id = _seed(
        repository,
        index,
        _memory("original", content="I prefer FAISS."),
    )
    replacement_id = _seed(
        repository,
        index,
        _memory("replacement", content="I prefer PostgreSQL."),
    )
    unrelated_id = _seed(
        repository,
        index,
        _memory("unrelated", content="My favorite color is blue."),
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE memories
            SET lifecycle_status = 'superseded', superseded_by = 'replacement'
            WHERE memory_id = 'original'
            """
        )
        connection.execute(
            "UPDATE memories SET supersedes_json = '[\"original\"]' "
            "WHERE memory_id = 'replacement'"
        )
        connection.commit()

    result = _service(repository, index).forget(
        RequestContext(user_id="user-1", request_id="forget-replacement"),
        ForgetRequest(memory_id="replacement"),
    )

    assert result.outcome is ForgetOutcome.FORGOTTEN
    restarted_repository = SQLiteMemoryRepository(database_path)
    restarted_index = FaissVectorIndex(
        index_directory,
        embedding_model="regression-model",
        vector_dimension=2,
    )
    restarted = _service(restarted_repository, restarted_index)
    current = restarted.retrieve(
        RequestContext(user_id="user-1", request_id="current-after-forget"),
        RetrievalRequest(query="PostgreSQL", limit=10, token_budget=1000),
    )
    historical = restarted.retrieve(
        RequestContext(user_id="user-1", request_id="history-after-forget"),
        RetrievalRequest(
            query="PostgreSQL",
            limit=10,
            token_budget=1000,
            intent=RetrievalIntent.HISTORICAL,
        ),
    )

    assert "replacement" not in current.included_memory_ids
    assert "replacement" not in historical.included_memory_ids
    assert "original" not in current.included_memory_ids
    assert "unrelated" in current.included_memory_ids
    assert {
        hit.vector_id
        for hit in restarted_index.search(
            embedding=EMBEDDING,
            allowed_vector_ids=(original_id, unrelated_id),
            limit=10,
        )
    } == {original_id, unrelated_id}
    with sqlite3.connect(database_path) as connection:
        relationships = connection.execute(
            """
            SELECT memory_id, lifecycle_status, supersedes_json, superseded_by,
                   deleted_at
            FROM memories
            WHERE memory_id IN ('original', 'replacement')
            ORDER BY memory_id
            """
        ).fetchall()
        mappings = dict(
            connection.execute(
                "SELECT memory_id, vector_id FROM memory_vector_mappings"
            ).fetchall()
        )
        cleanup = connection.execute(
            """
            SELECT vector_id, cleanup_state, completed_at
            FROM memory_forgetting WHERE memory_id = 'replacement'
            """
        ).fetchone()

    assert relationships[0][:4] == ("original", "superseded", "[]", "replacement")
    assert relationships[0][4] is None
    assert relationships[1][0:4] == ("replacement", "active", '["original"]', None)
    assert relationships[1][4] is not None
    assert mappings["original"] == original_id
    assert mappings["unrelated"] == unrelated_id
    assert "replacement" not in mappings
    assert cleanup is not None
    assert cleanup[0] == replacement_id
    assert cleanup[1] == "complete"
    assert cleanup[2] is not None
