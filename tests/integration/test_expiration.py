from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from conversational_memory import composition
from conversational_memory.application import (
    AdmissionRequest,
    ConfigurationError,
    Embedding,
    ExistingAdmission,
    HydratedMemory,
    MemoryService,
    RequestContext,
    RetrievalIntent,
    RetrievalOutcome,
    RetrievalRequest,
    StorageError,
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
from conversational_memory.entrypoints.cli import _demo_expiration, build_parser
from conversational_memory.infrastructure import (
    ALL_MPNET_BASE_V2_DIMENSION,
    ALL_MPNET_BASE_V2_MODEL_ID,
    FaissVectorIndex,
    SQLiteMemoryRepository,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
MODEL = "m6-test-model"
DIMENSION = 2
EMBEDDING = Embedding(values=(1.0, 0.0), model_id=MODEL, dimension=DIMENSION)


class _DemoEmbedder:
    def embed(self, content: str) -> Embedding:
        del content
        return Embedding(
            values=(1.0,) + (0.0,) * (ALL_MPNET_BASE_V2_DIMENSION - 1),
            model_id=ALL_MPNET_BASE_V2_MODEL_ID,
            dimension=ALL_MPNET_BASE_V2_DIMENSION,
        )


class _Embedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, content: str) -> Embedding:
        del content
        self.calls += 1
        return EMBEDDING


class _TokenCounter:
    tokenizer_id = "cl100k_base"

    @staticmethod
    def count_tokens(text: str) -> int:
        return len(text)


def test_cli_exposes_the_m6_expiration_demo() -> None:
    assert build_parser().parse_args(["demo-expiration"]).command == "demo-expiration"
    assert callable(_demo_expiration)


class _UnusedMemoryIds:
    def new_id(self) -> str:
        raise AssertionError("retrieval must not allocate a memory ID")


class _FixedMemoryIds:
    def new_id(self) -> str:
        return "valid-dst-memory"


class _Clock:
    def __init__(self, value: object = NOW) -> None:
        self.value = value
        self.calls = 0

    def now(self) -> object:
        self.calls += 1
        return self.value


class _ExplodingClock:
    def now(self) -> datetime:
        raise AssertionError("historical retrieval must not consult the clock")


class _ObservingRepository(SQLiteMemoryRepository):
    def __init__(self, database_path: Path) -> None:
        self.events: list[tuple[str, datetime]] = []
        super().__init__(database_path)

    def expire_current_memories(self, *, user_id: str, now: datetime) -> int:
        self.events.append(("expire", now))
        return super().expire_current_memories(user_id=user_id, now=now)

    def current_state_vector_ids(
        self, *, user_id: str, now: datetime
    ) -> tuple[int, ...]:
        self.events.append(("allowlist", now))
        return super().current_state_vector_ids(user_id=user_id, now=now)

    def hydrate_current_state(
        self,
        *,
        user_id: str,
        vector_ids: tuple[int, ...],
        now: datetime,
    ) -> tuple[HydratedMemory, ...]:
        self.events.append(("hydrate", now))
        return super().hydrate_current_state(
            user_id=user_id,
            vector_ids=vector_ids,
            now=now,
        )


class _FailingExpirationRepository(SQLiteMemoryRepository):
    def expire_current_memories(self, *, user_id: str, now: datetime) -> int:
        del user_id, now
        raise StorageError("forced expiration transition failure")


class _SearchObservingIndex(FaissVectorIndex):
    def __init__(self, index_directory: Path) -> None:
        self.add_calls = 0
        self.search_calls = 0
        super().__init__(
            index_directory,
            embedding_model=MODEL,
            vector_dimension=DIMENSION,
            create_if_missing=True,
        )

    def add(self, *, vector_id: int, embedding: Embedding) -> None:
        self.add_calls += 1
        super().add(vector_id=vector_id, embedding=embedding)

    def search(
        self,
        *,
        embedding: Embedding,
        allowed_vector_ids: tuple[int, ...],
        limit: int,
    ) -> tuple[VectorSearchHit, ...]:
        self.search_calls += 1
        return super().search(
            embedding=embedding,
            allowed_vector_ids=allowed_vector_ids,
            limit=limit,
        )


class _AdmissionObservingRepository(SQLiteMemoryRepository):
    def __init__(self, database_path: Path) -> None:
        self.admission_calls: list[str] = []
        super().__init__(database_path)

    def find(
        self, *, user_id: str, idempotency_key: str
    ) -> ExistingAdmission | None:
        self.admission_calls.append("find")
        return super().find(user_id=user_id, idempotency_key=idempotency_key)


def _memory(
    memory_id: str,
    *,
    user_id: str = "user-1",
    valid_until: datetime | None = NOW,
    supersedes: tuple[str, ...] = (),
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        content=f"Memory {memory_id}",
        memory_type=MemoryType.FACT,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id=memory_id,
        ),
        created_at=NOW - timedelta(days=2),
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.PENDING,
        valid_from=NOW - timedelta(days=1),
        valid_until=valid_until,
        supersedes=supersedes,
    )


