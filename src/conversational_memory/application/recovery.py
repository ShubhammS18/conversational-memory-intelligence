"""Immutable contracts for authoritative recovery inventory and readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from conversational_memory.domain.expiration import validate_trusted_utc

from .contracts import Embedding
from .errors import ConfigurationError, IndexingError, StorageError
from .locking import PROCESS_WRITE_LOCK


class RecoveryReadiness(StrEnum):
    """Safety classification produced by recovery audits."""

    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class RecoveryIndexError(IndexingError):
    """Classified failure from derived-index recovery."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class RecoveryVector:
    """One authoritative stable vector selected for a future rebuild."""

    memory_id: str
    user_id: str
    vector_id: int
    embedding: Embedding

    def __post_init__(self) -> None:
        if not self.memory_id.strip() or not self.user_id.strip():
            raise ValueError("recovery vector identity must not be empty")
        if isinstance(self.vector_id, bool) or not 0 < self.vector_id <= 2**63 - 1:
            raise ValueError("vector_id must be a positive signed-int64 integer")


@dataclass(frozen=True, slots=True)
class RecoveryCleanup:
    """One audited M7 cleanup-pending identity handed to recovery."""

    memory_id: str
    user_id: str
    stored_vector_id: int | None
    live_vector_id: int | None


@dataclass(frozen=True, slots=True)
class RecoveryInventory:
    """Read-only SQLite audit result used by later recovery orchestration."""

    readiness: RecoveryReadiness
    reason: str
    rebuild_items: tuple[RecoveryVector, ...]
    pending_count: int
    failed_count: int
    cleanup_pending_count: int
    cleanup_items: tuple[RecoveryCleanup, ...] = ()

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("recovery reason must not be empty")
        if min(self.pending_count, self.failed_count, self.cleanup_pending_count) < 0:
            raise ValueError("recovery counts must not be negative")


@dataclass(frozen=True, slots=True)
class RecoveryPublication:
    """Verified outcome of reconciling one durable FAISS generation."""

    rebuilt: bool
    vector_count: int
    orphan_vectors_removed: int

    def __post_init__(self) -> None:
        if min(self.vector_count, self.orphan_vectors_removed) < 0:
            raise ValueError("recovery publication counts must not be negative")


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """Complete application-level recovery and readiness result."""

    readiness: RecoveryReadiness
    reason: str
    rebuilt: bool
    vector_count: int
    orphan_vectors_removed: int
    pending_count: int
    failed_count: int
    cleanup_pending_count: int

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("recovery reason must not be empty")
        if min(
            self.vector_count,
            self.orphan_vectors_removed,
            self.pending_count,
            self.failed_count,
            self.cleanup_pending_count,
        ) < 0:
            raise ValueError("recovery result counts must not be negative")


class RecoveryInventoryPort(Protocol):
    def recovery_inventory(
        self, *, embedding_model: str, vector_dimension: int
    ) -> RecoveryInventory: ...

    def adopt_forgetting_vector_id(
        self, *, user_id: str, memory_id: str, vector_id: int
    ) -> None: ...

    def acknowledge_forgetting_complete(
        self,
        *,
        user_id: str,
        memory_id: str,
        vector_id: int,
        completed_at: datetime,
    ) -> object: ...


class RecoveryGenerationPort(Protocol):
    def reconcile(self, inventory: RecoveryInventory) -> RecoveryPublication: ...


class RecoveryClockPort(Protocol):
    def now(self) -> datetime: ...


