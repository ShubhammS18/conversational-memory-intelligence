from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from conversational_memory import composition
from conversational_memory.application import (
    AdmissionRequest,
    ClockPort,
    ConfigurationError,
    Embedding,
    ForgetRequest,
    IndexingError,
    MemoryService,
    RequestContext,
    RetrievalIntent,
    RetrievalOutcome,
    RetrievalRequest,
    StorageError,
)
from conversational_memory.domain.forgetting import ForgetOutcome, ForgettingCleanupState
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.entrypoints.cli import _demo_forgetting, build_parser
from conversational_memory.infrastructure import (
    ALL_MPNET_BASE_V2_DIMENSION,
    ALL_MPNET_BASE_V2_MODEL_ID,
    FaissVectorIndex,
)
from conversational_memory.infrastructure.sqlite import SQLiteMemoryRepository

REQUESTED_AT = datetime(2026, 9, 8, 15, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 9, 8, 15, 1, tzinfo=UTC)
EMBEDDING = Embedding(values=(1.0, 0.0), model_id="test-model", dimension=2)


def test_cli_exposes_the_m7_forgetting_demo() -> None:
    assert build_parser().parse_args(["demo-forgetting"]).command == "demo-forgetting"


class _Embedder:
    def embed(self, content: str) -> Embedding:
        assert content
        return EMBEDDING


class _CountingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, content: str) -> Embedding:
        assert content
        self.calls += 1
        return EMBEDDING


class _DemoEmbedder:
    def embed(self, content: str) -> Embedding:
        assert content
        values = [0.0] * ALL_MPNET_BASE_V2_DIMENSION
        values[0] = 1.0
        return Embedding(
            values=tuple(values),
            model_id=ALL_MPNET_BASE_V2_MODEL_ID,
            dimension=ALL_MPNET_BASE_V2_DIMENSION,
        )


class _DemoCounter:
    tokenizer_id = "cl100k_base"

    @staticmethod
    def count_tokens(text: str) -> int:
        return len(text)


class _Clock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        value = REQUESTED_AT + timedelta(minutes=self.calls)
        self.calls += 1
        return value


class _SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self._values = iter(values)
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return next(self._values)


class _Counter:
    tokenizer_id = "cl100k_base"

    @staticmethod
    def count_tokens(text: str) -> int:
        return len(text)


class _UnusedIds:
    def new_id(self) -> str:
        raise AssertionError("forget/retrieve must not allocate memory IDs")


class _OneMemoryId:
    def __init__(self, memory_id: str) -> None:
        self._memory_id = memory_id
        self.calls = 0

    def new_id(self) -> str:
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("idempotent replay must not allocate another ID")
        return self._memory_id


class _CountingIndex(FaissVectorIndex):
    def __init__(self, directory: Path) -> None:
        self.remove_calls = 0
        super().__init__(
            directory,
            embedding_model="test-model",
            vector_dimension=2,
            create_if_missing=True,
        )

    def remove(self, *, vector_id: int) -> None:
        self.remove_calls += 1
        super().remove(vector_id=vector_id)


class _FailOnceRemovalIndex(_CountingIndex):
    def remove(self, *, vector_id: int) -> None:
        self.remove_calls += 1
        if self.remove_calls == 1:
            raise IndexingError("forced forgetting removal failure")
        FaissVectorIndex.remove(self, vector_id=vector_id)


class _CountingWorkflowIndex(_CountingIndex):
    def __init__(self, directory: Path) -> None:
        self.add_calls = 0
        super().__init__(directory)

    def add(self, *, vector_id: int, embedding: Embedding) -> None:
        self.add_calls += 1
        super().add(vector_id=vector_id, embedding=embedding)


class _FailOnceCompletionRepository(SQLiteMemoryRepository):
    def __init__(self, database_path: Path) -> None:
        self.completion_calls = 0
        super().__init__(database_path)

    def acknowledge_forgetting_complete(
        self,
        *,
        user_id: str,
        memory_id: str,
        vector_id: int,
        completed_at: datetime,
    ):
        self.completion_calls += 1
        if self.completion_calls == 1:
            raise StorageError("forced forgetting acknowledgement failure")
        return super().acknowledge_forgetting_complete(
            user_id=user_id,
            memory_id=memory_id,
            vector_id=vector_id,
            completed_at=completed_at,
        )


class _FailOnceLookupRepository(SQLiteMemoryRepository):
    def __init__(self, database_path: Path) -> None:
        self.lookup_calls = 0
        super().__init__(database_path)

    def find_forgetting_target(self, *, user_id: str, memory_id: str):
        self.lookup_calls += 1
        if self.lookup_calls == 1:
            raise StorageError("forced owner lookup failure")
        return super().find_forgetting_target(user_id=user_id, memory_id=memory_id)