def _persist(
    repository: SQLiteMemoryRepository,
    memory: MemoryRecord,
    *,
    state: IndexingState = IndexingState.INDEXED,
) -> int:
    persisted = repository.persist_pending(
        memory=memory,
        embedding=EMBEDDING,
        idempotency_key=memory.memory_id,
        request_fingerprint=memory.memory_id,
    )
    if state is IndexingState.INDEXED:
        repository.mark_indexed(user_id=memory.user_id, memory_id=memory.memory_id)
    elif state is IndexingState.FAILED:
        repository.mark_failed(
            user_id=memory.user_id,
            memory_id=memory.memory_id,
            reason="seeded failure",
        )
    return persisted.vector_id


def _states(database_path: Path) -> dict[str, tuple[str, str | None, str]]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT memory_id, lifecycle_status, superseded_by, supersedes_json
            FROM memories ORDER BY memory_id
            """
        ).fetchall()
    return {str(row[0]): (str(row[1]), row[2], str(row[3])) for row in rows}


def _file_hashes(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def _service(
    *,
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    clock: object,
    embedder: _Embedder | None = None,
) -> tuple[MemoryService, _Embedder]:
    selected_embedder = embedder or _Embedder()
    return (
        compose_memory_service(
            repository=repository,
            vector_index=vector_index,
            embedder=selected_embedder,
            token_counter=_TokenCounter(),
            clock=clock,  # type: ignore[arg-type]
            memory_ids=_UnusedMemoryIds(),
            relevance_threshold=0.50,
        ),
        selected_embedder,
    )


def test_expiration_transition_uses_exact_boundary_and_is_idempotent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    _persist(repository, _memory("before", valid_until=NOW + timedelta(microseconds=1)))
    _persist(repository, _memory("exact", valid_until=NOW))
    _persist(repository, _memory("past", valid_until=NOW - timedelta(microseconds=1)))
    _persist(repository, _memory("open", valid_until=None))

    assert repository.expire_current_memories(user_id="user-1", now=NOW) == 2
    assert repository.expire_current_memories(user_id="user-1", now=NOW) == 0

    states = _states(database_path)
    assert states["before"][0] == "active"
    assert states["exact"][0] == "expired"
    assert states["past"][0] == "expired"
    assert states["open"][0] == "active"


def test_expiration_transition_is_owner_scoped_and_skips_ineligible_states(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    _persist(repository, _memory("eligible"))
    _persist(repository, _memory("other-owner", user_id="user-2"))
    _persist(repository, _memory("pending"), state=IndexingState.PENDING)
    _persist(repository, _memory("failed"), state=IndexingState.FAILED)
    _persist(repository, _memory("deleted"))
    _persist(repository, _memory("superseded"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE memories SET deleted_at = ? WHERE memory_id = 'deleted'",
            ((NOW - timedelta(hours=1)).isoformat(),),
        )
        connection.execute(
            """
            UPDATE memories
            SET lifecycle_status = 'superseded', superseded_by = 'replacement'
            WHERE memory_id = 'superseded'
            """
        )

    assert repository.expire_current_memories(user_id="user-1", now=NOW) == 1

    states = _states(database_path)
    assert states["eligible"][0] == "expired"
    assert states["other-owner"][0] == "active"
    assert states["pending"][0] == "active"
    assert states["failed"][0] == "active"
    assert states["deleted"][0] == "active"
    assert states["superseded"][:2] == ("superseded", "replacement")


def test_expiration_preserves_relationships_mappings_and_faiss_generation(
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
    vector_id = _persist(
        repository,
        _memory("replacement", supersedes=("original",)),
    )
    vector_index.add(vector_id=vector_id, embedding=EMBEDDING)
    before_hashes = _file_hashes(index_directory)

    assert repository.expire_current_memories(user_id="user-1", now=NOW) == 1

    assert _states(database_path)["replacement"] == (
        "expired",
        None,
        '["original"]',
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = 'replacement'"
        ).fetchone() == (vector_id,)
    assert _file_hashes(index_directory) == before_hashes
    assert vector_index.search(
        embedding=EMBEDDING,
        allowed_vector_ids=(vector_id,),
        limit=1,
    )[0].vector_id == vector_id


def test_expiration_repository_failure_rolls_back_and_fails_closed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    _persist(repository, _memory("first"))
    _persist(repository, _memory("second"))
    before = _states(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_expiration
            BEFORE UPDATE OF lifecycle_status ON memories
            WHEN OLD.memory_id = 'second' AND NEW.lifecycle_status = 'expired'
            BEGIN
                SELECT RAISE(ABORT, 'forced expiration failure');
            END
            """
        )

    with pytest.raises(StorageError, match="expiration transition failed"):
        repository.expire_current_memories(user_id="user-1", now=NOW)

    assert _states(database_path) == before


