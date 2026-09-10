"""SQLite implementation of owner-scoped idempotency and memory persistence ports."""

from __future__ import annotations

import json
import math
import sqlite3
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from conversational_memory.application.contracts import (
    AdmissionResult,
    Embedding,
    ExistingAdmission,
    ForgettingRecord,
    ForgettingTarget,
    HydratedMemory,
    IndexingWork,
    PersistedPendingMemory,
)
from conversational_memory.application.errors import StorageError
from conversational_memory.application.recovery import (
    RecoveryCleanup,
    RecoveryInventory,
    RecoveryReadiness,
    RecoveryVector,
)
from conversational_memory.domain.eligibility import (
    is_current_state_eligible,
    is_historical_eligible,
)
from conversational_memory.domain.expiration import validate_trusted_utc
from conversational_memory.domain.forgetting import ForgettingCleanupState
from conversational_memory.domain.idempotency import normalize_text
from conversational_memory.domain.models import (
    AdmissionDecision,
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.domain.supersession import validate_supersession_target

from .migrations import (
    expected_migration_checksums,
    expected_schema_signature,
    initialize_schema,
    schema_signature,
)

_WRITE_LOCK = RLock()


class SQLiteMemoryRepository:
    """Authoritative SQLite adapter implementing both admission persistence ports."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        initialize_schema(self._database_path)

    def recovery_inventory(
        self,
        *,
        embedding_model: str,
        vector_dimension: int,
    ) -> RecoveryInventory:
        """Audit SQLite authority and return immutable future-rebuild inputs."""
        if (
            not embedding_model.strip()
            or isinstance(vector_dimension, bool)
            or vector_dimension <= 0
        ):
            return _unavailable_inventory("unavailable_embedding_configuration")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN")
                applied = tuple(
                    (int(row["version"]), str(row["checksum"]))
                    for row in connection.execute(
                        "SELECT version, checksum FROM schema_migrations ORDER BY version"
                    )
                )
                if applied != expected_migration_checksums():
                    return _unavailable_inventory("unavailable_schema_or_migration")
                if schema_signature(connection) != expected_schema_signature():
                    return _unavailable_inventory("unavailable_schema_or_migration")
                try:
                    integrity = connection.execute("PRAGMA integrity_check").fetchone()
                    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
                except sqlite3.Error:
                    return _unavailable_inventory("unavailable_sqlite_integrity")
                if integrity is None or str(integrity[0]) != "ok" or foreign_keys:
                    return _unavailable_inventory("unavailable_sqlite_integrity")
                rows = connection.execute(
                    """
                    SELECT m.*,
                           e.embedding_blob, e.embedding_model, e.embedding_dimension,
                           v.vector_id, f.vector_id AS forgetting_vector_id,
                           f.cleanup_state,
                           f.requested_at AS forgetting_requested_at,
                           f.completed_at
                    FROM memories AS m
                    LEFT JOIN memory_embeddings AS e ON e.memory_id = m.memory_id
                    LEFT JOIN memory_vector_mappings AS v ON v.memory_id = m.memory_id
                    LEFT JOIN memory_forgetting AS f ON f.memory_id = m.memory_id
                    ORDER BY v.vector_id, m.memory_id
                    """
                ).fetchall()
        except (sqlite3.Error, OSError, UnicodeError, ValueError, TypeError):
            return _unavailable_inventory("unavailable_schema_or_migration")

        return _classify_recovery_rows(
            rows,
            embedding_model=embedding_model,
            vector_dimension=vector_dimension,
        )

    def adopt_forgetting_vector_id(
        self, *, user_id: str, memory_id: str, vector_id: int
    ) -> None:
        """Atomically bind one audited later mapping to null-ID cleanup."""
        with _WRITE_LOCK:
            try:
                with self._connection() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute(
                        """
                        SELECT f.vector_id AS stored_vector_id, v.vector_id AS live_vector_id
                        FROM memory_forgetting AS f
                        JOIN memories AS m
                          ON m.memory_id = f.memory_id AND m.user_id = f.user_id
                        JOIN memory_vector_mappings AS v ON v.memory_id = f.memory_id
                        WHERE f.user_id = ? AND f.memory_id = ?
                          AND m.deleted_at IS NOT NULL
                          AND f.cleanup_state = 'cleanup_pending'
                          AND f.completed_at IS NULL
                        """,
                        (user_id, memory_id),
                    ).fetchone()
                    if (
                        row is None
                        or row["stored_vector_id"] is not None
                        or int(row["live_vector_id"]) != vector_id
                    ):
                        raise StorageError("SQLite forgetting identity adoption rejected")
                    cursor = connection.execute(
                        """
                        UPDATE memory_forgetting SET vector_id = ?
                        WHERE user_id = ? AND memory_id = ? AND vector_id IS NULL
                          AND cleanup_state = 'cleanup_pending' AND completed_at IS NULL
                        """,
                        (vector_id, user_id, memory_id),
                    )
                    if cursor.rowcount != 1:
                        raise StorageError("SQLite forgetting identity adoption rejected")
                    connection.commit()
            except StorageError:
                raise
            except sqlite3.Error as error:
                raise StorageError("SQLite forgetting identity adoption failed") from error

    def find(self, *, user_id: str, idempotency_key: str) -> ExistingAdmission | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT m.*, i.request_fingerprint,
                           e.embedding_blob, e.embedding_model, e.embedding_dimension,
                           v.vector_id
                    FROM admission_idempotency AS i
                    JOIN memories AS m ON m.memory_id = i.memory_id AND m.user_id = i.user_id
                    JOIN memory_embeddings AS e ON e.memory_id = m.memory_id
                    LEFT JOIN memory_vector_mappings AS v ON v.memory_id = m.memory_id
                    WHERE i.user_id = ? AND i.idempotency_key = ?
                    """,
                    (user_id, idempotency_key),
                ).fetchone()
            if row is None:
                return None
            memory = _row_to_memory(row)
            indexing_work = (
                None
                if row["vector_id"] is None
                else IndexingWork(
                    memory_id=memory.memory_id,
                    vector_id=int(row["vector_id"]),
                    embedding=_row_to_embedding(row),
                )
            )
        except (sqlite3.Error, ValueError, TypeError, struct.error) as error:
            raise StorageError("SQLite idempotency lookup failed") from error

        return ExistingAdmission(
            request_fingerprint=str(row["request_fingerprint"]),
            result=_result_for(memory, _optional_text(row["indexing_error"])),
            indexing_work=indexing_work,
        )

    def persist_pending(
        self,
        *,
        memory: MemoryRecord,
        embedding: Embedding,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> PersistedPendingMemory:
        if memory.indexing_state is not IndexingState.PENDING:
            raise StorageError("SQLite can persist only a pending memory")

        with _WRITE_LOCK:
            try:
                with self._connection() as connection:
                    try:
                        connection.execute("BEGIN IMMEDIATE")
                        self._insert_memory(connection, memory)
                        connection.execute(
                            """
                            INSERT INTO admission_idempotency(
                                user_id, idempotency_key, request_fingerprint, memory_id
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (
                                memory.user_id,
                                idempotency_key,
                                request_fingerprint,
                                memory.memory_id,
                            ),
                        )
                        connection.execute(
                            """
                            INSERT INTO memory_embeddings(
                                memory_id, embedding_blob, embedding_model, embedding_dimension
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (
                                memory.memory_id,
                                _embedding_blob(embedding),
                                embedding.model_id,
                                embedding.dimension,
                            ),
                        )
                        cursor = connection.execute(
                            "INSERT INTO memory_vector_mappings(memory_id) VALUES (?)",
                            (memory.memory_id,),
                        )
                        vector_id = cursor.lastrowid
                        if vector_id is None:
                            raise StorageError("SQLite did not allocate a vector ID")
                        connection.commit()
                    except (
                        sqlite3.Error,
                        StorageError,
                        TypeError,
                        ValueError,
                        OverflowError,
                        struct.error,
                    ) as error:
                        connection.rollback()
                        if isinstance(error, StorageError):
                            raise
                        if isinstance(error, sqlite3.Error):
                            raise StorageError("SQLite pending-memory transaction failed") from error
                        raise StorageError("SQLite pending-memory serialization failed") from error
            except (sqlite3.Error, StorageError) as error:
                if isinstance(error, StorageError):
                    raise
                raise StorageError("SQLite pending-memory transaction failed") from error

        return PersistedPendingMemory(memory=memory, vector_id=vector_id)

    def find_forgetting_target(
        self,
        *,
        user_id: str,
        memory_id: str,
    ) -> ForgettingTarget | None:
        """Read a target and cleanup state only within the trusted owner."""
        try:
            with self._connection() as connection:
                memory_row = connection.execute(
                    "SELECT * FROM memories WHERE user_id = ? AND memory_id = ?",
                    (user_id, memory_id),
                ).fetchone()
                if memory_row is None:
                    return None
                mapping_row = connection.execute(
                    "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()
                forgetting_row = connection.execute(
                    "SELECT * FROM memory_forgetting WHERE user_id = ? AND memory_id = ?",
                    (user_id, memory_id),
                ).fetchone()
            return ForgettingTarget(
                memory=_row_to_memory(memory_row),
                vector_id=None if mapping_row is None else int(mapping_row["vector_id"]),
                forgetting=(
                    None
                    if forgetting_row is None
                    else _row_to_forgetting(forgetting_row)
                ),
            )
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise StorageError("SQLite forgetting target lookup failed") from error

    def begin_forgetting(
        self,
        *,
        user_id: str,
        memory_id: str,
        requested_at: datetime,
    ) -> ForgettingRecord | None:
        """Atomically tombstone an owned memory and persist pending cleanup."""
        requested_time = validate_trusted_utc(requested_at)
        with _WRITE_LOCK:
            try:
                with self._connection() as connection:
                    try:
                        connection.execute("BEGIN IMMEDIATE")
                        memory_row = connection.execute(
                            "SELECT * FROM memories WHERE user_id = ? AND memory_id = ?",
                            (user_id, memory_id),
                        ).fetchone()
                        if memory_row is None:
                            connection.rollback()
                            return None
                        existing_row = connection.execute(
                            "SELECT * FROM memory_forgetting WHERE user_id = ? AND memory_id = ?",
                            (user_id, memory_id),
                        ).fetchone()
                        if existing_row is not None:
                            connection.commit()
                            return _row_to_forgetting(existing_row)

                        mapping_row = connection.execute(
                            "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = ?",
                            (memory_id,),
                        ).fetchone()
                        deleted_at = _optional_datetime(memory_row["deleted_at"])
                        effective_requested_at = (
                            requested_time if deleted_at is None else deleted_at
                        )
                        if deleted_at is None:
                            cursor = connection.execute(
                                """
                                UPDATE memories SET deleted_at = ?
                                WHERE user_id = ? AND memory_id = ? AND deleted_at IS NULL
                                """,
                                (_utc_text(effective_requested_at), user_id, memory_id),
                            )
                            if cursor.rowcount != 1:
                                raise StorageError("SQLite forgetting initiation rejected")

                        vector_id = (
                            None if mapping_row is None else int(mapping_row["vector_id"])
                        )
                        connection.execute(
                            """
                            INSERT INTO memory_forgetting(
                                memory_id, user_id, vector_id, cleanup_state,
                                requested_at, completed_at
                            ) VALUES (?, ?, ?, 'cleanup_pending', ?, NULL)
                            """,
                            (
                                memory_id,
                                user_id,
                                vector_id,
                                _utc_text(effective_requested_at),
                            ),
                        )
                        connection.commit()
                    except StorageError:
                        connection.rollback()
                        raise
                    except (sqlite3.Error, TypeError, ValueError) as error:
                        connection.rollback()
                        raise StorageError("SQLite forgetting initiation failed") from error
            except StorageError:
                raise
            except sqlite3.Error as error:
                raise StorageError("SQLite forgetting initiation failed") from error
        return ForgettingRecord(
            memory_id=memory_id,
            user_id=user_id,
            vector_id=vector_id,
            cleanup_state=ForgettingCleanupState.CLEANUP_PENDING,
            requested_at=effective_requested_at,
            completed_at=None,
        )

    def acknowledge_forgetting_complete(
        self,
        *,
        user_id: str,
        memory_id: str,
        vector_id: int,
        completed_at: datetime,
    ) -> ForgettingRecord:
        """Atomically complete known-ID cleanup and remove its live mapping."""
        completed_time = validate_trusted_utc(completed_at)
        if isinstance(vector_id, bool) or not 0 < vector_id <= 2**63 - 1:
            raise StorageError("SQLite forgetting completion rejected")
        with _WRITE_LOCK:
            try:
                with self._connection() as connection:
                    try:
                        connection.execute("BEGIN IMMEDIATE")
                        row = connection.execute(
                            "SELECT * FROM memory_forgetting WHERE user_id = ? AND memory_id = ?",
                            (user_id, memory_id),
                        ).fetchone()
                        memory_row = connection.execute(
                            "SELECT deleted_at FROM memories WHERE user_id = ? AND memory_id = ?",
                            (user_id, memory_id),
                        ).fetchone()
                        if row is None or memory_row is None or memory_row["deleted_at"] is None:
                            raise StorageError("SQLite forgetting completion rejected")
                        stored = _row_to_forgetting(row)
                        if stored.cleanup_state is ForgettingCleanupState.COMPLETE:
                            if stored.vector_id != vector_id:
                                raise StorageError("SQLite forgetting completion rejected")
                            connection.commit()
                            return stored
                        if stored.vector_id != vector_id or stored.completed_at is not None:
                            raise StorageError("SQLite forgetting completion rejected")

                        mapping_row = connection.execute(
                            "SELECT vector_id FROM memory_vector_mappings WHERE memory_id = ?",
                            (memory_id,),
                        ).fetchone()
                        if mapping_row is not None and int(mapping_row["vector_id"]) != vector_id:
                            raise StorageError("SQLite forgetting completion rejected")
                        cursor = connection.execute(
                            """
                            UPDATE memory_forgetting
                            SET cleanup_state = 'complete', completed_at = ?
                            WHERE user_id = ? AND memory_id = ?
                              AND vector_id = ? AND cleanup_state = 'cleanup_pending'
                              AND completed_at IS NULL
                            """,
                            (_utc_text(completed_time), user_id, memory_id, vector_id),
                        )
                        if cursor.rowcount != 1:
                            raise StorageError("SQLite forgetting completion rejected")
                        connection.execute(
                            "DELETE FROM memory_vector_mappings WHERE memory_id = ? AND vector_id = ?",
                            (memory_id, vector_id),
                        )
                        connection.commit()
                    except StorageError:
                        connection.rollback()
                        raise
                    except (sqlite3.Error, TypeError, ValueError) as error:
                        connection.rollback()
                        raise StorageError("SQLite forgetting completion failed") from error
            except StorageError:
                raise
            except sqlite3.Error as error:
                raise StorageError("SQLite forgetting completion failed") from error
        return ForgettingRecord(
            memory_id=memory_id,
            user_id=user_id,
            vector_id=vector_id,
            cleanup_state=ForgettingCleanupState.COMPLETE,
            requested_at=stored.requested_at,
            completed_at=completed_time,
        )

    def find_supersession_target(
        self,
        *,
        user_id: str,
        memory_id: str,
    ) -> MemoryRecord | None:
        """Resolve an explicit target only within its trusted owner scope."""
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM memories WHERE user_id = ? AND memory_id = ?",
                    (user_id, memory_id),
                ).fetchone()
            return None if row is None else _row_to_memory(row)
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise StorageError("SQLite supersession target lookup failed") from error

    def acknowledge_supersession(
        self,
        *,
        user_id: str,
        replacement_memory_id: str,
        target_memory_id: str,
    ) -> None:
        """Atomically activate a replacement and persist both relationship directions."""
        with _WRITE_LOCK:
            try:
                with self._connection() as connection:
                    try:
                        connection.execute("BEGIN IMMEDIATE")
                        replacement_row = connection.execute(
                            "SELECT * FROM memories WHERE user_id = ? AND memory_id = ?",
                            (user_id, replacement_memory_id),
                        ).fetchone()
                        target_row = connection.execute(
                            "SELECT * FROM memories WHERE user_id = ? AND memory_id = ?",
                            (user_id, target_memory_id),
                        ).fetchone()
                        if replacement_row is None or target_row is None:
                            raise StorageError("SQLite supersession transition rejected")

                        replacement = _row_to_memory(replacement_row)
                        target = _row_to_memory(target_row)
                        if (
                            replacement.memory_id == target.memory_id
                            or replacement.lifecycle_status is not LifecycleStatus.ACTIVE
                            or replacement.indexing_state is not IndexingState.PENDING
                            or replacement.supersedes
                            or replacement.superseded_by is not None
                        ):
                            raise StorageError("SQLite supersession transition rejected")
                        try:
                            validate_supersession_target(
                                target=target,
                                user_id=user_id,
                                replacement_memory_id=replacement.memory_id,
                                replacement_authority=replacement.provenance.authority,
                                replacement_subject=replacement.subject,
                                replacement_memory_type=replacement.memory_type,
                            )
                        except ValueError as error:
                            raise StorageError(
                                "SQLite supersession transition rejected"
                            ) from error

                        replacement_cursor = connection.execute(
                            """
                            UPDATE memories
                            SET indexing_state = 'indexed', indexing_error = NULL,
                                supersedes_json = ?
                            WHERE user_id = ? AND memory_id = ?
                              AND lifecycle_status = 'active'
                              AND indexing_state = 'pending'
                              AND superseded_by IS NULL
                              AND supersedes_json = '[]'
                            """,
                            (_json_text((target_memory_id,)), user_id, replacement_memory_id),
                        )
                        target_cursor = connection.execute(
                            """
                            UPDATE memories
                            SET lifecycle_status = 'superseded', superseded_by = ?
                            WHERE user_id = ? AND memory_id = ?
                              AND lifecycle_status = 'active'
                              AND indexing_state = 'indexed'
                              AND superseded_by IS NULL
                            """,
                            (replacement_memory_id, user_id, target_memory_id),
                        )
                        if replacement_cursor.rowcount != 1 or target_cursor.rowcount != 1:
                            raise StorageError("SQLite supersession transition rejected")
                        connection.commit()
                    except StorageError:
                        connection.rollback()
                        raise
                    except (sqlite3.Error, TypeError, ValueError) as error:
                        connection.rollback()
                        raise StorageError("SQLite supersession transaction failed") from error
            except StorageError:
                raise
            except sqlite3.Error as error:
                raise StorageError("SQLite supersession transaction failed") from error

    def mark_indexed(self, *, user_id: str, memory_id: str) -> None:
        self._transition(
            user_id=user_id,
            memory_id=memory_id,
            expected=IndexingState.PENDING,
            target=IndexingState.INDEXED,
            reason=None,
        )

    def mark_pending(self, *, user_id: str, memory_id: str) -> None:
        self._transition(
            user_id=user_id,
            memory_id=memory_id,
            expected=IndexingState.FAILED,
            target=IndexingState.PENDING,
            reason=None,
        )

    def mark_failed(self, *, user_id: str, memory_id: str, reason: str) -> None:
        self._transition(
            user_id=user_id,
            memory_id=memory_id,
            expected=IndexingState.PENDING,
            target=IndexingState.FAILED,
            reason=reason,
        )

    def eligible_vector_ids(self, *, user_id: str) -> tuple[int, ...]:
        """Return only vector IDs that SQLite authorizes for M1 retrieval."""
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT v.vector_id
                    FROM memory_vector_mappings AS v
                    JOIN memories AS m ON m.memory_id = v.memory_id
                    WHERE m.user_id = ? AND m.indexing_state = 'indexed'
                    ORDER BY v.vector_id
                    """,
                    (user_id,),
                ).fetchall()
            return tuple(int(row["vector_id"]) for row in rows)
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise StorageError("SQLite eligible-vector lookup failed") from error

    def current_state_vector_ids(
        self,
        *,
        user_id: str,
        now: datetime,
    ) -> tuple[int, ...]:
        """Return vector IDs satisfying every authoritative M2 read rule."""
        current_time = _utc_text(now)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT v.vector_id
                    FROM memory_vector_mappings AS v
                    JOIN memories AS m ON m.memory_id = v.memory_id
                    WHERE m.user_id = ?
                      AND m.indexing_state = 'indexed'
                      AND m.deleted_at IS NULL
                      AND m.lifecycle_status = 'active'
                      AND m.superseded_by IS NULL
                      AND (m.valid_from IS NULL OR m.valid_from <= ?)
                      AND (m.valid_until IS NULL OR ? < m.valid_until)
                    ORDER BY v.vector_id
                    """,
                    (user_id, current_time, current_time),
                ).fetchall()
            return tuple(int(row["vector_id"]) for row in rows)
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise StorageError("SQLite current-state vector lookup failed") from error

    def historical_vector_ids(self, *, user_id: str) -> tuple[int, ...]:
        """Return vector IDs satisfying every authoritative M5 history rule."""
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT v.vector_id
                    FROM memory_vector_mappings AS v
                    JOIN memories AS m ON m.memory_id = v.memory_id
                    WHERE m.user_id = ?
                      AND m.indexing_state = 'indexed'
                      AND m.deleted_at IS NULL
                      AND (
                        (m.lifecycle_status = 'active' AND m.superseded_by IS NULL)
                        OR (
                          m.lifecycle_status = 'superseded'
                          AND m.superseded_by IS NOT NULL
                          AND TRIM(m.superseded_by) <> ''
                        )
                        OR (
                          m.lifecycle_status = 'expired'
                          AND m.superseded_by IS NULL
                        )
                      )
                    ORDER BY v.vector_id
                    """,
                    (user_id,),
                ).fetchall()
            return tuple(int(row["vector_id"]) for row in rows)
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise StorageError("SQLite historical vector lookup failed") from error

    def expire_current_memories(self, *, user_id: str, now: datetime) -> int:
        """Atomically expire eligible current memories within one owner scope."""
        current_time = _utc_text(validate_trusted_utc(now))
        with _WRITE_LOCK:
            try:
                with self._connection() as connection:
                    try:
                        connection.execute("BEGIN IMMEDIATE")
                        cursor = connection.execute(
                            """
                            UPDATE memories
                            SET lifecycle_status = 'expired'
                            WHERE user_id = ?
                              AND lifecycle_status = 'active'
                              AND indexing_state = 'indexed'
                              AND deleted_at IS NULL
                              AND superseded_by IS NULL
                              AND valid_until IS NOT NULL
                              AND valid_until <= ?
                            """,
                            (user_id, current_time),
                        )
                        transitioned = cursor.rowcount
                        connection.commit()
                    except sqlite3.Error as error:
                        connection.rollback()
                        raise StorageError("SQLite expiration transition failed") from error
            except StorageError:
                raise
            except sqlite3.Error as error:
                raise StorageError("SQLite expiration transition failed") from error
        return transitioned

    def hydrate_indexed(
        self,
        *,
        user_id: str,
        vector_ids: tuple[int, ...],
    ) -> tuple[HydratedMemory, ...]:
        """Hydrate requested mappings only when they remain owner-scoped and indexed."""
        if not vector_ids:
            return ()
        placeholders = ",".join("?" for _ in vector_ids)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT m.*, v.vector_id
                    FROM memory_vector_mappings AS v
                    JOIN memories AS m ON m.memory_id = v.memory_id
                    WHERE m.user_id = ?
                      AND m.indexing_state = 'indexed'
                      AND v.vector_id IN ({placeholders})
                    """,
                    (user_id, *vector_ids),
                ).fetchall()
            return tuple(
                HydratedMemory(vector_id=int(row["vector_id"]), memory=_row_to_memory(row))
                for row in rows
            )
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise StorageError("SQLite indexed-memory hydration failed") from error

    def hydrate_current_state(
        self,
        *,
        user_id: str,
        vector_ids: tuple[int, ...],
        now: datetime,
    ) -> tuple[HydratedMemory, ...]:
        """Hydrate mappings only while every M2 read rule still holds."""
        hydrated = self.hydrate_indexed(user_id=user_id, vector_ids=vector_ids)
        return tuple(
            item
            for item in hydrated
            if is_current_state_eligible(item.memory, user_id=user_id, now=now)
        )

    def hydrate_historical(
        self,
        *,
        user_id: str,
        vector_ids: tuple[int, ...],
    ) -> tuple[HydratedMemory, ...]:
        """Hydrate mappings only while every M5 historical rule still holds."""
        hydrated = self.hydrate_indexed(user_id=user_id, vector_ids=vector_ids)
        return tuple(
            item
            for item in hydrated
            if is_historical_eligible(item.memory, user_id=user_id)
        )

    def _transition(
        self,
        *,
        user_id: str,
        memory_id: str,
        expected: IndexingState,
        target: IndexingState,
        reason: str | None,
    ) -> None:
        with _WRITE_LOCK:
            try:
                with self._connection() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    cursor = connection.execute(
                        """
                        UPDATE memories
                        SET indexing_state = ?, indexing_error = ?
                        WHERE user_id = ? AND memory_id = ? AND indexing_state = ?
                        """,
                        (target.value, reason, user_id, memory_id, expected.value),
                    )
                    if cursor.rowcount != 1:
                        raise StorageError("SQLite indexing-state transition rejected")
                    connection.commit()
            except sqlite3.Error as error:
                raise StorageError("SQLite indexing-state transition failed") from error

    @staticmethod
    def _insert_memory(connection: sqlite3.Connection, memory: MemoryRecord) -> None:
        connection.execute(
            """
            INSERT INTO memories(
                memory_id, user_id, content, memory_type, provenance_authority,
                source_type, conversation_id, turn_id, source_event_at, created_at,
                lifecycle_status, indexing_state, indexing_error, subject, value_json,
                valid_from, valid_until, supersedes_json, superseded_by, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.memory_id,
                memory.user_id,
                memory.content,
                memory.memory_type.value,
                memory.provenance.authority.value,
                memory.provenance.source_type,
                memory.provenance.conversation_id,
                memory.provenance.turn_id,
                _optional_utc_text(memory.provenance.source_event_at),
                _utc_text(memory.created_at),
                memory.lifecycle_status.value,
                memory.indexing_state.value,
                None,
                memory.subject,
                _json_text(memory.value),
                _optional_utc_text(memory.valid_from),
                _optional_utc_text(memory.valid_until),
                _json_text(memory.supersedes),
                memory.superseded_by,
                _optional_utc_text(memory.deleted_at),
            ),
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()


def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        memory_id=str(row["memory_id"]),
        user_id=str(row["user_id"]),
        content=str(row["content"]),
        memory_type=MemoryType(str(row["memory_type"])),
        provenance=Provenance(
            authority=EvidenceAuthority(str(row["provenance_authority"])),
            source_type=str(row["source_type"]),
            conversation_id=str(row["conversation_id"]),
            turn_id=str(row["turn_id"]),
            source_event_at=_optional_datetime(row["source_event_at"]),
        ),
        created_at=_datetime(row["created_at"]),
        lifecycle_status=LifecycleStatus(str(row["lifecycle_status"])),
        indexing_state=IndexingState(str(row["indexing_state"])),
        subject=_optional_text(row["subject"]),
        value=json.loads(str(row["value_json"])),
        valid_from=_optional_datetime(row["valid_from"]),
        valid_until=_optional_datetime(row["valid_until"]),
        supersedes=tuple(json.loads(str(row["supersedes_json"]))),
        superseded_by=_optional_text(row["superseded_by"]),
        deleted_at=_optional_datetime(row["deleted_at"]),
    )


def _row_to_forgetting(row: sqlite3.Row) -> ForgettingRecord:
    return ForgettingRecord(
        memory_id=str(row["memory_id"]),
        user_id=str(row["user_id"]),
        vector_id=None if row["vector_id"] is None else int(row["vector_id"]),
        cleanup_state=ForgettingCleanupState(str(row["cleanup_state"])),
        requested_at=_datetime(row["requested_at"]),
        completed_at=_optional_datetime(row["completed_at"]),
    )


def _row_to_embedding(row: sqlite3.Row) -> Embedding:
    dimension = int(row["embedding_dimension"])
    blob = bytes(row["embedding_blob"])
    if len(blob) != dimension * 4:
        raise StorageError("SQLite embedding BLOB length does not match its dimension")
    return Embedding(
        values=tuple(struct.unpack(f"<{dimension}f", blob)),
        model_id=str(row["embedding_model"]),
        dimension=dimension,
    )


def _result_for(memory: MemoryRecord, indexing_error: str | None) -> AdmissionResult:
    reasons = {
        IndexingState.PENDING: "stored_pending",
        IndexingState.INDEXED: "accepted_and_indexed",
        IndexingState.FAILED: "indexing_failed",
    }
    return AdmissionResult(
        decision=AdmissionDecision.ACCEPTED,
        reason=reasons[memory.indexing_state],
        memory_id=memory.memory_id,
        indexing_state=memory.indexing_state,
        retrievable=memory.indexing_state is IndexingState.INDEXED,
        retryable_error=indexing_error,
        supersedes_memory_ids=memory.supersedes,
        superseded_by_memory_id=memory.superseded_by,
    )


def _embedding_blob(embedding: Embedding) -> bytes:
    return struct.pack(f"<{embedding.dimension}f", *embedding.values)


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _optional_utc_text(value: datetime | None) -> str | None:
    return None if value is None else _utc_text(value)


def _datetime(value: Any) -> datetime:
    return datetime.fromisoformat(str(value))


def _optional_datetime(value: Any) -> datetime | None:
    return None if value is None else _datetime(value)


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _unavailable_inventory(reason: str) -> RecoveryInventory:
    return RecoveryInventory(
        readiness=RecoveryReadiness.UNAVAILABLE,
        reason=reason,
        rebuild_items=(),
        pending_count=0,
        failed_count=0,
        cleanup_pending_count=0,
        cleanup_items=(),
    )


def _classify_recovery_rows(
    rows: list[sqlite3.Row],
    *,
    embedding_model: str,
    vector_dimension: int,
) -> RecoveryInventory:
    seen_memories: set[str] = set()
    live_vector_owners: dict[int, str] = {}
    forgetting_vector_owners: dict[int, str] = {}
    rebuild_items: list[RecoveryVector] = []
    pending_count = 0
    failed_count = 0
    cleanup_pending_count = 0
    cleanup_items: list[RecoveryCleanup] = []
    degraded = False
    memories: dict[str, MemoryRecord] = {}

    for row in rows:
        memory_id = str(row["memory_id"])
        if memory_id in seen_memories:
            return _unavailable_inventory("unavailable_authoritative_identity")
        seen_memories.add(memory_id)
        try:
            memory = _row_to_memory(row)
            state = memory.indexing_state
        except (TypeError, ValueError, json.JSONDecodeError):
            return _unavailable_inventory("unavailable_authoritative_identity")
        memories[memory_id] = memory
        deleted = row["deleted_at"] is not None
        cleanup_state = _optional_text(row["cleanup_state"])
        try:
            mapping_id = (
                None if row["vector_id"] is None else int(row["vector_id"])
            )
            forgetting_id = (
                None
                if row["forgetting_vector_id"] is None
                else int(row["forgetting_vector_id"])
            )
        except (TypeError, ValueError):
            return _unavailable_inventory("unavailable_authoritative_identity")
        if mapping_id is not None:
            if mapping_id <= 0:
                return _unavailable_inventory("unavailable_authoritative_identity")
            if live_vector_owners.get(mapping_id, memory_id) != memory_id:
                return _unavailable_inventory("unavailable_authoritative_identity")
            if forgetting_vector_owners.get(mapping_id, memory_id) != memory_id:
                return _unavailable_inventory("unavailable_authoritative_identity")
            live_vector_owners[mapping_id] = memory_id
        if forgetting_id is not None:
            if forgetting_id <= 0:
                return _unavailable_inventory("unavailable_authoritative_identity")
            if forgetting_vector_owners.get(forgetting_id, memory_id) != memory_id:
                return _unavailable_inventory("unavailable_authoritative_identity")
            if live_vector_owners.get(forgetting_id, memory_id) != memory_id:
                return _unavailable_inventory("unavailable_authoritative_identity")
            forgetting_vector_owners[forgetting_id] = memory_id
        if deleted != (cleanup_state is not None):
            return _unavailable_inventory("unavailable_authoritative_identity")
        if cleanup_state is not None:
            try:
                forgetting = ForgettingRecord(
                    memory_id=memory_id,
                    user_id=str(row["user_id"]),
                    vector_id=forgetting_id,
                    cleanup_state=ForgettingCleanupState(cleanup_state),
                    requested_at=_datetime(row["forgetting_requested_at"]),
                    completed_at=_optional_datetime(row["completed_at"]),
                )
                if memory.deleted_at is None:
                    raise ValueError("forgetting requires a deletion timestamp")
                validate_trusted_utc(memory.deleted_at)
                if forgetting.requested_at != memory.deleted_at:
                    raise ValueError("forgetting timestamps do not match")
            except (TypeError, ValueError):
                return _unavailable_inventory("unavailable_authoritative_identity")
        if cleanup_state == "complete":
            if (
                mapping_id is not None
                or forgetting_id is None
                or row["completed_at"] is None
            ):
                return _unavailable_inventory("unavailable_authoritative_identity")
            continue
        if cleanup_state == "cleanup_pending":
            cleanup_pending_count += 1
            degraded = True
            if forgetting_id is not None and mapping_id != forgetting_id:
                return _unavailable_inventory("unavailable_authoritative_identity")
            cleanup_items.append(
                RecoveryCleanup(
                    memory_id=memory_id,
                    user_id=str(row["user_id"]),
                    stored_vector_id=forgetting_id,
                    live_vector_id=mapping_id,
                )
            )
            continue
        if cleanup_state is not None:
            return _unavailable_inventory("unavailable_authoritative_identity")

        pending_count += int(state is IndexingState.PENDING)
        failed_count += int(state is IndexingState.FAILED)
        degraded |= state is not IndexingState.INDEXED

        if mapping_id is None or row["embedding_blob"] is None:
            if state is IndexingState.INDEXED:
                return _unavailable_inventory("unavailable_authoritative_identity")
            degraded = True
            continue
        try:
            dimension = int(row["embedding_dimension"])
            blob = bytes(row["embedding_blob"])
            values = struct.unpack(f"<{dimension}f", blob)
            valid_values = len(blob) == dimension * 4 and all(
                math.isfinite(value) for value in values
            )
        except (TypeError, ValueError, struct.error):
            valid_values = False
            values = ()
            dimension = 0
        configured = (
            str(row["embedding_model"]) == embedding_model
            and dimension == vector_dimension
            and valid_values
        )
        if not configured:
            if state is IndexingState.INDEXED:
                return _unavailable_inventory("unavailable_embedding_configuration")
            degraded = True
            continue
        rebuild_items.append(
            RecoveryVector(
                memory_id=memory_id,
                user_id=str(row["user_id"]),
                vector_id=mapping_id,
                embedding=Embedding(
                    values=tuple(values),
                    model_id=embedding_model,
                    dimension=dimension,
                ),
            )
        )

    if not _valid_recovery_relationships(memories):
        return _unavailable_inventory("unavailable_authoritative_identity")
    return RecoveryInventory(
        readiness=RecoveryReadiness.DEGRADED if degraded else RecoveryReadiness.READY,
        reason=(
            "degraded_excluded_work_pending"
            if degraded
            else "ready_existing_generation"
        ),
        rebuild_items=tuple(rebuild_items),
        pending_count=pending_count,
        failed_count=failed_count,
        cleanup_pending_count=cleanup_pending_count,
        cleanup_items=tuple(cleanup_items),
    )


def _valid_recovery_relationships(memories: dict[str, MemoryRecord]) -> bool:
    for memory in memories.values():
        if memory.lifecycle_status is LifecycleStatus.SUPERSEDED:
            if memory.superseded_by is None:
                return False
        elif memory.superseded_by is not None:
            return False
        if len(memory.supersedes) > 1:
            return False
        for target_id in memory.supersedes:
            target = memories.get(target_id)
            replacement_subject = (
                None if memory.subject is None else normalize_text(memory.subject)
            )
            target_subject = (
                None if target is None or target.subject is None
                else normalize_text(target.subject)
            )
            if (
                target_id == memory.memory_id
                or memory.indexing_state is not IndexingState.INDEXED
                or target is None
                or target.user_id != memory.user_id
                or target.indexing_state is not IndexingState.INDEXED
                or target.lifecycle_status is not LifecycleStatus.SUPERSEDED
                or target.superseded_by != memory.memory_id
                or memory.provenance.authority
                is not EvidenceAuthority.EXPLICIT_USER
                or not replacement_subject
                or not target_subject
                or replacement_subject != target_subject
                or memory.memory_type is not target.memory_type
            ):
                return False
        if memory.superseded_by is not None:
            replacement = memories.get(memory.superseded_by)
            if (
                replacement is None
                or replacement.user_id != memory.user_id
                or memory.memory_id not in replacement.supersedes
            ):
                return False
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(memory_id: str) -> bool:
        if memory_id in visiting:
            return False
        if memory_id in visited:
            return True
        visiting.add(memory_id)
        for target_id in memories[memory_id].supersedes:
            if target_id in memories and not visit(target_id):
                return False
        visiting.remove(memory_id)
        visited.add(memory_id)
        return True

    return all(visit(memory_id) for memory_id in memories)
