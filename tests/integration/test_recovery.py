from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest

from conversational_memory import composition
from conversational_memory.application import (
    AdmissionRequest,
    Embedding,
    ForgetRequest,
    IndexingError,
    MemoryService,
    RequestContext,
    StorageError,
)
from conversational_memory.application.recovery import (
    RecoveryCoordinator,
    RecoveryIndexError,
    RecoveryInventory,
    RecoveryPublication,
    RecoveryReadiness,
)
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.entrypoints.cli import _demo_recovery, build_parser
from conversational_memory.infrastructure import (
    ALL_MPNET_BASE_V2_DIMENSION,
    ALL_MPNET_BASE_V2_MODEL_ID,
    FaissRecoveryAdapter,
    FaissVectorIndex,
)
from conversational_memory.infrastructure.sqlite import SQLiteMemoryRepository

NOW = datetime(2026, 9, 9, tzinfo=UTC)
EMBEDDING = Embedding(values=(1.0, 0.0), model_id="test-model", dimension=2)


class _DemoEmbedder:
    def embed(self, content: str) -> Embedding:
        values = [0.0] * ALL_MPNET_BASE_V2_DIMENSION
        values[0] = 1.0
        return Embedding(
            values=tuple(values),
            model_id=ALL_MPNET_BASE_V2_MODEL_ID,
            dimension=ALL_MPNET_BASE_V2_DIMENSION,
        )


class _DemoCounter:
    tokenizer_id = "cl100k_base"

    def count_tokens(self, text: str) -> int:
        return len(text)


def test_cli_exposes_demo_recovery() -> None:
    assert build_parser().parse_args(["demo-recovery"]).command == "demo-recovery"


def _memory(memory_id: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id="user-1",
        content=f"memory {memory_id}",
        memory_type=MemoryType.FACT,
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


def _persist(repository: SQLiteMemoryRepository, memory_id: str) -> None:
    repository.persist_pending(
        memory=_memory(memory_id),
        embedding=EMBEDDING,
        idempotency_key=f"key-{memory_id}",
        request_fingerprint=memory_id.ljust(64, "0"),
    )


def _coordinator(path: Path, index: Path) -> RecoveryCoordinator:
    return RecoveryCoordinator(
        inventory=SQLiteMemoryRepository(path),
        vector_index=FaissRecoveryAdapter(
            index, embedding_model="test-model", vector_dimension=2
        ),
        embedding_model="test-model",
        vector_dimension=2,
        clock=_Clock(),
    )


class _Clock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return NOW


class _Embedder:
    def embed(self, content: str) -> Embedding:
        assert content
        return EMBEDDING


class _Counter:
    tokenizer_id = "cl100k_base"

    def count_tokens(self, text: str) -> int:
        return len(text)


class _Ids:
    def __init__(self, *memory_ids: str) -> None:
        self._memory_ids = iter(memory_ids)

    def new_id(self) -> str:
        return next(self._memory_ids)


def _request(
    key: str, *, target: str | None = None, subject: str | None = None
) -> AdmissionRequest:
    return AdmissionRequest(
        idempotency_key=key,
        conversation_id="conversation",
        turn_id=f"turn-{key}",
        content=f"memory {key}",
        memory_type="fact",
        subject=subject,
        value=key,
        source_type="explicit_user",
        supersedes_memory_id=target,
    )


def _service(
    repository: SQLiteMemoryRepository,
    index: FaissVectorIndex,
    *memory_ids: str,
) -> MemoryService:
    return MemoryService(
        idempotency=repository,
        embedder=_Embedder(),
        repository=repository,
        vector_index=index,
        token_counter=_Counter(),
        clock=_Clock(),
        memory_ids=_Ids(*memory_ids),
        relevance_threshold=0.50,
    )


class _FailAcknowledgementRepository(SQLiteMemoryRepository):
    def __init__(self, database_path: Path) -> None:
        self.inventory_calls = 0
        super().__init__(database_path)

    def recovery_inventory(self, **kwargs):
        self.inventory_calls += 1
        return super().recovery_inventory(**kwargs)

    def acknowledge_forgetting_complete(self, **kwargs):
        raise StorageError("forced acknowledgement failure")


class _FailSecondAcknowledgementRepository(SQLiteMemoryRepository):
    def __init__(self, database_path: Path) -> None:
        self.acknowledgements = 0
        super().__init__(database_path)

    def acknowledge_forgetting_complete(self, **kwargs):
        self.acknowledgements += 1
        if self.acknowledgements == 2:
            raise StorageError("forced later acknowledgement failure")
        return super().acknowledge_forgetting_complete(**kwargs)


class _FailSupersessionRepository(SQLiteMemoryRepository):
    def acknowledge_supersession(self, **kwargs):
        raise StorageError("forced interrupted supersession acknowledgement")


def test_coordinator_rebuilds_then_reuses_clean_generation(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    index = tmp_path / "index"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "memory-1")
    repository.mark_indexed(user_id="user-1", memory_id="memory-1")

    first = _coordinator(database, index).recover()
    second = _coordinator(database, index).recover()

    assert first.readiness is RecoveryReadiness.READY
    assert first.reason == "ready_rebuilt_generation"
    assert first.rebuilt is True
    assert first.vector_count == 1
    assert second.reason == "ready_existing_generation"
    assert second.rebuilt is False


def test_coordinator_reports_safely_excluded_pending_and_failed_work(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "pending")
    _persist(repository, "failed")
    repository.mark_failed(user_id="user-1", memory_id="failed", reason="forced")

    result = _coordinator(database, tmp_path / "index").recover()

    assert result.readiness is RecoveryReadiness.DEGRADED
    assert result.reason == "degraded_excluded_work_pending"
    assert (result.pending_count, result.failed_count) == (1, 1)
    assert result.vector_count == 2


def test_coordinator_never_rebuilds_an_unsafe_inventory(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "memory-1")
    repository.mark_indexed(user_id="user-1", memory_id="memory-1")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "DELETE FROM memory_vector_mappings WHERE memory_id = 'memory-1'"
        )
        connection.commit()

    index = tmp_path / "index"
    result = _coordinator(database, index).recover()

    assert result.readiness is RecoveryReadiness.UNAVAILABLE
    assert result.reason == "unavailable_authoritative_identity"
    assert not index.exists()


