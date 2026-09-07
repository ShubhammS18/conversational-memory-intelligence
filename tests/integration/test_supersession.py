from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from conversational_memory import composition
from conversational_memory.application import (
    AdmissionRequest,
    AdmissionResult,
    Embedding,
    IndexingError,
    MemoryService,
    PersistedPendingMemory,
    RequestContext,
    RetrievalOutcome,
    RetrievalRequest,
    StorageError,
    ValidationError,
)
from conversational_memory.domain.models import (
    AdmissionDecision,
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.entrypoints.cli import _demo_supersession, build_parser
from conversational_memory.infrastructure import (
    ALL_MPNET_BASE_V2_DIMENSION,
    ALL_MPNET_BASE_V2_MODEL_ID,
    FaissVectorIndex,
)
from conversational_memory.infrastructure.sqlite import SQLiteMemoryRepository

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
EMBEDDING = Embedding(values=(0.25, 0.75), model_id="m4-test-model", dimension=2)


def test_cli_exposes_the_m4_supersession_demo() -> None:
    assert build_parser().parse_args(["demo-supersession"]).command == "demo-supersession"


def _memory(
    memory_id: str,
    *,
    user_id: str = "user-1",
    authority: EvidenceAuthority = EvidenceAuthority.EXPLICIT_USER,
    subject: str = "vector database",
    memory_type: MemoryType = MemoryType.PREFERENCE,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        content=f"Memory {memory_id}",
        memory_type=memory_type,
        provenance=Provenance(
            authority=authority,
            source_type=authority.value,
            conversation_id="conversation-1",
            turn_id=f"turn-{memory_id}",
        ),
        created_at=NOW,
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.PENDING,
        subject=subject,
        value=memory_id,
        valid_from=NOW,
    )


def _persist(
    repository: SQLiteMemoryRepository,
    memory: MemoryRecord,
) -> None:
    repository.persist_pending(
        memory=memory,
        embedding=EMBEDDING,
        idempotency_key=f"key-{memory.memory_id}",
        request_fingerprint=memory.memory_id,
    )


def _rows(database_path: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            """
            SELECT memory_id, user_id, lifecycle_status, indexing_state,
                   supersedes_json, superseded_by
            FROM memories ORDER BY memory_id
            """
        ).fetchall()


def _seed_pair(
    database_path: Path,
    *,
    replacement: MemoryRecord | None = None,
    target: MemoryRecord | None = None,
) -> SQLiteMemoryRepository:
    repository = SQLiteMemoryRepository(database_path)
    replacement = replacement or _memory("replacement")
    target = target or _memory("target")
    _persist(repository, target)
    repository.mark_indexed(user_id=target.user_id, memory_id=target.memory_id)
    _persist(repository, replacement)
    return repository


def test_supersession_target_resolution_is_owner_scoped(tmp_path: Path) -> None:
    repository = _seed_pair(tmp_path / "memory.sqlite3")

    target = repository.find_supersession_target(
        user_id="user-1",
        memory_id="target",
    )

    assert target is not None
    assert target.memory_id == "target"
    assert target.indexing_state is IndexingState.INDEXED
    assert repository.find_supersession_target(
        user_id="user-2",
        memory_id="target",
    ) is None
    assert repository.find_supersession_target(
        user_id="user-1",
        memory_id="missing",
    ) is None


def test_atomic_supersession_activates_replacement_and_writes_both_directions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = _seed_pair(database_path)

    repository.acknowledge_supersession(
        user_id="user-1",
        replacement_memory_id="replacement",
        target_memory_id="target",
    )

    assert _rows(database_path) == [
        ("replacement", "user-1", "active", "indexed", '["target"]', None),
        ("target", "user-1", "superseded", "indexed", "[]", "replacement"),
    ]


@pytest.mark.parametrize(
    ("replacement", "target"),
    [
        (_memory("replacement", authority=EvidenceAuthority.INFERRED), _memory("target")),
        (_memory("replacement", subject="programming language"), _memory("target")),
        (
            _memory("replacement", memory_type=MemoryType.DECISION),
            _memory("target"),
        ),
        (_memory("replacement", user_id="user-1"), _memory("target", user_id="user-2")),
    ],
    ids=("inferred", "subject-mismatch", "type-mismatch", "cross-owner"),
)
def test_rejected_supersession_transition_leaves_all_rows_unchanged(
    tmp_path: Path,
    replacement: MemoryRecord,
    target: MemoryRecord,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = _seed_pair(database_path, replacement=replacement, target=target)
    before = _rows(database_path)

    with pytest.raises(StorageError, match="supersession transition rejected"):
        repository.acknowledge_supersession(
            user_id="user-1",
            replacement_memory_id="replacement",
            target_memory_id="target",
        )

    assert _rows(database_path) == before


def test_stale_target_revalidation_leaves_all_rows_unchanged(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = _seed_pair(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE memories
            SET lifecycle_status = 'superseded', superseded_by = 'other-replacement'
            WHERE memory_id = 'target'
            """
        )
    before = _rows(database_path)

    with pytest.raises(StorageError, match="supersession transition rejected"):
        repository.acknowledge_supersession(
            user_id="user-1",
            replacement_memory_id="replacement",
            target_memory_id="target",
        )

    assert _rows(database_path) == before


def test_sqlite_failure_rolls_back_both_supersession_directions(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = _seed_pair(database_path)
    before = _rows(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_target_supersession
            BEFORE UPDATE ON memories
            WHEN OLD.memory_id = 'target'
            BEGIN
                SELECT RAISE(ABORT, 'forced supersession failure');
            END
            """
        )

    with pytest.raises(StorageError, match="supersession transaction failed"):
        repository.acknowledge_supersession(
            user_id="user-1",
            replacement_memory_id="replacement",
            target_memory_id="target",
        )

    assert _rows(database_path) == before


class _ObservingRepository(SQLiteMemoryRepository):
    def __init__(self, database_path: Path, events: list[str]) -> None:
        self.events = events
        super().__init__(database_path)

    def find_supersession_target(
        self, *, user_id: str, memory_id: str
    ) -> MemoryRecord | None:
        self.events.append(f"resolve:{user_id}:{memory_id}")
        return super().find_supersession_target(user_id=user_id, memory_id=memory_id)

    def persist_pending(
        self,
        *,
        memory: MemoryRecord,
        embedding: Embedding,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> PersistedPendingMemory:
        self.events.append(f"persist:{memory.memory_id}")
        return super().persist_pending(
            memory=memory,
            embedding=embedding,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

    def mark_indexed(self, *, user_id: str, memory_id: str) -> None:
        self.events.append(f"mark-indexed:{memory_id}")
        super().mark_indexed(user_id=user_id, memory_id=memory_id)

    def acknowledge_supersession(
        self,
        *,
        user_id: str,
        replacement_memory_id: str,
        target_memory_id: str,
    ) -> None:
        self.events.append(f"acknowledge:{replacement_memory_id}:{target_memory_id}")
        super().acknowledge_supersession(
            user_id=user_id,
            replacement_memory_id=replacement_memory_id,
            target_memory_id=target_memory_id,
        )


class _FailOnceAcknowledgementRepository(_ObservingRepository):
    def __init__(self, database_path: Path, events: list[str]) -> None:
        self.failures_remaining = 1
        super().__init__(database_path, events)

    def acknowledge_supersession(
        self,
        *,
        user_id: str,
        replacement_memory_id: str,
        target_memory_id: str,
    ) -> None:
        self.events.append(f"acknowledge:{replacement_memory_id}:{target_memory_id}")
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise StorageError("simulated supersession acknowledgement failure")
        SQLiteMemoryRepository.acknowledge_supersession(
            self,
            user_id=user_id,
            replacement_memory_id=replacement_memory_id,
            target_memory_id=target_memory_id,
        )


class _FailReplacementPersistenceRepository(_ObservingRepository):
    fail_persistence = False

    def persist_pending(
        self,
        *,
        memory: MemoryRecord,
        embedding: Embedding,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> PersistedPendingMemory:
        if self.fail_persistence:
            self.events.append(f"persist:{memory.memory_id}")
            raise StorageError("simulated replacement persistence failure")
        return super().persist_pending(
            memory=memory,
            embedding=embedding,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )


class _ObservingEmbedder:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def embed(self, content: str) -> Embedding:
        self.events.append(f"embed:{content}")
        return EMBEDDING


class _ObservingIndex(FaissVectorIndex):
    def __init__(self, index_directory: Path, events: list[str]) -> None:
        self.events = events
        super().__init__(
            index_directory,
            embedding_model=EMBEDDING.model_id,
            vector_dimension=EMBEDDING.dimension,
            create_if_missing=True,
        )

    def add(self, *, vector_id: int, embedding: Embedding) -> None:
        self.events.append(f"publish:{vector_id}")
        super().add(vector_id=vector_id, embedding=embedding)


class _FailingPublicationIndex(FaissVectorIndex):
    def __init__(self, index_directory: Path, events: list[str]) -> None:
        self.events = events
        self.failure_mode: str | None = None
        super().__init__(
            index_directory,
            embedding_model=EMBEDDING.model_id,
            vector_dimension=EMBEDDING.dimension,
            create_if_missing=True,
        )

    def add(self, *, vector_id: int, embedding: Embedding) -> None:
        self.events.append(f"publish:{vector_id}")
        if self.failure_mode == "failed":
            raise IndexingError("simulated failed publication")
        super().add(vector_id=vector_id, embedding=embedding)
        if self.failure_mode == "uncertain":
            raise IndexingError("simulated uncertain publication")


class _FixedClock:
    def now(self) -> datetime:
        return NOW


class _ReplacementId:
    def new_id(self) -> str:
        return "replacement"


class _UnusedReplacementId:
    def new_id(self) -> str:
        raise AssertionError("restart retry must reuse the persisted replacement ID")


class _SequentialIds:
    def __init__(self) -> None:
        self._ids = iter(("original", "correction", "reversion"))

    def new_id(self) -> str:
        return next(self._ids)


class _FixedId:
    def __init__(self, memory_id: str) -> None:
        self._memory_id = memory_id

    def new_id(self) -> str:
        return self._memory_id


class _CharacterCounter:
    tokenizer_id = "cl100k_base"

    @staticmethod
    def count_tokens(text: str) -> int:
        return len(text)


class _DemoEmbedder:
    def embed(self, content: str) -> Embedding:
        assert content
        return Embedding(
            values=(1.0,) + (0.0,) * (ALL_MPNET_BASE_V2_DIMENSION - 1),
            model_id=ALL_MPNET_BASE_V2_MODEL_ID,
            dimension=ALL_MPNET_BASE_V2_DIMENSION,
        )


def _targeted_request() -> AdmissionRequest:
    return AdmissionRequest(
        idempotency_key="replacement-key",
        conversation_id="conversation-2",
        turn_id="turn-replacement",
        content="I prefer PostgreSQL.",
        memory_type="preference",
        subject="vector database",
        value="PostgreSQL",
        source_type="explicit_user",
        supersedes_memory_id="target",
    )


def _correction_request(
    *,
    idempotency_key: str,
    content: str,
    value: str,
    target: str | None,
) -> AdmissionRequest:
    return AdmissionRequest(
        idempotency_key=idempotency_key,
        conversation_id="reversion-conversation",
        turn_id=idempotency_key,
        content=content,
        memory_type="preference",
        subject="vector database",
        value=value,
        source_type="explicit_user",
        supersedes_memory_id=target,
    )


def test_targeted_admission_publishes_then_atomically_replaces_current_memory(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    database_path = tmp_path / "memory.sqlite3"
    repository = _ObservingRepository(database_path, events)
    vector_index = _ObservingIndex(tmp_path / "index", events)
    target = _memory("target")
    _persist(repository, target)
    vector_index.add(vector_id=1, embedding=EMBEDDING)
    repository.mark_indexed(user_id="user-1", memory_id="target")
    events.clear()
    service = MemoryService(
        idempotency=repository,
        embedder=_ObservingEmbedder(events),
        repository=repository,
        vector_index=vector_index,
        token_counter=_CharacterCounter(),
        clock=_FixedClock(),
        memory_ids=_ReplacementId(),
        relevance_threshold=0.50,
    )

    result = service.admit(
        RequestContext(user_id="user-1", request_id="correct"),
        _targeted_request(),
    )

    assert result.decision is AdmissionDecision.ACCEPTED
    assert result.indexing_state is IndexingState.INDEXED
    assert result.retrievable is True
    assert events == [
        "resolve:user-1:target",
        "embed:I prefer PostgreSQL.",
        "persist:replacement",
        "publish:2",
        "acknowledge:replacement:target",
    ]
    assert _rows(database_path) == [
        ("replacement", "user-1", "active", "indexed", '["target"]', None),
        ("target", "user-1", "superseded", "indexed", "[]", "replacement"),
    ]
    persisted_original = repository.find(user_id="user-1", idempotency_key="key-target")
    persisted_replacement = repository.find(
        user_id="user-1", idempotency_key="replacement-key"
    )
    assert persisted_original is not None
    assert persisted_original.result.superseded_by_memory_id == "replacement"
    assert persisted_replacement is not None
    assert persisted_replacement.result.supersedes_memory_ids == ("target",)

    retrieved = service.retrieve(
        RequestContext(user_id="user-1", request_id="retrieve"),
        RetrievalRequest(query="database", limit=5, token_budget=1000),
    )

    assert retrieved.outcome is RetrievalOutcome.MEMORIES_SELECTED
    assert retrieved.included_memory_ids == ("replacement",)


def test_targeted_acknowledgement_failure_retries_only_atomic_relationship(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    database_path = tmp_path / "memory.sqlite3"
    repository = _FailOnceAcknowledgementRepository(database_path, events)
    vector_index = _ObservingIndex(tmp_path / "index", events)
    target = _memory("target")
    _persist(repository, target)
    vector_index.add(vector_id=1, embedding=EMBEDDING)
    repository.mark_indexed(user_id="user-1", memory_id="target")
    events.clear()
    service = MemoryService(
        idempotency=repository,
        embedder=_ObservingEmbedder(events),
        repository=repository,
        vector_index=vector_index,
        token_counter=_CharacterCounter(),
        clock=_FixedClock(),
        memory_ids=_ReplacementId(),
        relevance_threshold=0.50,
    )

    pending = service.admit(
        RequestContext(user_id="user-1", request_id="first"),
        _targeted_request(),
    )

    assert pending.reason == "supersession_acknowledgement_failed"
    assert pending.indexing_state is IndexingState.PENDING
    assert pending.retrievable is False
    assert pending.retryable_error == "simulated supersession acknowledgement failure"
    assert _rows(database_path) == [
        ("replacement", "user-1", "active", "pending", "[]", None),
        ("target", "user-1", "active", "indexed", "[]", None),
    ]
    first_events = tuple(events)
    assert first_events.count("embed:I prefer PostgreSQL.") == 1
    assert first_events.count("publish:2") == 1
    assert first_events.count("acknowledge:replacement:target") == 1

    events.clear()
    current = service.retrieve(
        RequestContext(user_id="user-1", request_id="pending-retrieve"),
        RetrievalRequest(query="database", limit=5, token_budget=1000),
    )
    assert current.included_memory_ids == ("target",)

    events.clear()
    indexed = service.admit(
        RequestContext(user_id="user-1", request_id="retry"),
        _targeted_request(),
    )

    assert indexed.reason == "accepted_and_indexed"
    assert indexed.memory_id == pending.memory_id == "replacement"
    assert indexed.indexing_state is IndexingState.INDEXED
    assert indexed.retrievable is True
    assert events == ["acknowledge:replacement:target"]
    assert _rows(database_path) == [
        ("replacement", "user-1", "active", "indexed", '["target"]', None),
        ("target", "user-1", "superseded", "indexed", "[]", "replacement"),
    ]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memories").fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_embeddings"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_vector_mappings"
        ).fetchone() == (2,)

    completed_rows = _rows(database_path)
    events.clear()
    replayed = service.admit(
        RequestContext(user_id="user-1", request_id="replay"),
        _targeted_request(),
    )

    assert replayed == indexed
    assert events == []
    assert _rows(database_path) == completed_rows


def test_targeted_acknowledgement_retry_is_restart_safe(tmp_path: Path) -> None:
    first_events: list[str] = []
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    first_repository = _FailOnceAcknowledgementRepository(database_path, first_events)
    first_index = _ObservingIndex(index_directory, first_events)
    target = _memory("target")
    _persist(first_repository, target)
    first_index.add(vector_id=1, embedding=EMBEDDING)
    first_repository.mark_indexed(user_id="user-1", memory_id="target")
    first_events.clear()
    first_service = MemoryService(
        idempotency=first_repository,
        embedder=_ObservingEmbedder(first_events),
        repository=first_repository,
        vector_index=first_index,
        token_counter=_CharacterCounter(),
        clock=_FixedClock(),
        memory_ids=_ReplacementId(),
        relevance_threshold=0.50,
    )

    pending = first_service.admit(
        RequestContext(user_id="user-1", request_id="before-restart"),
        _targeted_request(),
    )
    assert pending.reason == "supersession_acknowledgement_failed"
    assert _rows(database_path) == [
        ("replacement", "user-1", "active", "pending", "[]", None),
        ("target", "user-1", "active", "indexed", "[]", None),
    ]

    del first_service, first_repository, first_index
    restart_events: list[str] = []
    restarted_repository = _ObservingRepository(database_path, restart_events)
    restarted_index = _ObservingIndex(index_directory, restart_events)
    restarted_service = MemoryService(
        idempotency=restarted_repository,
        embedder=_ObservingEmbedder(restart_events),
        repository=restarted_repository,
        vector_index=restarted_index,
        token_counter=_CharacterCounter(),
        clock=_FixedClock(),
        memory_ids=_UnusedReplacementId(),
        relevance_threshold=0.50,
    )

    indexed = restarted_service.admit(
        RequestContext(user_id="user-1", request_id="after-restart"),
        _targeted_request(),
    )

    assert indexed.reason == "accepted_and_indexed"
    assert indexed.memory_id == pending.memory_id == "replacement"
    assert indexed.indexing_state is IndexingState.INDEXED
    assert indexed.retrievable is True
    assert restart_events == ["publish:2", "acknowledge:replacement:target"]
    assert _rows(database_path) == [
        ("replacement", "user-1", "active", "indexed", '["target"]', None),
        ("target", "user-1", "superseded", "indexed", "[]", "replacement"),
    ]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memories").fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_embeddings"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_vector_mappings"
        ).fetchone() == (2,)

    restart_events.clear()
    current = restarted_service.retrieve(
        RequestContext(user_id="user-1", request_id="restart-retrieve"),
        RetrievalRequest(query="database", limit=5, token_budget=1000),
    )
    assert current.included_memory_ids == ("replacement",)


def test_reversion_creates_a_third_record_without_reactivating_original(
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
    service = MemoryService(
        idempotency=repository,
        embedder=_ObservingEmbedder([]),
        repository=repository,
        vector_index=vector_index,
        token_counter=_CharacterCounter(),
        clock=_FixedClock(),
        memory_ids=_SequentialIds(),
        relevance_threshold=0.50,
    )

    original = service.admit(
        RequestContext(user_id="user-1", request_id="original"),
        _correction_request(
            idempotency_key="original-key",
            content="I prefer FAISS.",
            value="FAISS",
            target=None,
        ),
    )
    correction = service.admit(
        RequestContext(user_id="user-1", request_id="correction"),
        _correction_request(
            idempotency_key="correction-key",
            content="I prefer PostgreSQL.",
            value="PostgreSQL",
            target="original",
        ),
    )
    reversion = service.admit(
        RequestContext(user_id="user-1", request_id="reversion"),
        _correction_request(
            idempotency_key="reversion-key",
            content="I prefer FAISS again.",
            value="FAISS",
            target="correction",
        ),
    )

    assert original.memory_id == "original"
    assert correction.memory_id == "correction"
    assert reversion.memory_id == "reversion"
    assert all(
        result.indexing_state is IndexingState.INDEXED and result.retrievable
        for result in (original, correction, reversion)
    )
    assert _rows(database_path) == [
        (
            "correction",
            "user-1",
            "superseded",
            "indexed",
            '["original"]',
            "reversion",
        ),
        ("original", "user-1", "superseded", "indexed", "[]", "correction"),
        (
            "reversion",
            "user-1",
            "active",
            "indexed",
            '["correction"]',
            None,
        ),
    ]

    current = service.retrieve(
        RequestContext(user_id="user-1", request_id="current-after-reversion"),
        RetrievalRequest(query="database", limit=5, token_budget=1000),
    )

    assert current.included_memory_ids == ("reversion",)


def test_concurrent_corrections_create_exactly_one_replacement(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    seed_repository = SQLiteMemoryRepository(database_path)
    seed_index = FaissVectorIndex(
        index_directory,
        embedding_model=EMBEDDING.model_id,
        vector_dimension=EMBEDDING.dimension,
        create_if_missing=True,
    )
    target = _memory("target")
    _persist(seed_repository, target)
    seed_index.add(vector_id=1, embedding=EMBEDDING)
    seed_repository.mark_indexed(user_id="user-1", memory_id="target")

    event_sets = ([], [])
    replacement_ids = ("correction-a", "correction-b")
    services: list[MemoryService] = []
    for events, memory_id in zip(event_sets, replacement_ids, strict=True):
        repository = _ObservingRepository(database_path, events)
        services.append(
            MemoryService(
                idempotency=repository,
                embedder=_ObservingEmbedder(events),
                repository=repository,
                vector_index=_ObservingIndex(index_directory, events),
                token_counter=_CharacterCounter(),
                clock=_FixedClock(),
                memory_ids=_FixedId(memory_id),
                relevance_threshold=0.50,
            )
        )
    requests = tuple(
        _correction_request(
            idempotency_key=f"{memory_id}-key",
            content=f"Correction from {memory_id}.",
            value=memory_id,
            target="target",
        )
        for memory_id in replacement_ids
    )
    start = Barrier(3)

    def admit(index: int) -> AdmissionResult | ValidationError:
        start.wait()
        try:
            return services[index].admit(
                RequestContext(user_id="user-1", request_id=f"request-{index}"),
                requests[index],
            )
        except ValidationError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(admit, index) for index in range(2))
        start.wait()
        outcomes = tuple(future.result() for future in futures)

    successes = [
        outcome
        for outcome in outcomes
        if not isinstance(outcome, ValidationError)
    ]
    rejections = [outcome for outcome in outcomes if isinstance(outcome, ValidationError)]
    assert len(successes) == len(rejections) == 1
    winner = successes[0]
    assert isinstance(winner, AdmissionResult)
    assert winner.indexing_state is IndexingState.INDEXED
    assert winner.retrievable is True
    assert rejections[0].reason == "invalid_supersession_target"
    winner_id = winner.memory_id
    loser_id = next(memory_id for memory_id in replacement_ids if memory_id != winner_id)
    all_events = event_sets[0] + event_sets[1]
    assert sum(event.startswith("resolve:") for event in all_events) == 2
    assert sum(event.startswith("embed:") for event in all_events) == 1
    assert sum(event.startswith("persist:") for event in all_events) == 1
    assert sum(event.startswith("publish:") for event in all_events) == 1
    assert sum(event.startswith("acknowledge:") for event in all_events) == 1
    rows = _rows(database_path)
    assert len(rows) == 2
    assert not any(row[0] == loser_id for row in rows)
    assert (winner_id, "user-1", "active", "indexed", '["target"]', None) in rows
    assert ("target", "user-1", "superseded", "indexed", "[]", winner_id) in rows

    fresh_repository = SQLiteMemoryRepository(database_path)
    fresh_service = MemoryService(
        idempotency=fresh_repository,
        embedder=_ObservingEmbedder([]),
        repository=fresh_repository,
        vector_index=FaissVectorIndex(
            index_directory,
            embedding_model=EMBEDDING.model_id,
            vector_dimension=EMBEDDING.dimension,
        ),
        token_counter=_CharacterCounter(),
        clock=_FixedClock(),
        memory_ids=_UnusedReplacementId(),
        relevance_threshold=0.50,
    )
    current = fresh_service.retrieve(
        RequestContext(user_id="user-1", request_id="concurrent-current"),
        RetrievalRequest(query="database", limit=5, token_budget=1000),
    )
    assert current.included_memory_ids == (winner_id,)


def test_targeted_inferred_admission_fails_before_embedding_or_mutation(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    database_path = tmp_path / "memory.sqlite3"
    repository = _ObservingRepository(database_path, events)
    vector_index = _ObservingIndex(tmp_path / "index", events)
    target = _memory("target")
    _persist(repository, target)
    vector_index.add(vector_id=1, embedding=EMBEDDING)
    repository.mark_indexed(user_id="user-1", memory_id="target")
    events.clear()
    service = MemoryService(
        idempotency=repository,
        embedder=_ObservingEmbedder(events),
        repository=repository,
        vector_index=vector_index,
        token_counter=_CharacterCounter(),
        clock=_FixedClock(),
        memory_ids=_ReplacementId(),
        relevance_threshold=0.50,
    )

    with pytest.raises(ValidationError, match="invalid_supersession_target"):
        service.admit(
            RequestContext(user_id="user-1", request_id="inferred"),
            AdmissionRequest(
                idempotency_key="inferred-key",
                conversation_id="conversation-inferred",
                turn_id="turn-inferred",
                content="Perhaps PostgreSQL is preferred.",
                memory_type="preference",
                subject="vector database",
                value="PostgreSQL",
                source_type="inferred",
                supersedes_memory_id="target",
            ),
        )

    assert events == ["resolve:user-1:target"]
    assert _rows(database_path) == [
        ("target", "user-1", "active", "indexed", "[]", None),
    ]


def test_targeted_replacement_persistence_failure_preserves_old_current_memory(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    database_path = tmp_path / "memory.sqlite3"
    repository = _FailReplacementPersistenceRepository(database_path, events)
    vector_index = _ObservingIndex(tmp_path / "index", events)
    target = _memory("target")
    _persist(repository, target)
    vector_index.add(vector_id=1, embedding=EMBEDDING)
    repository.mark_indexed(user_id="user-1", memory_id="target")
    repository.fail_persistence = True
    events.clear()
    service = MemoryService(
        idempotency=repository,
        embedder=_ObservingEmbedder(events),
        repository=repository,
        vector_index=vector_index,
        token_counter=_CharacterCounter(),
        clock=_FixedClock(),
        memory_ids=_ReplacementId(),
        relevance_threshold=0.50,
    )

    with pytest.raises(StorageError, match="replacement persistence failure"):
        service.admit(
            RequestContext(user_id="user-1", request_id="persist-failure"),
            _targeted_request(),
        )

    assert not any(event.startswith("publish:") for event in events)
    assert not any(event.startswith("acknowledge:") for event in events)
    assert _rows(database_path) == [
        ("target", "user-1", "active", "indexed", "[]", None),
    ]
    current = service.retrieve(
        RequestContext(user_id="user-1", request_id="after-persist-failure"),
        RetrievalRequest(query="database", limit=5, token_budget=1000),
    )
    assert current.included_memory_ids == ("target",)


@pytest.mark.parametrize("failure_mode", ["failed", "uncertain"])
def test_targeted_publication_failure_uses_existing_m1_failed_state(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    events: list[str] = []
    database_path = tmp_path / "memory.sqlite3"
    repository = _ObservingRepository(database_path, events)
    vector_index = _FailingPublicationIndex(tmp_path / "index", events)
    target = _memory("target")
    _persist(repository, target)
    vector_index.add(vector_id=1, embedding=EMBEDDING)
    repository.mark_indexed(user_id="user-1", memory_id="target")
    vector_index.failure_mode = failure_mode
    events.clear()
    service = MemoryService(
        idempotency=repository,
        embedder=_ObservingEmbedder(events),
        repository=repository,
        vector_index=vector_index,
        token_counter=_CharacterCounter(),
        clock=_FixedClock(),
        memory_ids=_ReplacementId(),
        relevance_threshold=0.50,
    )

    result = service.admit(
        RequestContext(user_id="user-1", request_id=failure_mode),
        _targeted_request(),
    )

    assert result.reason == "indexing_failed"
    assert result.indexing_state is IndexingState.FAILED
    assert result.retrievable is False
    assert not any(event.startswith("acknowledge:") for event in events)
    assert _rows(database_path) == [
        ("replacement", "user-1", "active", "failed", "[]", None),
        ("target", "user-1", "active", "indexed", "[]", None),
    ]
    current = service.retrieve(
        RequestContext(user_id="user-1", request_id=f"after-{failure_mode}"),
        RetrievalRequest(query="database", limit=5, token_budget=1000),
    )
    assert current.included_memory_ids == ("target",)


def test_supersession_filtering_preserves_exact_tie_ranking(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=EMBEDDING.model_id,
        vector_dimension=EMBEDDING.dimension,
        create_if_missing=True,
    )
    memories = (
        replace(
            _memory("newer-inferred", authority=EvidenceAuthority.INFERRED),
            created_at=NOW - timedelta(hours=1),
            valid_from=NOW - timedelta(hours=1),
        ),
        replace(
            _memory("authority-explicit"),
            created_at=NOW - timedelta(hours=2),
            valid_from=NOW - timedelta(hours=2),
        ),
        replace(
            _memory("authority-inferred", authority=EvidenceAuthority.INFERRED),
            created_at=NOW - timedelta(hours=2),
            valid_from=NOW - timedelta(hours=2),
        ),
        replace(
            _memory("stable-a"),
            created_at=NOW - timedelta(hours=3),
            valid_from=NOW - timedelta(hours=3),
        ),
        replace(
            _memory("stable-z"),
            created_at=NOW - timedelta(hours=3),
            valid_from=NOW - timedelta(hours=3),
        ),
        replace(
            _memory("superseded-newest"),
            created_at=NOW,
            valid_from=NOW,
            lifecycle_status=LifecycleStatus.SUPERSEDED,
            superseded_by="newer-inferred",
        ),
    )
    vector_ids: dict[str, int] = {}
    for memory in memories:
        persisted = repository.persist_pending(
            memory=memory,
            embedding=EMBEDDING,
            idempotency_key=f"key-{memory.memory_id}",
            request_fingerprint=memory.memory_id,
        )
        vector_index.add(vector_id=persisted.vector_id, embedding=EMBEDDING)
        repository.mark_indexed(user_id="user-1", memory_id=memory.memory_id)
        vector_ids[memory.memory_id] = persisted.vector_id
    service = MemoryService(
        idempotency=repository,
        embedder=_ObservingEmbedder([]),
        repository=repository,
        vector_index=vector_index,
        token_counter=_CharacterCounter(),
        clock=_FixedClock(),
        memory_ids=_UnusedReplacementId(),
        relevance_threshold=0.50,
    )

    allowed = repository.current_state_vector_ids(user_id="user-1", now=NOW)
    result = service.retrieve(
        RequestContext(user_id="user-1", request_id="ranking-preservation"),
        RetrievalRequest(query="database", limit=10, token_budget=10_000),
    )

    assert vector_ids["superseded-newest"] not in allowed
    assert result.included_memory_ids == (
        "newer-inferred",
        "authority-explicit",
        "authority-inferred",
        "stable-a",
        "stable-z",
    )
    assert "superseded-newest" not in result.context


def test_demo_supersession_prints_relationships_read_after_restart(
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
        lambda: _CharacterCounter(),
    )

    assert _demo_supersession() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["relationships"] == {
        "replacement_supersedes": ["m4-original-memory"],
        "original_superseded_by": "m4-replacement-memory",
        "atomic_bidirectional_commit": True,
    }