def _service(
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    clock: ClockPort,
) -> MemoryService:
    return MemoryService(
        idempotency=repository,
        embedder=_Embedder(),
        repository=repository,
        vector_index=vector_index,
        token_counter=_Counter(),
        clock=clock,
        memory_ids=_UnusedIds(),
        relevance_threshold=0.50,
    )


def _retrieval(intent: RetrievalIntent = RetrievalIntent.CURRENT) -> RetrievalRequest:
    return RetrievalRequest(query="FAISS", limit=5, token_budget=1000, intent=intent)


def _seed_indexed(
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    memory: MemoryRecord,
) -> int:
    vector_id = _persist(repository, memory)
    vector_index.add(vector_id=vector_id, embedding=EMBEDDING)
    repository.mark_indexed(user_id=memory.user_id, memory_id=memory.memory_id)
    return vector_id


def _memory(memory_id: str, user_id: str = "user-1") -> MemoryRecord:
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
        created_at=REQUESTED_AT,
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.PENDING,
    )


def _persist(repository: SQLiteMemoryRepository, memory: MemoryRecord) -> int:
    return repository.persist_pending(
        memory=memory,
        embedding=EMBEDDING,
        idempotency_key=f"key-{memory.memory_id}",
        request_fingerprint=memory.memory_id[0].lower() * 64,
    ).vector_id


def test_migration_creates_constrained_forgetting_state(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    SQLiteMemoryRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        columns = {
            row[1]: (row[2], row[3])
            for row in connection.execute("PRAGMA table_info(memory_forgetting)")
        }

    assert version == (3,)
    assert columns == {
        "memory_id": ("TEXT", 0),
        "user_id": ("TEXT", 1),
        "vector_id": ("INTEGER", 0),
        "cleanup_state": ("TEXT", 1),
        "requested_at": ("TEXT", 1),
        "completed_at": ("TEXT", 0),
    }


def test_owner_scoped_read_and_atomic_initiation_preserve_mapping(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_id = _persist(repository, _memory("aaaa-memory"))

    target = repository.find_forgetting_target(user_id="user-1", memory_id="aaaa-memory")
    hidden = repository.find_forgetting_target(user_id="user-2", memory_id="aaaa-memory")
    initiated = repository.begin_forgetting(
        user_id="user-1", memory_id="aaaa-memory", requested_at=REQUESTED_AT
    )

    assert target is not None
    assert target.memory.memory_id == "aaaa-memory"
    assert target.vector_id == vector_id
    assert target.forgetting is None
    assert hidden is None
    assert initiated is not None
    assert initiated.vector_id == vector_id
    assert initiated.cleanup_state is ForgettingCleanupState.CLEANUP_PENDING
    assert initiated.requested_at is REQUESTED_AT
    assert initiated.completed_at is None
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT deleted_at FROM memories WHERE memory_id = 'aaaa-memory'"
        ).fetchone()
        mapping = connection.execute(
            "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = 'aaaa-memory'"
        ).fetchone()
    assert row == ("2026-09-08T15:00:00.000000Z",)
    assert mapping == (vector_id,)


def test_cross_owner_initiation_is_opaque_and_has_zero_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    _persist(repository, _memory("bbbb-memory", "user-1"))

    result = repository.begin_forgetting(
        user_id="user-2", memory_id="bbbb-memory", requested_at=REQUESTED_AT
    )

    assert result is None
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT deleted_at FROM memories WHERE memory_id = 'bbbb-memory'"
        ).fetchone() == (None,)
        assert connection.execute("SELECT COUNT(*) FROM memory_forgetting").fetchone() == (0,)