def test_coordinator_translates_publication_failure_to_unavailable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "memory-1")
    repository.mark_indexed(user_id="user-1", memory_id="memory-1")
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked")

    result = _coordinator(database, blocked).recover()

    assert result.readiness is RecoveryReadiness.UNAVAILABLE
    assert result.reason == "unavailable_publication"
    assert result.rebuilt is False


def test_tombstoned_memory_remains_excluded_during_recovery(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "forgotten")
    repository.mark_indexed(user_id="user-1", memory_id="forgotten")
    repository.begin_forgetting(user_id="user-1", memory_id="forgotten", requested_at=NOW)

    result = _coordinator(database, tmp_path / "index").recover()

    assert result.readiness is RecoveryReadiness.READY
    assert result.cleanup_pending_count == 0
    assert result.vector_count == 0


def test_known_id_cleanup_is_completed_after_verified_absence_and_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    index_path = tmp_path / "index"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "forgotten")
    repository.mark_indexed(user_id="user-1", memory_id="forgotten")
    repository.begin_forgetting(user_id="user-1", memory_id="forgotten", requested_at=NOW)
    clock = _Clock()
    coordinator = RecoveryCoordinator(
        inventory=repository,
        vector_index=FaissRecoveryAdapter(
            index_path, embedding_model="test-model", vector_dimension=2
        ),
        embedding_model="test-model",
        vector_dimension=2,
        clock=clock,
    )

    first = coordinator.recover()
    metadata = (index_path / "memory.faiss.meta.json").read_bytes()
    second = _coordinator(database, index_path).recover()

    assert first.readiness is RecoveryReadiness.READY
    assert first.cleanup_pending_count == 0
    assert clock.calls == 1
    assert second.readiness is RecoveryReadiness.READY
    assert second.rebuilt is False
    assert (index_path / "memory.faiss.meta.json").read_bytes() == metadata
    assert repository.find_forgetting_target(
        user_id="user-1", memory_id="forgotten"
    ).forgetting.completed_at == NOW