class RecoveryCoordinator:
    """Serialize authoritative inventory and derived-index reconciliation."""

    def __init__(
        self,
        *,
        inventory: RecoveryInventoryPort,
        vector_index: RecoveryGenerationPort,
        embedding_model: str,
        vector_dimension: int,
        clock: RecoveryClockPort,
    ) -> None:
        self._inventory = inventory
        self._vector_index = vector_index
        self._embedding_model = embedding_model
        self._vector_dimension = vector_dimension
        self._clock = clock

    def recover(self) -> RecoveryResult:
        with PROCESS_WRITE_LOCK:
            inventory = self._inventory.recovery_inventory(
                embedding_model=self._embedding_model,
                vector_dimension=self._vector_dimension,
            )
            if inventory.readiness is RecoveryReadiness.UNAVAILABLE:
                return _result_without_publication(inventory)
            try:
                publication = self._vector_index.reconcile(inventory)
            except RecoveryIndexError as error:
                return _failed_publication(inventory, error.reason)
            except IndexingError:
                return _failed_publication(inventory, "unavailable_rebuild")
            cleanup_complete = True
            if inventory.cleanup_items:
                cleanup_complete = self._complete_forgetting(inventory.cleanup_items)
            if publication.rebuilt or inventory.cleanup_items:
                inventory = self._inventory.recovery_inventory(
                    embedding_model=self._embedding_model,
                    vector_dimension=self._vector_dimension,
                )
                if inventory.readiness is RecoveryReadiness.UNAVAILABLE:
                    return _result_without_publication(inventory)
                try:
                    verified = self._vector_index.reconcile(inventory)
                except RecoveryIndexError as error:
                    return _failed_publication(inventory, error.reason)
                except IndexingError:
                    return _failed_publication(inventory, "unavailable_rebuild")
                publication = RecoveryPublication(
                    rebuilt=publication.rebuilt or verified.rebuilt,
                    vector_count=verified.vector_count,
                    orphan_vectors_removed=(
                        publication.orphan_vectors_removed
                        + verified.orphan_vectors_removed
                    ),
                )
            if not cleanup_complete:
                return RecoveryResult(
                    readiness=RecoveryReadiness.DEGRADED,
                    reason="degraded_excluded_work_pending",
                    rebuilt=publication.rebuilt,
                    vector_count=publication.vector_count,
                    orphan_vectors_removed=publication.orphan_vectors_removed,
                    pending_count=inventory.pending_count,
                    failed_count=inventory.failed_count,
                    cleanup_pending_count=inventory.cleanup_pending_count,
                )
            reason = inventory.reason
            if inventory.readiness is RecoveryReadiness.READY:
                reason = (
                    "ready_rebuilt_generation"
                    if publication.rebuilt
                    else "ready_existing_generation"
                )
            return RecoveryResult(
                readiness=inventory.readiness,
                reason=reason,
                rebuilt=publication.rebuilt,
                vector_count=publication.vector_count,
                orphan_vectors_removed=publication.orphan_vectors_removed,
                pending_count=inventory.pending_count,
                failed_count=inventory.failed_count,
                cleanup_pending_count=inventory.cleanup_pending_count,
            )

    def _complete_forgetting(
        self, cleanup_items: tuple[RecoveryCleanup, ...]
    ) -> bool:
        for item in cleanup_items:
            vector_id = item.stored_vector_id or item.live_vector_id
            if vector_id is None:
                continue
            try:
                if item.stored_vector_id is None:
                    self._inventory.adopt_forgetting_vector_id(
                        user_id=item.user_id,
                        memory_id=item.memory_id,
                        vector_id=vector_id,
                    )
                completed_at = validate_trusted_utc(self._clock.now())
                self._inventory.acknowledge_forgetting_complete(
                    user_id=item.user_id,
                    memory_id=item.memory_id,
                    vector_id=vector_id,
                    completed_at=completed_at,
                )
            except (ConfigurationError, StorageError, ValueError):
                return False
        return True


def _result_without_publication(inventory: RecoveryInventory) -> RecoveryResult:
    return RecoveryResult(
        readiness=RecoveryReadiness.UNAVAILABLE,
        reason=inventory.reason,
        rebuilt=False,
        vector_count=0,
        orphan_vectors_removed=0,
        pending_count=inventory.pending_count,
        failed_count=inventory.failed_count,
        cleanup_pending_count=inventory.cleanup_pending_count,
    )


def _failed_publication(inventory: RecoveryInventory, reason: str) -> RecoveryResult:
    return RecoveryResult(
        readiness=RecoveryReadiness.UNAVAILABLE,
        reason=reason,
        rebuilt=False,
        vector_count=0,
        orphan_vectors_removed=0,
        pending_count=inventory.pending_count,
        failed_count=inventory.failed_count,
        cleanup_pending_count=inventory.cleanup_pending_count,
    )
