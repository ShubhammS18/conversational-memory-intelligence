from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conversational_memory.application import AdmissionRequest, RequestContext, RetrievalRequest
from conversational_memory.composition import compose_memory_service
from conversational_memory.domain.context import serialize_memory_block
from conversational_memory.domain.models import IndexingState
from conversational_memory.infrastructure import (
    ALL_MPNET_BASE_V2_DIMENSION,
    ALL_MPNET_BASE_V2_MODEL_ID,
    FaissVectorIndex,
    SentenceTransformerEmbedder,
    SQLiteMemoryRepository,
    TiktokenTokenCounter,
)

NOW = datetime(2026, 9, 1, 14, 30, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequentialMemoryIds:
    def __init__(self) -> None:
        self._next = 1

    def new_id(self) -> str:
        memory_id = f"real-memory-{self._next}"
        self._next += 1
        return memory_id


def _request() -> AdmissionRequest:
    return AdmissionRequest(
        idempotency_key="real-turn-1",
        conversation_id="real-conversation-1",
        turn_id="real-turn-1",
        content="I prefer SQLite as the authoritative local memory store.",
        memory_type="preference",
        subject="authoritative memory store",
        value="SQLite",
        source_type="explicit_user",
    )


@pytest.mark.real_model
def test_real_model_restart_owner_scope_and_bounded_context(tmp_path: Path) -> None:
    cache_value = os.environ.get("CONVERSATIONAL_MEMORY_MODEL_CACHE")
    assert cache_value, "CONVERSATIONAL_MEMORY_MODEL_CACHE is required for the M1 real-model gate"
    cache_directory = Path(cache_value)
    assert cache_directory.is_dir(), "configured M1 model cache directory does not exist"

    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    embedder = SentenceTransformerEmbedder(cache_directory=cache_directory)
    token_counter = TiktokenTokenCounter()
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        index_directory,
        embedding_model=ALL_MPNET_BASE_V2_MODEL_ID,
        vector_dimension=ALL_MPNET_BASE_V2_DIMENSION,
        create_if_missing=True,
    )
    service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=embedder,
        token_counter=token_counter,
        clock=FixedClock(),
        memory_ids=SequentialMemoryIds(),
    )
    owner_context = RequestContext(user_id="user-1", request_id="real-admit-1")

    admitted = service.admit(owner_context, _request())
    replayed = service.admit(
        RequestContext(user_id="user-1", request_id="real-admit-replay"),
        _request(),
    )

    assert admitted.indexing_state is IndexingState.INDEXED
    assert admitted.retrievable is True
    assert replayed == admitted
    existing = repository.find(user_id="user-1", idempotency_key="real-turn-1")
    assert existing is not None and existing.indexing_work is not None
    vector_id = existing.indexing_work.vector_id
    with sqlite3.connect(database_path) as connection:
        stored_metadata = connection.execute(
            "SELECT embedding_model, embedding_dimension FROM memory_embeddings"
        ).fetchall()
        row_count = connection.execute("SELECT count(*) FROM memories").fetchone()[0]
    faiss_metadata = json.loads(
        (index_directory / "memory.faiss.meta.json").read_text(encoding="utf-8")
    )
    assert stored_metadata == [(ALL_MPNET_BASE_V2_MODEL_ID, ALL_MPNET_BASE_V2_DIMENSION)]
    assert row_count == 1
    assert faiss_metadata["embedding_model"] == ALL_MPNET_BASE_V2_MODEL_ID
    assert faiss_metadata["vector_dimension"] == ALL_MPNET_BASE_V2_DIMENSION
    assert faiss_metadata["vector_count"] == 1

    restarted_repository = SQLiteMemoryRepository(database_path)
    restarted_index = FaissVectorIndex(
        index_directory,
        embedding_model=ALL_MPNET_BASE_V2_MODEL_ID,
        vector_dimension=ALL_MPNET_BASE_V2_DIMENSION,
    )
    restarted_service = compose_memory_service(
        repository=restarted_repository,
        vector_index=restarted_index,
        embedder=embedder,
        token_counter=token_counter,
        clock=FixedClock(),
        memory_ids=SequentialMemoryIds(),
    )
    hydrated = restarted_repository.hydrate_indexed(
        user_id="user-1",
        vector_ids=(vector_id,),
    )
    assert len(hydrated) == 1
    expected_context = serialize_memory_block(hydrated[0].memory)
    exact_budget = token_counter.count_tokens(expected_context)

    retrieved = restarted_service.retrieve(
        RequestContext(user_id="user-1", request_id="real-retrieve-owner"),
        RetrievalRequest(
            query="Which local memory store do I prefer?",
            limit=5,
            token_budget=exact_budget,
        ),
    )
    other_user = restarted_service.retrieve(
        RequestContext(user_id="user-2", request_id="real-retrieve-other"),
        RetrievalRequest(
            query="Which local memory store does user-1 prefer?",
            limit=5,
            token_budget=exact_budget,
        ),
    )

    assert retrieved.included_memory_ids == (admitted.memory_id,)
    assert retrieved.context == expected_context
    assert retrieved.tokens_used == exact_budget
    assert retrieved.token_budget == exact_budget
    assert retrieved.exclusions == ()
    assert all(item.memory.user_id == "user-1" for item in retrieved.memories)
    assert other_user.memories == ()
    assert other_user.context == ""
    assert other_user.tokens_used == 0