@pytest.mark.parametrize(
    ("valid_until", "expected_outcome", "expected_state"),
    [
        (
            NOW + timedelta(microseconds=1),
            RetrievalOutcome.MEMORIES_SELECTED,
            "active",
        ),
        (NOW, RetrievalOutcome.NO_ELIGIBLE_MEMORY, "expired"),
        (
            NOW - timedelta(microseconds=1),
            RetrievalOutcome.NO_ELIGIBLE_MEMORY,
            "expired",
        ),
    ],
    ids=("before", "exactly-at", "after"),
)
def test_current_retrieval_transitions_at_the_exact_valid_until_boundary(
    tmp_path: Path,
    valid_until: datetime,
    expected_outcome: RetrievalOutcome,
    expected_state: str,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )
    vector_id = _persist(repository, _memory("bounded", valid_until=valid_until))
    vector_index.add(vector_id=vector_id, embedding=EMBEDDING)
    service, _ = _service(
        repository=repository,
        vector_index=vector_index,
        clock=_Clock(),
    )

    result = service.retrieve(
        RequestContext(user_id="user-1", request_id="current-boundary"),
        RetrievalRequest(query="bounded", limit=5, token_budget=1000),
    )

    assert result.outcome is expected_outcome
    assert _states(database_path)["bounded"][0] == expected_state


def test_current_retrieval_uses_one_clock_value_for_transition_and_both_reads(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = _ObservingRepository(database_path)
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )
    vector_id = _persist(
        repository,
        _memory("current", valid_until=NOW + timedelta(days=1)),
    )
    vector_index.add(vector_id=vector_id, embedding=EMBEDDING)
    clock = _Clock()
    service, _ = _service(
        repository=repository,
        vector_index=vector_index,
        clock=clock,
    )

    result = service.retrieve(
        RequestContext(user_id="user-1", request_id="one-clock"),
        RetrievalRequest(query="current", limit=5, token_budget=1000),
    )

    assert result.included_memory_ids == ("current",)
    assert clock.calls == 1
    assert [event for event, _ in repository.events] == [
        "expire",
        "allowlist",
        "hydrate",
    ]
    assert all(observed is NOW for _, observed in repository.events)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "2026-09-08T12:00:00Z",
        datetime(2026, 9, 8, 12),  # noqa: DTZ001 - intentionally invalid input
        datetime(2026, 9, 8, 12, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_invalid_clock_fails_closed_before_repository_or_faiss(
    tmp_path: Path,
    value: object,
) -> None:
    repository = _ObservingRepository(tmp_path / "memory.sqlite3")
    vector_index = _SearchObservingIndex(tmp_path / "index")
    embedder = _Embedder()
    service, _ = _service(
        repository=repository,
        vector_index=vector_index,
        clock=_Clock(value),
        embedder=embedder,
    )

    with pytest.raises(ConfigurationError, match="^invalid_trusted_clock$"):
        service.retrieve(
            RequestContext(user_id="user-1", request_id="invalid-clock"),
            RetrievalRequest(query="query", limit=5, token_budget=1000),
        )

    assert repository.events == []
    assert embedder.calls == 0
    assert vector_index.search_calls == 0


def test_expiration_transition_failure_prevents_embedding_and_faiss_search(
    tmp_path: Path,
) -> None:
    repository = _FailingExpirationRepository(tmp_path / "memory.sqlite3")
    vector_index = _SearchObservingIndex(tmp_path / "index")
    embedder = _Embedder()
    service, _ = _service(
        repository=repository,
        vector_index=vector_index,
        clock=_Clock(),
        embedder=embedder,
    )

    with pytest.raises(StorageError, match="forced expiration transition failure"):
        service.retrieve(
            RequestContext(user_id="user-1", request_id="transition-failure"),
            RetrievalRequest(query="query", limit=5, token_budget=1000),
        )

    assert embedder.calls == 0
    assert vector_index.search_calls == 0


def test_historical_retrieval_is_clock_free_and_does_not_expire_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = _ObservingRepository(database_path)
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )
    vector_id = _persist(
        repository,
        _memory("elapsed", valid_until=NOW - timedelta(days=1)),
    )
    vector_index.add(vector_id=vector_id, embedding=EMBEDDING)
    service, _ = _service(
        repository=repository,
        vector_index=vector_index,
        clock=_ExplodingClock(),
    )

    result = service.retrieve(
        RequestContext(user_id="user-1", request_id="history"),
        RetrievalRequest(
            query="elapsed",
            limit=5,
            token_budget=1000,
            intent=RetrievalIntent.HISTORICAL,
        ),
    )

    assert result.included_memory_ids == ("elapsed",)
    assert repository.events == []
    assert _states(database_path)["elapsed"][0] == "active"