def test_initiation_failure_rolls_back_tombstone_and_cleanup_state(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_id = _persist(repository, _memory("eeee-memory"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_forgetting BEFORE INSERT ON memory_forgetting
            BEGIN SELECT RAISE(ABORT, 'forced forgetting failure'); END
            """
        )
        connection.commit()

    with pytest.raises(StorageError, match="forgetting initiation failed"):
        repository.begin_forgetting(
            user_id="user-1", memory_id="eeee-memory", requested_at=REQUESTED_AT
        )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT deleted_at FROM memories WHERE memory_id = 'eeee-memory'"
        ).fetchone() == (None,)
        assert connection.execute("SELECT COUNT(*) FROM memory_forgetting").fetchone() == (0,)
        assert connection.execute(
            "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = 'eeee-memory'"
        ).fetchone() == (vector_id,)


def test_owned_record_without_mapping_becomes_null_id_cleanup_pending(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    _persist(repository, _memory("cccc-memory"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM memory_vector_mappings WHERE memory_id = 'cccc-memory'"
        )
        connection.commit()

    state = repository.begin_forgetting(
        user_id="user-1", memory_id="cccc-memory", requested_at=REQUESTED_AT
    )

    assert state is not None
    assert state.vector_id is None
    assert state.cleanup_state is ForgettingCleanupState.CLEANUP_PENDING
    assert state.completed_at is None
    stored = repository.find_forgetting_target(
        user_id="user-1", memory_id="cccc-memory"
    )
    assert stored is not None
    assert stored.forgetting == state
    assert stored.vector_id is None


def test_migration_backfills_mapped_and_unmapped_tombstones_as_pending(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    mapped_vector_id = _persist(repository, _memory("aaaa-mapped"))
    _persist(repository, _memory("bbbb-unmapped"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE memories SET deleted_at = ?",
            ("2026-09-08T15:00:00.000000Z",),
        )
        connection.execute(
            "DELETE FROM memory_vector_mappings WHERE memory_id = 'bbbb-unmapped'"
        )
        connection.execute("DROP TABLE memory_forgetting")
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")
        connection.commit()

    migrated = SQLiteMemoryRepository(database_path)
    mapped = migrated.find_forgetting_target(
        user_id="user-1", memory_id="aaaa-mapped"
    )
    unmapped = migrated.find_forgetting_target(
        user_id="user-1", memory_id="bbbb-unmapped"
    )

    assert mapped is not None and mapped.forgetting is not None
    assert mapped.forgetting.vector_id == mapped_vector_id
    assert mapped.forgetting.cleanup_state is ForgettingCleanupState.CLEANUP_PENDING
    assert mapped.forgetting.completed_at is None
    assert unmapped is not None and unmapped.forgetting is not None
    assert unmapped.forgetting.vector_id is None
    assert unmapped.forgetting.cleanup_state is ForgettingCleanupState.CLEANUP_PENDING
    assert unmapped.forgetting.completed_at is None


def test_initiation_and_stored_state_reads_are_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    first = SQLiteMemoryRepository(database_path)
    vector_id = _persist(first, _memory("dddd-memory"))
    initial = first.begin_forgetting(
        user_id="user-1", memory_id="dddd-memory", requested_at=REQUESTED_AT
    )
    later = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)

    restarted = SQLiteMemoryRepository(database_path)
    repeated = restarted.begin_forgetting(
        user_id="user-1", memory_id="dddd-memory", requested_at=later
    )
    stored = restarted.find_forgetting_target(
        user_id="user-1", memory_id="dddd-memory"
    )

    assert initial == repeated
    assert stored is not None
    assert stored.forgetting == initial
    assert stored.vector_id == vector_id
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_forgetting").fetchone() == (1,)
        assert connection.execute(
            "SELECT deleted_at FROM memories WHERE memory_id = 'dddd-memory'"
        ).fetchone() == ("2026-09-08T15:00:00.000000Z",)


def test_completion_acknowledgement_removes_mapping_only_when_it_commits(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_id = _persist(repository, _memory("ffff-memory"))
    pending = repository.begin_forgetting(
        user_id="user-1", memory_id="ffff-memory", requested_at=REQUESTED_AT
    )
    assert pending is not None
    index_directory = tmp_path / "index"
    vector_index = FaissVectorIndex(
        index_directory,
        embedding_model="test-model",
        vector_dimension=2,
        create_if_missing=True,
    )
    vector_index.add(vector_id=vector_id, embedding=EMBEDDING)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = 'ffff-memory'"
        ).fetchone() == (vector_id,)

    vector_index.remove(vector_id=vector_id)
    restarted_index = FaissVectorIndex(
        index_directory,
        embedding_model="test-model",
        vector_dimension=2,
    )
    assert restarted_index.search(
        embedding=EMBEDDING, allowed_vector_ids=(), limit=1
    ) == ()

    complete = repository.acknowledge_forgetting_complete(
        user_id="user-1",
        memory_id="ffff-memory",
        vector_id=vector_id,
        completed_at=COMPLETED_AT,
    )

    assert complete.cleanup_state is ForgettingCleanupState.COMPLETE
    assert complete.completed_at is COMPLETED_AT
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = 'ffff-memory'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT cleanup_state, completed_at FROM memory_forgetting WHERE memory_id = 'ffff-memory'"
        ).fetchone() == ("complete", "2026-09-08T15:01:00.000000Z")


def test_failed_completion_acknowledgement_retains_pending_state_and_mapping(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_id = _persist(repository, _memory("aaaa-ack-failure"))
    repository.begin_forgetting(
        user_id="user-1", memory_id="aaaa-ack-failure", requested_at=REQUESTED_AT
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_forgetting_completion
            BEFORE UPDATE OF cleanup_state ON memory_forgetting
            WHEN NEW.cleanup_state = 'complete'
            BEGIN SELECT RAISE(ABORT, 'forced acknowledgement failure'); END
            """
        )
        connection.commit()

    with pytest.raises(StorageError, match="forgetting completion failed"):
        repository.acknowledge_forgetting_complete(
            user_id="user-1",
            memory_id="aaaa-ack-failure",
            vector_id=vector_id,
            completed_at=COMPLETED_AT,
        )

    target = repository.find_forgetting_target(
        user_id="user-1", memory_id="aaaa-ack-failure"
    )
    assert target is not None
    assert target.vector_id == vector_id
    assert target.forgetting is not None
    assert target.forgetting.cleanup_state is ForgettingCleanupState.CLEANUP_PENDING
    assert target.forgetting.completed_at is None


def test_faiss_removal_failure_leaves_cleanup_pending_and_mapping_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_id = _persist(repository, _memory("bbbb-remove-failure"))
    repository.begin_forgetting(
        user_id="user-1", memory_id="bbbb-remove-failure", requested_at=REQUESTED_AT
    )
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model="test-model",
        vector_dimension=2,
        create_if_missing=True,
    )
    vector_index.add(vector_id=vector_id, embedding=EMBEDDING)

    def fail_publish(*args: object, **kwargs: object) -> object:
        raise IndexingError("forced removal failure")

    monkeypatch.setattr(vector_index, "_persist_generation", fail_publish)
    with pytest.raises(IndexingError, match="forced removal failure"):
        vector_index.remove(vector_id=vector_id)

    target = repository.find_forgetting_target(
        user_id="user-1", memory_id="bbbb-remove-failure"
    )
    assert target is not None
    assert target.vector_id == vector_id
    assert target.memory.deleted_at == REQUESTED_AT
    assert target.forgetting is not None
    assert target.forgetting.cleanup_state is ForgettingCleanupState.CLEANUP_PENDING
    assert target.forgetting.completed_at is None


def test_service_forget_excludes_current_and_history_and_repeats_idempotently(
    tmp_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    vector_index = _CountingIndex(tmp_path / "index")
    _seed_indexed(repository, vector_index, _memory("service-memory"))
    clock = _Clock()
    service = _service(repository, vector_index, clock)
    context = RequestContext(user_id="user-1", request_id="forget-1")
    assert service.retrieve(context, _retrieval()).outcome is RetrievalOutcome.MEMORIES_SELECTED
    assert service.retrieve(context, _retrieval(RetrievalIntent.HISTORICAL)).outcome is RetrievalOutcome.MEMORIES_SELECTED
    calls_before_forget = clock.calls

    result = service.forget(context, ForgetRequest(memory_id="service-memory"))
    repeated = service.forget(context, ForgetRequest(memory_id="service-memory"))

    assert result.outcome is ForgetOutcome.FORGOTTEN
    assert result.reason == "forgotten"
    assert repeated.outcome is ForgetOutcome.FORGOTTEN
    assert repeated.reason == "already_forgotten"
    assert clock.calls == calls_before_forget + 2
    assert vector_index.remove_calls == 1
    assert service.retrieve(context, _retrieval()).outcome is RetrievalOutcome.NO_ELIGIBLE_MEMORY
    assert service.retrieve(context, _retrieval(RetrievalIntent.HISTORICAL)).outcome is RetrievalOutcome.NO_ELIGIBLE_MEMORY


def test_service_forget_cross_owner_is_opaque_with_no_clock_or_faiss(
    tmp_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    vector_index = _CountingIndex(tmp_path / "index")
    _seed_indexed(repository, vector_index, _memory("private-memory", "user-1"))
    clock = _Clock()
    service = _service(repository, vector_index, clock)

    result = service.forget(
        RequestContext(user_id="user-2", request_id="forget-cross-owner"),
        ForgetRequest(memory_id="private-memory"),
    )

    assert result.outcome is ForgetOutcome.NOT_FOUND
    assert result.memory_id is None
    assert result.deleted_at is None
    assert clock.calls == 0
    assert vector_index.remove_calls == 0
    target = repository.find_forgetting_target(
        user_id="user-1", memory_id="private-memory"
    )
    assert target is not None
    assert target.memory.deleted_at is None
    assert target.forgetting is None


def test_nonexistent_forget_matches_cross_owner_with_zero_effects(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = _CountingIndex(tmp_path / "index")
    _seed_indexed(repository, vector_index, _memory("private-memory", "user-1"))
    vector_index.remove_calls = 0
    clock = _Clock()
    service = _service(repository, vector_index, clock)

    def persisted_rows() -> tuple[list[tuple[object, ...]], ...]:
        with sqlite3.connect(database_path) as connection:
            return tuple(
                connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in (
                    "memories",
                    "admission_idempotency",
                    "memory_embeddings",
                    "memory_vector_mappings",
                    "memory_forgetting",
                )
            )

    before = persisted_rows()
    cross_owner = service.forget(
        RequestContext(user_id="user-2", request_id="cross-owner"),
        ForgetRequest(memory_id="private-memory"),
    )
    nonexistent = service.forget(
        RequestContext(user_id="user-2", request_id="nonexistent"),
        ForgetRequest(memory_id="does-not-exist"),
    )

    assert nonexistent == cross_owner
    assert nonexistent.outcome is ForgetOutcome.NOT_FOUND
    assert nonexistent.memory_id is None
    assert nonexistent.deleted_at is None
    assert clock.calls == 0
    assert vector_index.remove_calls == 0
    assert persisted_rows() == before


def test_completed_forgetting_preserves_original_admission_idempotency(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = _CountingWorkflowIndex(tmp_path / "index")
    embedder = _CountingEmbedder()
    memory_ids = _OneMemoryId("forgotten-admission")
    clock = _Clock()
    service = MemoryService(
        idempotency=repository,
        embedder=embedder,
        repository=repository,
        vector_index=vector_index,
        token_counter=_Counter(),
        clock=clock,
        memory_ids=memory_ids,
        relevance_threshold=0.50,
    )
    context = RequestContext(user_id="user-1", request_id="admit-forget-replay")
    request = AdmissionRequest(
        idempotency_key="retained-admission-key",
        conversation_id="conversation-1",
        turn_id="turn-retained",
        content="I prefer FAISS.",
        memory_type="preference",
        subject="vector database",
        value="FAISS",
        source_type="explicit_user",
    )
    admitted = service.admit(context, request)
    forgotten = service.forget(
        context,
        ForgetRequest(memory_id="forgotten-admission"),
    )

    replayed = service.admit(context, request)

    assert forgotten.outcome is ForgetOutcome.FORGOTTEN
    assert replayed == admitted
    assert embedder.calls == 1
    assert memory_ids.calls == 1
    assert vector_index.add_calls == 1
    assert vector_index.remove_calls == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM admission_idempotency"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT deleted_at FROM memories WHERE memory_id = ?",
            ("forgotten-admission",),
        ).fetchone()[0] is not None
        assert connection.execute(
            "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = ?",
            ("forgotten-admission",),
        ).fetchone() is None
        assert connection.execute(
            "SELECT cleanup_state FROM memory_forgetting WHERE memory_id = ?",
            ("forgotten-admission",),
        ).fetchone() == ("complete",)


def test_service_null_id_forgetting_and_retry_use_zero_faiss_and_one_clock(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = _CountingIndex(tmp_path / "index")
    _persist(repository, _memory("null-id-memory"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM memory_vector_mappings WHERE memory_id = 'null-id-memory'"
        )
        connection.commit()
    clock = _Clock()
    service = _service(repository, vector_index, clock)
    context = RequestContext(user_id="user-1", request_id="forget-null")

    first = service.forget(context, ForgetRequest(memory_id="null-id-memory"))
    repeated = service.forget(context, ForgetRequest(memory_id="null-id-memory"))

    assert first.reason == repeated.reason == "physical_cleanup_identity_pending"
    assert first.outcome is repeated.outcome is ForgetOutcome.CLEANUP_PENDING
    assert clock.calls == 1
    assert vector_index.remove_calls == 0


def test_null_id_retry_after_restart_ignores_later_mapping_without_clock_or_faiss(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    first_repository = SQLiteMemoryRepository(database_path)
    first_index = _CountingIndex(index_directory)
    _persist(first_repository, _memory("aaaa-null-restart"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM memory_vector_mappings WHERE memory_id = 'aaaa-null-restart'"
        )
        connection.commit()
    first_clock = _Clock()
    first = _service(first_repository, first_index, first_clock).forget(
        RequestContext(user_id="user-1", request_id="null-first"),
        ForgetRequest(memory_id="aaaa-null-restart"),
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO memory_vector_mappings(vector_id, memory_id) VALUES (?, ?)",
            (777, "aaaa-null-restart"),
        )
        connection.commit()

    restarted_repository = SQLiteMemoryRepository(database_path)
    restarted_index = _CountingIndex(index_directory)
    restarted_clock = _Clock()
    repeated = _service(restarted_repository, restarted_index, restarted_clock).forget(
        RequestContext(user_id="user-1", request_id="null-retry"),
        ForgetRequest(memory_id="aaaa-null-restart"),
    )

    assert first.reason == repeated.reason == "physical_cleanup_identity_pending"
    assert first_clock.calls == 1
    assert restarted_clock.calls == 0
    assert first_index.remove_calls == restarted_index.remove_calls == 0
    stored = restarted_repository.find_forgetting_target(
        user_id="user-1", memory_id="aaaa-null-restart"
    )
    assert stored is not None and stored.forgetting is not None
    assert stored.vector_id == 777
    assert stored.forgetting.vector_id is None
    assert stored.forgetting.cleanup_state is ForgettingCleanupState.CLEANUP_PENDING


def test_invalid_deletion_clock_fails_before_tombstone_or_faiss(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    vector_index = _CountingIndex(tmp_path / "index")
    _seed_indexed(repository, vector_index, _memory("aaaa-invalid-delete-clock"))
    clock = _SequenceClock(REQUESTED_AT.replace(tzinfo=None))
    service = _service(repository, vector_index, clock)

    with pytest.raises(ConfigurationError, match="invalid_trusted_clock"):
        service.forget(
            RequestContext(user_id="user-1", request_id="invalid-delete-clock"),
            ForgetRequest(memory_id="aaaa-invalid-delete-clock"),
        )

    target = repository.find_forgetting_target(
        user_id="user-1", memory_id="aaaa-invalid-delete-clock"
    )
    assert target is not None
    assert target.memory.deleted_at is None
    assert target.forgetting is None
    assert vector_index.remove_calls == 0


def test_invalid_completion_clock_leaves_known_id_cleanup_pending(
    tmp_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    vector_index = _CountingIndex(tmp_path / "index")
    vector_id = _seed_indexed(
        repository,
        vector_index,
        _memory("aaaa-invalid-completion-clock"),
    )
    clock = _SequenceClock(REQUESTED_AT, COMPLETED_AT.replace(tzinfo=None))
    service = _service(repository, vector_index, clock)

    with pytest.raises(ConfigurationError, match="invalid_trusted_clock"):
        service.forget(
            RequestContext(user_id="user-1", request_id="invalid-complete-clock"),
            ForgetRequest(memory_id="aaaa-invalid-completion-clock"),
        )

    target = repository.find_forgetting_target(
        user_id="user-1", memory_id="aaaa-invalid-completion-clock"
    )
    assert target is not None and target.forgetting is not None
    assert target.vector_id == vector_id
    assert target.forgetting.cleanup_state is ForgettingCleanupState.CLEANUP_PENDING
    assert target.forgetting.completed_at is None
    assert vector_index.remove_calls == 1


def test_stored_and_live_vector_id_mismatch_fails_before_faiss(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = _CountingIndex(tmp_path / "index")
    vector_id = _seed_indexed(repository, vector_index, _memory("aaaa-id-mismatch"))
    repository.begin_forgetting(
        user_id="user-1",
        memory_id="aaaa-id-mismatch",
        requested_at=REQUESTED_AT,
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE memory_vector_mappings SET vector_id = ? WHERE memory_id = ?",
            (vector_id + 100, "aaaa-id-mismatch"),
        )
        connection.commit()
    vector_index.remove_calls = 0

    with pytest.raises(StorageError, match="forgetting target lookup failed"):
        _service(repository, vector_index, _Clock()).forget(
            RequestContext(user_id="user-1", request_id="id-mismatch"),
            ForgetRequest(memory_id="aaaa-id-mismatch"),
        )

    assert vector_index.remove_calls == 0
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = ?",
            ("aaaa-id-mismatch",),
        ).fetchone() == (vector_id + 100,)
        assert connection.execute(
            """
            SELECT vector_id, cleanup_state, completed_at
            FROM memory_forgetting WHERE memory_id = ?
            """,
            ("aaaa-id-mismatch",),
        ).fetchone() == (vector_id, "cleanup_pending", None)


def test_unavailable_owner_lookup_releases_process_lock_for_next_forget(
    tmp_path: Path,
) -> None:
    repository = _FailOnceLookupRepository(tmp_path / "memory.sqlite3")
    vector_index = _CountingIndex(tmp_path / "index")
    _seed_indexed(repository, vector_index, _memory("aaaa-lookup-release"))
    service = _service(repository, vector_index, _Clock())
    context = RequestContext(user_id="user-1", request_id="lookup-release")
    request = ForgetRequest(memory_id="aaaa-lookup-release")

    with pytest.raises(StorageError, match="forced owner lookup failure"):
        service.forget(context, request)
    result = service.forget(context, request)

    assert result.outcome is ForgetOutcome.FORGOTTEN
    assert repository.lookup_calls == 2
    assert vector_index.remove_calls == 1


@pytest.mark.parametrize("lifecycle", list(LifecycleStatus))
@pytest.mark.parametrize("indexing", list(IndexingState))
def test_every_owned_lifecycle_and_indexing_state_can_be_forgotten(
    tmp_path: Path,
    lifecycle: LifecycleStatus,
    indexing: IndexingState,
) -> None:
    suffix = f"{lifecycle.value}-{indexing.value}"
    scenario = tmp_path / suffix
    scenario.mkdir()
    repository = SQLiteMemoryRepository(scenario / "memory.sqlite3")
    vector_index = _CountingIndex(scenario / "index")
    memory_id = f"aaaa-{suffix}"
    _seed_indexed(repository, vector_index, _memory(memory_id))
    superseded_by = "replacement" if lifecycle is LifecycleStatus.SUPERSEDED else None
    with sqlite3.connect(scenario / "memory.sqlite3") as connection:
        connection.execute(
            """
            UPDATE memories
            SET lifecycle_status = ?, indexing_state = ?, superseded_by = ?
            WHERE memory_id = ?
            """,
            (lifecycle.value, indexing.value, superseded_by, memory_id),
        )
        connection.commit()

    result = _service(repository, vector_index, _Clock()).forget(
        RequestContext(user_id="user-1", request_id=f"forget-{suffix}"),
        ForgetRequest(memory_id=memory_id),
    )

    assert result.outcome is ForgetOutcome.FORGOTTEN
    target = repository.find_forgetting_target(user_id="user-1", memory_id=memory_id)
    assert target is not None
    assert target.memory.lifecycle_status is lifecycle
    assert target.memory.indexing_state is indexing
    assert target.memory.deleted_at == REQUESTED_AT


def test_concurrent_identical_forgetting_serializes_to_one_cleanup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    seed_repository = SQLiteMemoryRepository(database_path)
    seed_index = _CountingIndex(index_directory)
    _seed_indexed(seed_repository, seed_index, _memory("aaaa-concurrent"))
    repositories = [
        SQLiteMemoryRepository(database_path),
        SQLiteMemoryRepository(database_path),
    ]
    indexes = [_CountingIndex(index_directory), _CountingIndex(index_directory)]
    clocks = [_Clock(), _Clock()]
    services = [
        _service(repository, index, clock)
        for repository, index, clock in zip(repositories, indexes, clocks, strict=True)
    ]
    barrier = Barrier(2)

    def invoke(position: int):
        barrier.wait()
        return services[position].forget(
            RequestContext(user_id="user-1", request_id=f"concurrent-{position}"),
            ForgetRequest(memory_id="aaaa-concurrent"),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(invoke, range(2)))

    assert {result.reason for result in results} == {"forgotten", "already_forgotten"}
    assert sum(index.remove_calls for index in indexes) == 1
    assert sum(clock.calls for clock in clocks) == 2
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_forgetting"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_vector_mappings"
        ).fetchone() == (0,)


def test_partial_faiss_generation_never_acknowledges_cleanup(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = _CountingIndex(index_directory)
    vector_id = _seed_indexed(
        repository,
        vector_index,
        _memory("aaaa-partial-generation"),
    )
    (index_directory / "memory.faiss.meta.json").unlink()

    result = _service(repository, vector_index, _Clock()).forget(
        RequestContext(user_id="user-1", request_id="partial-generation"),
        ForgetRequest(memory_id="aaaa-partial-generation"),
    )

    assert result.outcome is ForgetOutcome.CLEANUP_PENDING
    assert result.reason == "physical_cleanup_pending"
    target = repository.find_forgetting_target(
        user_id="user-1", memory_id="aaaa-partial-generation"
    )
    assert target is not None and target.forgetting is not None
    assert target.vector_id == vector_id
    assert target.forgetting.cleanup_state is ForgettingCleanupState.CLEANUP_PENDING
    assert target.forgetting.completed_at is None


def test_service_faiss_failure_is_pending_and_immediately_excluded(
    tmp_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    vector_index = _FailOnceRemovalIndex(tmp_path / "index")
    _seed_indexed(repository, vector_index, _memory("remove-failure"))
    clock = _Clock()
    service = _service(repository, vector_index, clock)
    context = RequestContext(user_id="user-1", request_id="forget-failure")

    result = service.forget(context, ForgetRequest(memory_id="remove-failure"))

    assert result.outcome is ForgetOutcome.CLEANUP_PENDING
    assert result.reason == "physical_cleanup_pending"
    assert clock.calls == 1
    assert service.retrieve(context, _retrieval()).outcome is RetrievalOutcome.NO_ELIGIBLE_MEMORY
    assert service.retrieve(context, _retrieval(RetrievalIntent.HISTORICAL)).outcome is RetrievalOutcome.NO_ELIGIBLE_MEMORY


def test_service_acknowledgement_failure_retries_without_duplicate_removal(
    tmp_path: Path,
) -> None:
    repository = _FailOnceCompletionRepository(tmp_path / "memory.sqlite3")
    vector_index = _CountingIndex(tmp_path / "index")
    _seed_indexed(repository, vector_index, _memory("ack-failure"))
    clock = _Clock()
    service = _service(repository, vector_index, clock)
    context = RequestContext(user_id="user-1", request_id="forget-ack")

    first = service.forget(context, ForgetRequest(memory_id="ack-failure"))
    second = service.forget(context, ForgetRequest(memory_id="ack-failure"))

    assert first.outcome is ForgetOutcome.CLEANUP_PENDING
    assert first.reason == "physical_cleanup_pending"
    assert second.outcome is ForgetOutcome.FORGOTTEN
    assert repository.completion_calls == 2
    assert vector_index.remove_calls == 2
    target = repository.find_forgetting_target(user_id="user-1", memory_id="ack-failure")
    assert target is not None
    assert target.forgetting is not None
    assert target.forgetting.cleanup_state is ForgettingCleanupState.COMPLETE


def test_forgetting_replacement_does_not_reactivate_superseded_predecessor(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = _CountingIndex(tmp_path / "index")
    _seed_indexed(repository, vector_index, _memory("predecessor"))
    _seed_indexed(repository, vector_index, _memory("replacement"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE memories SET lifecycle_status = 'superseded', superseded_by = 'replacement' WHERE memory_id = 'predecessor'"
        )
        connection.execute(
            "UPDATE memories SET supersedes_json = '[\"predecessor\"]' WHERE memory_id = 'replacement'"
        )
        connection.commit()
    service = _service(repository, vector_index, _Clock())
    context = RequestContext(user_id="user-1", request_id="forget-replacement")

    result = service.forget(context, ForgetRequest(memory_id="replacement"))

    assert result.outcome is ForgetOutcome.FORGOTTEN
    assert service.retrieve(context, _retrieval()).outcome is RetrievalOutcome.NO_ELIGIBLE_MEMORY
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT memory_id, lifecycle_status, superseded_by, supersedes_json FROM memories ORDER BY memory_id"
        ).fetchall()
    assert rows == [
        ("predecessor", "superseded", "replacement", "[]"),
        ("replacement", "active", None, '["predecessor"]'),
    ]


def test_demo_forgetting_prints_verified_restart_and_cleanup_state(
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
        lambda: _DemoCounter(),
    )

    assert _demo_forgetting() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["authorization"] == {
        "cross_owner_outcome": "not_found",
        "owner_outcome": "forgotten",
    }
    assert output["exclusion"]["immediate_current_absent"] is True
    assert output["exclusion"]["immediate_historical_absent"] is True
    assert output["restart"]["current_absent"] is True
    assert output["restart"]["historical_absent"] is True
    assert output["relationships"] == {
        "original_lifecycle_status": "superseded",
        "original_superseded_by": "m7-replacement-memory",
        "replacement_supersedes": ["m7-original-memory"],
        "predecessor_reactivated": False,
    }
    assert output["physical_cleanup"]["forgotten_vector_absent"] is True
    assert output["physical_cleanup"]["unrelated_vector_preserved"] is True
    assert output["persisted_forgetting"]["cleanup_state"] == "complete"
    assert output["persisted_forgetting"]["deleted_at"] is not None
    assert output["persisted_forgetting"]["requested_at"] is not None
    assert output["persisted_forgetting"]["completed_at"] is not None
