from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conversational_memory.application import (
    AuthorizationError,
    Embedding,
    HydratedMemory,
    RequestContext,
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
from conversational_memory.infrastructure import FaissVectorIndex, SQLiteMemoryRepository

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
MODEL = "m2-test-model"
DIMENSION = 2


class MappingEmbedder:
    def embed(self, content: str) -> Embedding:
        if content == "current-state query":
            return Embedding(values=(1.0, 0.0), model_id=MODEL, dimension=DIMENSION)
        raise AssertionError(f"unexpected embedding input: {content}")


class CountingClock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return NOW


class CharacterCounter:
    tokenizer_id = "cl100k_base"

    @staticmethod
    def count_tokens(text: str) -> int:
        return len(text)


class UnusedMemoryIds:
    def new_id(self) -> str:
        raise AssertionError("retrieval must not allocate a memory ID")


class TombstoneBeforeHydrationRepository(SQLiteMemoryRepository):
    def __init__(self, database_path: Path) -> None:
        self._test_database_path = database_path
        self.observed_times: list[datetime] = []
        super().__init__(database_path)

    def current_state_vector_ids(
        self,
        *,
        user_id: str,
        now: datetime,
    ) -> tuple[int, ...]:
        self.observed_times.append(now)
        return super().current_state_vector_ids(user_id=user_id, now=now)

    def hydrate_current_state(
        self,
        *,
        user_id: str,
        vector_ids: tuple[int, ...],
        now: datetime,
    ) -> tuple[HydratedMemory, ...]:
        self.observed_times.append(now)
        with sqlite3.connect(self._test_database_path) as connection:
            connection.execute(
                "UPDATE memories SET deleted_at = ? WHERE memory_id = 'current'",
                (NOW.isoformat(),),
            )
        return super().hydrate_current_state(
            user_id=user_id,
            vector_ids=vector_ids,
            now=now,
        )


def _memory(
    memory_id: str,
    *,
    user_id: str = "user-1",
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE,
    indexing_state: IndexingState = IndexingState.PENDING,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    superseded_by: str | None = None,
    deleted_at: datetime | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        content=memory_id,
        memory_type=MemoryType.PREFERENCE,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id=memory_id,
        ),
        created_at=NOW - timedelta(days=2),
        lifecycle_status=lifecycle_status,
        indexing_state=indexing_state,
        valid_from=valid_from if valid_from is not None else NOW - timedelta(days=1),
        valid_until=valid_until,
        superseded_by=superseded_by,
        deleted_at=deleted_at,
    )


def _seed(
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    memory: MemoryRecord,
    *,
    score: float,
    final_state: IndexingState,
) -> int:
    embedding = Embedding(values=(score, 0.0), model_id=MODEL, dimension=DIMENSION)
    persisted = repository.persist_pending(
        memory=memory,
        embedding=embedding,
        idempotency_key=memory.memory_id,
        request_fingerprint=memory.memory_id,
    )
    vector_index.add(vector_id=persisted.vector_id, embedding=embedding)
    if final_state is IndexingState.INDEXED:
        repository.mark_indexed(user_id=memory.user_id, memory_id=memory.memory_id)
    elif final_state is IndexingState.FAILED:
        repository.mark_failed(
            user_id=memory.user_id,
            memory_id=memory.memory_id,
            reason="seeded failure",
        )
    return persisted.vector_id


def test_seeded_ineligible_states_never_enter_current_state_results(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )
    states = [
        (_memory("current"), IndexingState.INDEXED, 0.1),
        (_memory("pending"), IndexingState.PENDING, 1.0),
        (_memory("failed"), IndexingState.FAILED, 0.99),
        (
            _memory("expired", lifecycle_status=LifecycleStatus.EXPIRED),
            IndexingState.INDEXED,
            0.98,
        ),
        (
            _memory(
                "superseded",
                lifecycle_status=LifecycleStatus.SUPERSEDED,
                superseded_by="replacement",
            ),
            IndexingState.INDEXED,
            0.97,
        ),
        (
            _memory("deleted", deleted_at=NOW - timedelta(seconds=1)),
            IndexingState.INDEXED,
            0.96,
        ),
        (
            _memory("invalid-start", valid_from=NOW + timedelta(microseconds=1)),
            IndexingState.INDEXED,
            0.95,
        ),
        (
            _memory("ended", valid_until=NOW),
            IndexingState.INDEXED,
            0.94,
        ),
        (
            _memory("other-owner", user_id="user-2"),
            IndexingState.INDEXED,
            0.93,
        ),
    ]
    vector_ids = {
        memory.memory_id: _seed(
            repository,
            vector_index,
            memory,
            score=score,
            final_state=state,
        )
        for memory, state, score in states
    }
    clock = CountingClock()
    service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=MappingEmbedder(),
        token_counter=CharacterCounter(),
        clock=clock,
        memory_ids=UnusedMemoryIds(),
    )

    result = service.retrieve(
        RequestContext(user_id="user-1", request_id="retrieve-current"),
        RetrievalRequest(query="current-state query", limit=20, token_budget=1000),
    )

    assert result.included_memory_ids == ("current",)
    assert repository.current_state_vector_ids(user_id="user-1", now=NOW) == (
        vector_ids["current"],
    )
    assert clock.calls == 1
    with sqlite3.connect(database_path) as connection:
        assert [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ] == [1, 2]
        assert connection.execute(
            "SELECT deleted_at FROM memories WHERE memory_id = 'deleted'"
        ).fetchone()[0] is not None


def test_hydration_rechecks_eligibility_with_the_same_trusted_now(tmp_path: Path) -> None:
    repository = TombstoneBeforeHydrationRepository(tmp_path / "memory.sqlite3")
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )
    _seed(
        repository,
        vector_index,
        _memory("current"),
        score=1.0,
        final_state=IndexingState.INDEXED,
    )
    clock = CountingClock()
    service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=MappingEmbedder(),
        token_counter=CharacterCounter(),
        clock=clock,
        memory_ids=UnusedMemoryIds(),
    )

    with pytest.raises(AuthorizationError, match="unauthorized_retrieval_result"):
        service.retrieve(
            RequestContext(user_id="user-1", request_id="retrieve-current"),
            RetrievalRequest(query="current-state query", limit=5, token_budget=1000),
        )

    assert repository.observed_times == [NOW, NOW]
    assert clock.calls == 1