def test_dst_fold_reversed_interval_fails_before_any_admission_effect(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = _AdmissionObservingRepository(database_path)
    vector_index = _SearchObservingIndex(index_directory)
    embedder = _Embedder()
    clock = _Clock()
    service, _ = _service(
        repository=repository,
        vector_index=vector_index,
        clock=clock,
        embedder=embedder,
    )
    faiss_before = _file_hashes(index_directory)
    new_york = ZoneInfo("America/New_York")
    valid_from = datetime(2024, 11, 3, 1, 30, tzinfo=new_york, fold=1)
    valid_until = datetime(2024, 11, 3, 1, 45, tzinfo=new_york, fold=0)
    assert valid_from.astimezone(UTC) > valid_until.astimezone(UTC)

    with pytest.raises(ValidationError, match="^invalid_admission_request$") as error:
        service.admit(
            RequestContext(user_id="user-1", request_id="invalid-interval"),
            AdmissionRequest(
                idempotency_key="invalid-interval",
                conversation_id="conversation-1",
                turn_id="turn-1",
                content="This interval is invalid.",
                memory_type="fact",
                subject="interval",
                value="invalid",
                source_type="explicit_user",
                valid_from=valid_from,
                valid_until=valid_until,
            ),
        )

    assert error.value.reason == "invalid_admission_request"
    assert repository.admission_calls == []
    assert clock.calls == 0
    assert embedder.calls == 0
    assert vector_index.add_calls == 0
    assert vector_index.search_calls == 0
    assert _file_hashes(index_directory) == faiss_before
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memories").fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_vector_mappings"
        ).fetchone() == (0,)


def test_dst_fold_valid_interval_admits_with_utc_instant_semantics(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = _AdmissionObservingRepository(database_path)
    vector_index = _SearchObservingIndex(tmp_path / "index")
    embedder = _Embedder()
    clock = _Clock()
    service = compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=embedder,
        token_counter=_TokenCounter(),
        clock=clock,  # type: ignore[arg-type]
        memory_ids=_FixedMemoryIds(),
        relevance_threshold=0.50,
    )
    new_york = ZoneInfo("America/New_York")
    valid_from = datetime(2024, 11, 3, 1, 45, tzinfo=new_york, fold=0)
    valid_until = datetime(2024, 11, 3, 1, 30, tzinfo=new_york, fold=1)
    assert valid_from.astimezone(UTC) < valid_until.astimezone(UTC)

    result = service.admit(
        RequestContext(user_id="user-1", request_id="valid-dst-interval"),
        AdmissionRequest(
            idempotency_key="valid-dst-interval",
            conversation_id="conversation-1",
            turn_id="turn-valid-dst",
            content="This interval crosses the DST fallback safely.",
            memory_type="fact",
            subject="interval",
            value="valid",
            source_type="explicit_user",
            valid_from=valid_from,
            valid_until=valid_until,
        ),
    )

    assert result.retrievable is True
    assert result.memory_id == "valid-dst-memory"
    assert repository.admission_calls == ["find"]
    assert clock.calls == 1
    assert embedder.calls == 1
    assert vector_index.add_calls == 1
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT valid_from, valid_until FROM memories
            WHERE memory_id = 'valid-dst-memory'
            """
        ).fetchone()
        assert row == (
            "2024-11-03T05:45:00.000000Z",
            "2024-11-03T06:30:00.000000Z",
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_vector_mappings"
        ).fetchone() == (1,)


def test_demo_expiration_prints_verified_persisted_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        composition,
        "SentenceTransformerEmbedder",
        lambda *, cache_directory: _DemoEmbedder(),
    )
    monkeypatch.setattr(
        composition,
        "TiktokenTokenCounter",
        lambda: _TokenCounter(),
    )

    assert _demo_expiration() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["boundary"] == {
        "before_current": ["m6-expiring-memory"],
        "at_current": [],
        "after_restart_current": [],
        "persisted_lifecycle_status": "expired",
    }
    assert output["preservation"] == {
        "faiss_generation_unchanged": True,
        "replacement_supersedes": ["m6-original-memory"],
        "original_superseded_by": "m6-expiring-memory",
        "relationships_unchanged": True,
    }
    assert output["historical"]["expired_memory_returned"] is True
    assert output["historical"]["clock_read"] is False
    assert output["historical"]["state_unchanged"] is True
    assert output["historical"]["remained_expired"] is True
    assert output["historical"]["tokens_used"] <= output["historical"]["token_budget"]