def test_null_id_cleanup_without_mapping_stays_degraded_with_empty_generation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    index_path = tmp_path / "index"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "forgotten")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM memory_vector_mappings WHERE memory_id = 'forgotten'"
        )
        connection.commit()
    repository.begin_forgetting(user_id="user-1", memory_id="forgotten", requested_at=NOW)
    clock = _Clock()
    result = RecoveryCoordinator(
        inventory=repository,
        vector_index=FaissRecoveryAdapter(
            index_path, embedding_model="test-model", vector_dimension=2
        ),
        embedding_model="test-model",
        vector_dimension=2,
        clock=clock,
    ).recover()

    assert result.readiness is RecoveryReadiness.DEGRADED
    assert result.cleanup_pending_count == 1
    assert clock.calls == 0
    assert json.loads((index_path / "memory.faiss.meta.json").read_text())[
        "vector_count"
    ] == 0


def test_null_id_cleanup_adopts_one_later_mapping_then_completes(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    index_path = tmp_path / "index"
    repository = SQLiteMemoryRepository(database)
    vector_id = 41
    _persist(repository, "forgotten")
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM memory_vector_mappings WHERE memory_id = 'forgotten'")
        connection.commit()
    repository.begin_forgetting(user_id="user-1", memory_id="forgotten", requested_at=NOW)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO memory_vector_mappings(vector_id, memory_id) VALUES (?, 'forgotten')",
            (vector_id,),
        )
        connection.commit()
    index = FaissVectorIndex(
        index_path,
        embedding_model="test-model",
        vector_dimension=2,
        create_if_missing=True,
    )
    index.add(vector_id=vector_id, embedding=EMBEDDING)

    result = _coordinator(database, index_path).recover()

    target = repository.find_forgetting_target(user_id="user-1", memory_id="forgotten")
    assert result.readiness is RecoveryReadiness.READY
    assert target is not None and target.vector_id is None
    assert target.forgetting is not None
    assert target.forgetting.vector_id == vector_id
    assert target.forgetting.completed_at == NOW


def test_acknowledgement_failure_retains_pending_tombstone_and_mapping(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    index_path = tmp_path / "index"
    seed = SQLiteMemoryRepository(database)
    _persist(seed, "forgotten")
    seed.mark_indexed(user_id="user-1", memory_id="forgotten")
    pending = seed.begin_forgetting(
        user_id="user-1", memory_id="forgotten", requested_at=NOW
    )
    repository = _FailAcknowledgementRepository(database)

    result = RecoveryCoordinator(
        inventory=repository,
        vector_index=FaissRecoveryAdapter(
            index_path, embedding_model="test-model", vector_dimension=2
        ),
        embedding_model="test-model",
        vector_dimension=2,
        clock=_Clock(),
    ).recover()

    stored = seed.find_forgetting_target(user_id="user-1", memory_id="forgotten")
    assert result.readiness is RecoveryReadiness.DEGRADED
    assert stored is not None and stored.vector_id == pending.vector_id
    assert stored.memory.deleted_at == NOW
    assert stored.forgetting == pending
    assert repository.inventory_calls == 2


def test_partial_cleanup_refreshes_counts_after_one_later_failure(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    index_path = tmp_path / "index"
    seed = SQLiteMemoryRepository(database)
    for memory_id in ("first", "second"):
        _persist(seed, memory_id)
        seed.mark_indexed(user_id="user-1", memory_id=memory_id)
        seed.begin_forgetting(
            user_id="user-1", memory_id=memory_id, requested_at=NOW
        )
    repository = _FailSecondAcknowledgementRepository(database)

    result = RecoveryCoordinator(
        inventory=repository,
        vector_index=FaissRecoveryAdapter(
            index_path, embedding_model="test-model", vector_dimension=2
        ),
        embedding_model="test-model",
        vector_dimension=2,
        clock=_Clock(),
    ).recover()

    first = seed.find_forgetting_target(user_id="user-1", memory_id="first")
    second = seed.find_forgetting_target(user_id="user-1", memory_id="second")
    assert result.readiness is RecoveryReadiness.DEGRADED
    assert result.cleanup_pending_count == 1
    assert result.vector_count == 0
    assert first is not None and first.forgetting is not None
    assert first.forgetting.completed_at == NOW
    assert first.vector_id is None
    assert second is not None and second.forgetting is not None
    assert second.forgetting.completed_at is None
    assert second.vector_id is not None


def test_ambiguous_pending_supersession_stays_unlinked_and_degraded(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "target")
    repository.mark_indexed(user_id="user-1", memory_id="target")
    _persist(repository, "ambiguous-replacement")

    result = _coordinator(database, tmp_path / "index").recover()

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT memory_id, indexing_state, lifecycle_status, supersedes_json, superseded_by FROM memories ORDER BY memory_id"
        ).fetchall()
    assert result.readiness is RecoveryReadiness.DEGRADED
    assert rows == [
        ("ambiguous-replacement", "pending", "active", "[]", None),
        ("target", "indexed", "active", "[]", None),
    ]


def test_interrupted_targeted_supersession_stays_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    index_path = tmp_path / "index"
    seed = SQLiteMemoryRepository(database)
    index = FaissVectorIndex(
        index_path,
        embedding_model="test-model",
        vector_dimension=2,
        create_if_missing=True,
    )
    _persist(seed, "target")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE memories SET subject = 'topic' WHERE memory_id = 'target'"
        )
        connection.commit()
    index.add(vector_id=1, embedding=EMBEDDING)
    seed.mark_indexed(user_id="user-1", memory_id="target")
    interrupted_repository = _FailSupersessionRepository(database)
    interrupted = _service(interrupted_repository, index, "replacement").admit(
        RequestContext(user_id="user-1", request_id="correction"),
        _request("correction", target="target", subject="topic"),
    )

    assert interrupted.reason == "supersession_acknowledgement_failed"
    result = _coordinator(database, index_path).recover()

    assert result.readiness is RecoveryReadiness.DEGRADED
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT memory_id, indexing_state, lifecycle_status, "
            "supersedes_json, superseded_by FROM memories ORDER BY memory_id"
        ).fetchall()
    assert rows == [
        ("replacement", "pending", "active", "[]", None),
        ("target", "indexed", "active", "[]", None),
    ]


