"""SQLite implementation of owner-scoped idempotency and memory persistence ports."""

from __future__ import annotations

import json
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
    PersistedPendingMemory,
)
from conversational_memory.application.errors import StorageError
from conversational_memory.domain.models import (
    AdmissionDecision,
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)

from .migrations import initialize_schema

_WRITE_LOCK = RLock()


class SQLiteMemoryRepository:
    """Authoritative SQLite adapter implementing both admission persistence ports."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        initialize_schema(self._database_path)

    def find(self, *, user_id: str, idempotency_key: str) -> ExistingAdmission | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT m.*, i.request_fingerprint
                    FROM admission_idempotency AS i
                    JOIN memories AS m ON m.memory_id = i.memory_id AND m.user_id = i.user_id
                    WHERE i.user_id = ? AND i.idempotency_key = ?
                    """,
                    (user_id, idempotency_key),
                ).fetchone()
        except sqlite3.Error as error:
            raise StorageError("SQLite idempotency lookup failed") from error

        if row is None:
            return None
        memory = _row_to_memory(row)
        return ExistingAdmission(
            request_fingerprint=str(row["request_fingerprint"]),
            result=_result_for(memory, _optional_text(row["indexing_error"])),
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

    def mark_indexed(self, *, user_id: str, memory_id: str) -> None:
        self._transition(
            user_id=user_id,
            memory_id=memory_id,
            target=IndexingState.INDEXED,
            reason=None,
        )

    def mark_failed(self, *, user_id: str, memory_id: str, reason: str) -> None:
        self._transition(
            user_id=user_id,
            memory_id=memory_id,
            target=IndexingState.FAILED,
            reason=reason,
        )

    def _transition(
        self,
        *,
        user_id: str,
        memory_id: str,
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
                        WHERE user_id = ? AND memory_id = ? AND indexing_state = 'pending'
                        """,
                        (target.value, reason, user_id, memory_id),
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
                valid_from, valid_until, supersedes_json, superseded_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
