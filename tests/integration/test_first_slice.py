from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conversational_memory.application import AdmissionRequest, RequestContext
from conversational_memory.composition import compose_memory_service
from conversational_memory.domain.models import IndexingState
from conversational_memory.infrastructure import (
    ALL_MPNET_BASE_V2_DIMENSION,
    ALL_MPNET_BASE_V2_MODEL_ID,
    FaissVectorIndex,
    SentenceTransformerEmbedder,
    SQLiteMemoryRepository,
    TiktokenTokenCounter,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 1, 16, 0, tzinfo=UTC)


class FixedMemoryIds:
    def new_id(self) -> str:
        return "real-failure-memory"


@pytest.mark.real_model
def test_real_faiss_persistence_failure_is_failed_and_nonretrievable(tmp_path: Path) -> None:
    cache_value = os.environ.get("CONVERSATIONAL_MEMORY_MODEL_CACHE")
    assert cache_value, "CONVERSATIONAL_MEMORY_MODEL_CACHE is required for the M1 real-model gate"
    embedder = SentenceTransformerEmbedder(cache_directory=Path(cache_value))
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        index_directory,
        embedding_model=ALL_MPNET_BASE_V2_MODEL_ID,
        vector_dimension=ALL_MPNET_BASE_V2_DIMENSION,
        create_if_missing=True,
    )
    index_directory.rename(tmp_path / "index-unavailable")
    service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=embedder,
        token_counter=TiktokenTokenCounter(),
        clock=FixedClock(),
        memory_ids=FixedMemoryIds(),
    )

    result = service.admit(
        RequestContext(user_id="user-1", request_id="real-failure-admit"),
        AdmissionRequest(
            idempotency_key="real-failure-turn",
            conversation_id="real-failure-conversation",
            turn_id="real-failure-turn",
            content="I prefer SQLite for authoritative local memory storage.",
            memory_type="preference",
            subject="authoritative memory store",
            value="SQLite",
            source_type="explicit_user",
        ),
    )
    stored = repository.find(user_id="user-1", idempotency_key="real-failure-turn")

    assert result.indexing_state is IndexingState.FAILED
    assert result.retrievable is False
    assert result.retryable_error == "FAISS generation persistence failed"
    assert stored is not None
    assert stored.result == result
    assert repository.eligible_vector_ids(user_id="user-1") == ()
    assert not index_directory.exists()