class _FailureIndex:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def reconcile(self, inventory: RecoveryInventory) -> RecoveryPublication:
        raise RecoveryIndexError(self.reason)


def test_coordinator_preserves_all_approved_failure_reason_families(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    inventory = repository.recovery_inventory(
        embedding_model="test-model", vector_dimension=2
    )
    for reason in (
        "unavailable_rebuild",
        "unavailable_publication",
        "unavailable_post_publication_verification",
    ):
        result = RecoveryCoordinator(
            inventory=repository,
            vector_index=_FailureIndex(reason),
            embedding_model="test-model",
            vector_dimension=2,
            clock=_Clock(),
        ).recover()
        assert result.readiness is RecoveryReadiness.UNAVAILABLE
        assert result.reason == reason
        assert inventory.rebuild_items == ()


@pytest.mark.parametrize(
    ("stage", "reason"),
    (
        ("build", "unavailable_rebuild"),
        ("publication", "unavailable_publication"),
        ("verification", "unavailable_post_publication_verification"),
    ),
)
def test_coordinator_classifies_real_faiss_stage_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    reason: str,
) -> None:
    database = tmp_path / f"{stage}.sqlite3"
    repository = SQLiteMemoryRepository(database)
    _persist(repository, "memory-1")
    repository.mark_indexed(user_id="user-1", memory_id="memory-1")
    if stage == "build":
        monkeypatch.setattr(
            "conversational_memory.infrastructure.faiss_index.faiss.write_index",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("forced build failure")),
        )
    elif stage == "publication":
        monkeypatch.setattr(
            "conversational_memory.infrastructure.faiss_index.os.replace",
            lambda *_args: (_ for _ in ()).throw(OSError("forced publication failure")),
        )
    else:
        original = FaissVectorIndex._verify_pair
        calls = 0

        def fail_final(self: FaissVectorIndex, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise IndexingError("forced final verification failure")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(FaissVectorIndex, "_verify_pair", fail_final)

    result = _coordinator(database, tmp_path / f"{stage}-index").recover()

    assert result.readiness is RecoveryReadiness.UNAVAILABLE
    assert result.reason == reason


class _BlockingRepository(SQLiteMemoryRepository):
    def __init__(self, database_path: Path, entered: Event, release: Event) -> None:
        self.entered = entered
        self.release = release
        super().__init__(database_path)

    def recovery_inventory(self, **kwargs):
        self.entered.set()
        assert self.release.wait(timeout=5)
        return super().recovery_inventory(**kwargs)


class _CrossWorkflowRepository(_BlockingRepository):
    def __init__(self, database_path: Path, entered: Event, release: Event) -> None:
        self.observe_operations = False
        self.operation_entered = Event()
        super().__init__(database_path, entered, release)

    def find(self, **kwargs):
        if self.observe_operations:
            self.operation_entered.set()
        return super().find(**kwargs)

    def find_forgetting_target(self, **kwargs):
        if self.observe_operations:
            self.operation_entered.set()
        return super().find_forgetting_target(**kwargs)


def test_recovery_shared_lock_serializes_and_releases_after_failure(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    entered = Event()
    release = Event()
    blocked = _BlockingRepository(database, entered, release)
    normal = SQLiteMemoryRepository(database)
    recovery = RecoveryCoordinator(
        inventory=blocked,
        vector_index=_FailureIndex("unavailable_publication"),
        embedding_model="test-model",
        vector_dimension=2,
        clock=_Clock(),
    )
    second_entered = Event()

    def second_recovery():
        second_entered.set()
        return _coordinator(database, tmp_path / "second-index").recover()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(recovery.recover)
        assert entered.wait(timeout=5)
        second_future = executor.submit(second_recovery)
        assert second_entered.wait(timeout=5)
        assert second_future.done() is False
        release.set()
        assert first_future.result().reason == "unavailable_publication"
        assert second_future.result().readiness is RecoveryReadiness.READY
    assert normal.recovery_inventory(
        embedding_model="test-model", vector_dimension=2
    ).readiness is RecoveryReadiness.READY


@pytest.mark.parametrize("workflow", ("admission", "retry", "forgetting"))
def test_recovery_serializes_other_write_workflows_and_releases_after_failure(
    tmp_path: Path,
    workflow: str,
) -> None:
    database = tmp_path / f"{workflow}.sqlite3"
    index_path = tmp_path / f"{workflow}-index"
    entered = Event()
    release = Event()
    repository = _CrossWorkflowRepository(database, entered, release)
    index = FaissVectorIndex(
        index_path,
        embedding_model="test-model",
        vector_dimension=2,
        create_if_missing=True,
    )
    service = _service(repository, index, "seed", "new")
    context = RequestContext(user_id="user-1", request_id=workflow)
    request = _request("seed")
    if workflow in {"retry", "forgetting"}:
        seeded = service.admit(context, request)
        assert seeded.memory_id == "seed"
    if workflow == "retry":
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE memories SET indexing_state = 'failed' WHERE memory_id = 'seed'"
            )
            connection.commit()

    recovery = RecoveryCoordinator(
        inventory=repository,
        vector_index=_FailureIndex("unavailable_publication"),
        embedding_model="test-model",
        vector_dimension=2,
        clock=_Clock(),
    )
    repository.observe_operations = True

    def run_workflow():
        if workflow == "admission":
            return service.admit(context, _request("new"))
        if workflow == "retry":
            return service.admit(context, request)
        return service.forget(context, ForgetRequest(memory_id="seed"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        recovery_future = executor.submit(recovery.recover)
        assert entered.wait(timeout=5)
        workflow_future = executor.submit(run_workflow)
        assert not repository.operation_entered.wait(timeout=0.1)
        release.set()
        assert recovery_future.result().reason == "unavailable_publication"
        workflow_future.result(timeout=5)
    assert repository.operation_entered.is_set()


def test_demo_recovery_prints_audited_rebuild_and_idempotent_restart(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        composition,
        "SentenceTransformerEmbedder",
        lambda *, cache_directory: _DemoEmbedder(),
    )
    monkeypatch.setattr(composition, "TiktokenTokenCounter", lambda: _DemoCounter())

    assert _demo_recovery() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["first_recovery"]["readiness"] == "ready"
    assert output["first_recovery"]["rebuilt"] is True
    assert output["first_recovery"]["orphan_vectors_removed"] == 1
    assert output["stable_mapping"]["preserved"] is True
    assert output["exclusions"]["tombstoned_absent"] is True
    assert output["second_startup"]["readiness"] == "ready"
    assert output["second_startup"]["rebuilt"] is False
    assert output["second_startup"]["durable_files_unchanged"] is True
