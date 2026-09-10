from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conversational_memory.application import Embedding
from conversational_memory.application.recovery import RecoveryReadiness
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.infrastructure.sqlite import SQLiteMemoryRepository

NOW = datetime(2026, 9, 9, tzinfo=UTC)
EMBEDDING = Embedding(values=(1.0, 0.0), model_id="test-model", dimension=2)


def _memory(memory_id: str, state: IndexingState = IndexingState.PENDING) -> MemoryRecord:
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
        indexing_state=state,
    )


def _persist(repository: SQLiteMemoryRepository, memory_id: str) -> int:
    persisted = repository.persist_pending(
        memory=_memory(memory_id),
        embedding=EMBEDDING,
        idempotency_key=f"key-{memory_id}",
        request_fingerprint=memory_id.ljust(64, "0"),
    )
    return persisted.vector_id


def _inventory(path: Path):
    return SQLiteMemoryRepository(path).recovery_inventory(
        embedding_model="test-model", vector_dimension=2
    )


def test_inventory_classifies_valid_indexed_pending_and_failed_mappings(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(path)
    indexed = _persist(repository, "indexed")
    pending = _persist(repository, "pending")
    failed = _persist(repository, "failed")
    repository.mark_indexed(user_id="user-1", memory_id="indexed")
    repository.mark_failed(user_id="user-1", memory_id="failed", reason="forced")

    inventory = _inventory(path)

    assert inventory.readiness is RecoveryReadiness.DEGRADED
    assert inventory.reason == "degraded_excluded_work_pending"
    assert [(item.memory_id, item.vector_id) for item in inventory.rebuild_items] == [
        ("indexed", indexed),
        ("pending", pending),
        ("failed", failed),
    ]
    assert (inventory.pending_count, inventory.failed_count) == (1, 1)


def test_inventory_excludes_tombstones_and_classifies_forgetting(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    repository = SQLiteMemoryRepository(path)
    _persist(repository, "forgotten")
    repository.begin_forgetting(user_id="user-1", memory_id="forgotten", requested_at=NOW)

    inventory = _inventory(path)

    assert inventory.readiness is RecoveryReadiness.DEGRADED
    assert inventory.rebuild_items == ()
    assert inventory.cleanup_pending_count == 1


def test_indexed_missing_embedding_or_mapping_is_unavailable(tmp_path: Path) -> None:
    for missing_table in ("memory_embeddings", "memory_vector_mappings"):
        path = tmp_path / f"{missing_table}.sqlite3"
        repository = SQLiteMemoryRepository(path)
        _persist(repository, "memory-1")
        repository.mark_indexed(user_id="user-1", memory_id="memory-1")
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(f"DELETE FROM {missing_table} WHERE memory_id = 'memory-1'")
            connection.commit()

        inventory = _inventory(path)

        assert inventory.readiness is RecoveryReadiness.UNAVAILABLE
        assert inventory.reason == "unavailable_authoritative_identity"


def test_pending_missing_embedding_or_mapping_is_degraded(tmp_path: Path) -> None:
    for missing_table in ("memory_embeddings", "memory_vector_mappings"):
        path = tmp_path / f"pending-{missing_table}.sqlite3"
        repository = SQLiteMemoryRepository(path)
        _persist(repository, "memory-1")
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(f"DELETE FROM {missing_table} WHERE memory_id = 'memory-1'")
            connection.commit()

        inventory = _inventory(path)

        assert inventory.readiness is RecoveryReadiness.DEGRADED
        assert inventory.reason == "degraded_excluded_work_pending"
        assert inventory.rebuild_items == ()


def test_duplicate_or_mismatched_mapping_is_unavailable(tmp_path: Path) -> None:
    duplicate_path = tmp_path / "duplicate.sqlite3"
    duplicate = SQLiteMemoryRepository(duplicate_path)
    _persist(duplicate, "memory-1")
    with sqlite3.connect(duplicate_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE memory_vector_mappings RENAME TO old_mappings")
        connection.execute(
            "CREATE TABLE memory_vector_mappings(vector_id INTEGER, memory_id TEXT)"
        )
        connection.execute(
            "INSERT INTO memory_vector_mappings SELECT vector_id, memory_id FROM old_mappings"
        )
        connection.execute(
            "INSERT INTO memory_vector_mappings VALUES (999, 'memory-1')"
        )
        connection.commit()
    assert _inventory(duplicate_path).reason == "unavailable_schema_or_migration"

    mismatch_path = tmp_path / "mismatch.sqlite3"
    mismatch = SQLiteMemoryRepository(mismatch_path)
    _persist(mismatch, "memory-1")
    mismatch.begin_forgetting(user_id="user-1", memory_id="memory-1", requested_at=NOW)
    with sqlite3.connect(mismatch_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "UPDATE memory_forgetting SET vector_id = vector_id + 100 WHERE memory_id = 'memory-1'"
        )
        connection.commit()
    assert _inventory(mismatch_path).reason == "unavailable_authoritative_identity"


def _complete_forgetting(
    repository: SQLiteMemoryRepository,
    memory_id: str,
) -> int:
    state = repository.begin_forgetting(
        user_id="user-1", memory_id=memory_id, requested_at=NOW
    )
    assert state is not None and state.vector_id is not None
    repository.acknowledge_forgetting_complete(
        user_id="user-1",
        memory_id=memory_id,
        vector_id=state.vector_id,
        completed_at=NOW,
    )
    return state.vector_id


def test_completed_forgetting_id_cannot_collide_with_live_mapping(
    tmp_path: Path,
) -> None:
    path = tmp_path / "completed-live-collision.sqlite3"
    repository = SQLiteMemoryRepository(path)
    _persist(repository, "forgotten")
    _complete_forgetting(repository, "forgotten")
    live_vector_id = _persist(repository, "live")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE memory_forgetting SET vector_id = CAST(? AS BLOB) "
            "WHERE memory_id = 'forgotten'",
            (str(live_vector_id),),
        )
        connection.commit()

    assert _inventory(path).reason == "unavailable_authoritative_identity"


def test_completed_forgetting_ids_must_be_globally_unique(tmp_path: Path) -> None:
    path = tmp_path / "completed-forgetting-collision.sqlite3"
    repository = SQLiteMemoryRepository(path)
    _persist(repository, "first")
    first_vector_id = _complete_forgetting(repository, "first")
    _persist(repository, "second")
    _complete_forgetting(repository, "second")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE memory_forgetting SET vector_id = CAST(? AS BLOB) "
            "WHERE memory_id = 'second'",
            (str(first_vector_id),),
        )
        connection.commit()

    assert _inventory(path).reason == "unavailable_authoritative_identity"


@pytest.mark.parametrize(
    ("complete", "statement", "parameters"),
    (
        (
            False,
            "UPDATE memory_forgetting SET requested_at = ?",
            ("2026-09-09T00:00:00",),
        ),
        (
            False,
            "UPDATE memories SET deleted_at = ?",
            ("2026-09-09T01:00:00+01:00",),
        ),
        (
            False,
            "UPDATE memory_forgetting SET requested_at = ?",
            ("2026-09-10T00:00:00+00:00",),
        ),
        (
            True,
            "UPDATE memory_forgetting SET completed_at = ?",
            ("2026-09-09T00:00:00",),
        ),
    ),
    ids=("naive-request", "non-utc-deletion", "timestamp-mismatch", "naive-completion"),
)
def test_forgetting_timestamp_corruption_is_unavailable(
    tmp_path: Path,
    complete: bool,
    statement: str,
    parameters: tuple[str, ...],
) -> None:
    path = tmp_path / f"timestamp-{complete}-{parameters[0]}.sqlite3"
    repository = SQLiteMemoryRepository(path)
    _persist(repository, "forgotten")
    if complete:
        _complete_forgetting(repository, "forgotten")
    else:
        repository.begin_forgetting(
            user_id="user-1", memory_id="forgotten", requested_at=NOW
        )
    with sqlite3.connect(path) as connection:
        connection.execute(statement, parameters)
        connection.commit()

    assert _inventory(path).reason == "unavailable_authoritative_identity"


def test_model_and_dimension_mismatch_classification(tmp_path: Path) -> None:
    indexed_path = tmp_path / "indexed.sqlite3"
    indexed = SQLiteMemoryRepository(indexed_path)
    _persist(indexed, "memory-1")
    indexed.mark_indexed(user_id="user-1", memory_id="memory-1")
    with sqlite3.connect(indexed_path) as connection:
        connection.execute(
            "UPDATE memory_embeddings SET embedding_model = 'other' WHERE memory_id = 'memory-1'"
        )
        connection.commit()
    assert _inventory(indexed_path).reason == "unavailable_embedding_configuration"

    pending_path = tmp_path / "pending.sqlite3"
    pending = SQLiteMemoryRepository(pending_path)
    _persist(pending, "memory-1")
    with sqlite3.connect(pending_path) as connection:
        connection.execute(
            "UPDATE memory_embeddings SET embedding_dimension = 3 WHERE memory_id = 'memory-1'"
        )
        connection.commit()
    result = _inventory(pending_path)
    assert result.readiness is RecoveryReadiness.DEGRADED
    assert result.rebuild_items == ()


def test_integrity_and_migration_failures_are_unavailable(tmp_path: Path) -> None:
    migration_path = tmp_path / "migration.sqlite3"
    migration_repository = SQLiteMemoryRepository(migration_path)
    with sqlite3.connect(migration_path) as connection:
        connection.execute("UPDATE schema_migrations SET checksum = 'bad' WHERE version = 1")
        connection.commit()
    assert migration_repository.recovery_inventory(
        embedding_model="test-model", vector_dimension=2
    ).reason == "unavailable_schema_or_migration"

    integrity_path = tmp_path / "integrity.sqlite3"
    repository = SQLiteMemoryRepository(integrity_path)
    _persist(repository, "memory-1")
    with sqlite3.connect(integrity_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM memories WHERE memory_id = 'memory-1'")
        connection.commit()
    assert _inventory(integrity_path).reason == "unavailable_sqlite_integrity"


def test_native_sqlite_integrity_check_failure_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "native-integrity.sqlite3"
    repository = SQLiteMemoryRepository(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_master SET rootpage = 9 "
            "WHERE name = 'sqlite_autoindex_memory_embeddings_1'"
        )
        connection.commit()

    inventory = repository.recovery_inventory(
        embedding_model="test-model", vector_dimension=2
    )

    assert inventory.reason == "unavailable_sqlite_integrity"


def test_empty_valid_inventory_is_ready_and_immutable(tmp_path: Path) -> None:
    result = _inventory(tmp_path / "memory.sqlite3")
    assert result.readiness is RecoveryReadiness.READY
    assert result.reason == "ready_existing_generation"
    assert result.rebuild_items == ()
    assert replace(result) == result


def test_schema_constraints_and_supersession_relationships_are_audited(
    tmp_path: Path,
) -> None:
    schema_path = tmp_path / "schema.sqlite3"
    SQLiteMemoryRepository(schema_path)
    with sqlite3.connect(schema_path) as connection:
        connection.execute("ALTER TABLE memory_embeddings RENAME TO altered_embeddings")
        connection.commit()
    assert _inventory(schema_path).reason == "unavailable_schema_or_migration"

    relationship_path = tmp_path / "relationship.sqlite3"
    relationship = SQLiteMemoryRepository(relationship_path)
    _persist(relationship, "memory-1")
    relationship.mark_indexed(user_id="user-1", memory_id="memory-1")
    with sqlite3.connect(relationship_path) as connection:
        connection.execute(
            "UPDATE memories SET lifecycle_status = 'superseded', superseded_by = 'missing'"
        )
        connection.commit()
    assert _inventory(relationship_path).reason == "unavailable_authoritative_identity"


def test_self_link_and_supersession_cycles_are_unavailable(tmp_path: Path) -> None:
    self_path = tmp_path / "self-link.sqlite3"
    self_repository = SQLiteMemoryRepository(self_path)
    _persist(self_repository, "self")
    self_repository.mark_indexed(user_id="user-1", memory_id="self")
    with sqlite3.connect(self_path) as connection:
        connection.execute(
            "UPDATE memories SET lifecycle_status = 'superseded', "
            "supersedes_json = '[\"self\"]', superseded_by = 'self'"
        )
        connection.commit()
    assert _inventory(self_path).reason == "unavailable_authoritative_identity"

    cycle_path = tmp_path / "cycle.sqlite3"
    cycle_repository = SQLiteMemoryRepository(cycle_path)
    for memory_id in ("first", "second"):
        _persist(cycle_repository, memory_id)
        cycle_repository.mark_indexed(user_id="user-1", memory_id=memory_id)
    with sqlite3.connect(cycle_path) as connection:
        connection.execute(
            "UPDATE memories SET lifecycle_status = 'superseded', "
            "supersedes_json = '[\"second\"]', superseded_by = 'second' "
            "WHERE memory_id = 'first'"
        )
        connection.execute(
            "UPDATE memories SET lifecycle_status = 'superseded', "
            "supersedes_json = '[\"first\"]', superseded_by = 'first' "
            "WHERE memory_id = 'second'"
        )
        connection.commit()
    assert _inventory(cycle_path).reason == "unavailable_authoritative_identity"


def test_nonindexed_replacement_link_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "pending-replacement.sqlite3"
    repository = SQLiteMemoryRepository(path)
    _persist(repository, "target")
    repository.mark_indexed(user_id="user-1", memory_id="target")
    _persist(repository, "replacement")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE memories SET lifecycle_status = 'superseded', "
            "superseded_by = 'replacement' WHERE memory_id = 'target'"
        )
        connection.execute(
            "UPDATE memories SET supersedes_json = '[\"target\"]' "
            "WHERE memory_id = 'replacement'"
        )
        connection.commit()
    assert _inventory(path).reason == "unavailable_authoritative_identity"


def _persist_valid_supersession_pair(path: Path) -> None:
    repository = SQLiteMemoryRepository(path)
    _persist(repository, "target")
    _persist(repository, "replacement")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE memories SET subject = 'topic'")
        connection.commit()
    repository.mark_indexed(user_id="user-1", memory_id="target")
    repository.acknowledge_supersession(
        user_id="user-1",
        replacement_memory_id="replacement",
        target_memory_id="target",
    )


@pytest.mark.parametrize(
    ("name", "corruption"),
    (
        (
            "nonindexed-target",
            (
                "UPDATE memories SET indexing_state = 'pending' "
                "WHERE memory_id = 'target'"
            ),
        ),
        (
            "inferred-replacement",
            (
                "UPDATE memories SET provenance_authority = 'inferred' "
                "WHERE memory_id = 'replacement'"
            ),
        ),
        (
            "unequal-normalized-subject",
            (
                "UPDATE memories SET subject = 'different topic' "
                "WHERE memory_id = 'replacement'"
            ),
        ),
        (
            "unequal-memory-type",
            (
                "UPDATE memories SET memory_type = 'preference' "
                "WHERE memory_id = 'replacement'"
            ),
        ),
    ),
)
def test_m4_invalid_supersession_edges_are_unavailable(
    tmp_path: Path,
    name: str,
    corruption: str,
) -> None:
    path = tmp_path / f"{name}.sqlite3"
    _persist_valid_supersession_pair(path)
    with sqlite3.connect(path) as connection:
        connection.execute(corruption)
        connection.commit()

    inventory = _inventory(path)

    assert inventory.readiness is RecoveryReadiness.UNAVAILABLE
    assert inventory.reason == "unavailable_authoritative_identity"


def test_replacement_with_multiple_supersession_targets_is_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "multiple-targets.sqlite3"
    _persist_valid_supersession_pair(path)
    repository = SQLiteMemoryRepository(path)
    _persist(repository, "second-target")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE memories SET subject = 'topic', indexing_state = 'indexed', "
            "lifecycle_status = 'superseded', superseded_by = 'replacement' "
            "WHERE memory_id = 'second-target'"
        )
        connection.execute(
            "UPDATE memories SET supersedes_json = "
            "'[\"target\",\"second-target\"]' WHERE memory_id = 'replacement'"
        )
        connection.commit()

    inventory = _inventory(path)

    assert inventory.readiness is RecoveryReadiness.UNAVAILABLE
    assert inventory.reason == "unavailable_authoritative_identity"


def test_replacement_with_duplicate_supersession_targets_is_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate-targets.sqlite3"
    _persist_valid_supersession_pair(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE memories SET supersedes_json = '[\"target\",\"target\"]' "
            "WHERE memory_id = 'replacement'"
        )
        connection.commit()

    inventory = _inventory(path)

    assert inventory.readiness is RecoveryReadiness.UNAVAILABLE
    assert inventory.reason == "unavailable_authoritative_identity"
