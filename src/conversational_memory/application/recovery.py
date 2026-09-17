"""Immutable contracts for authoritative recovery inventory and readiness."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Protocol, TypeVar

from conversational_memory.domain.expiration import validate_trusted_utc

from .contracts import Embedding
from .errors import ConfigurationError, ConfigurationMismatchError, IndexingError, StorageError
from .events import (
    EventName,
    EventOutcome,
    EventReasonCode,
    EventStage,
    HmacUserPseudonymizer,
    MemoryEvent,
    ObservabilityReadiness,
    isolated_event,
    privacy_safe_reason_code,
)
from .locking import PROCESS_WRITE_LOCK

if TYPE_CHECKING:
    from .ports import EventSinkPort, TelemetryClockPort

_ResultT = TypeVar("_ResultT")


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
        event_sink: EventSinkPort | None = None,
        telemetry_clock: TelemetryClockPort | None = None,
        user_pseudonymizer: HmacUserPseudonymizer | None = None,
    ) -> None:
        observability_parts = (event_sink, telemetry_clock, user_pseudonymizer)
        if any(part is None for part in observability_parts) and any(
            part is not None for part in observability_parts
        ):
            raise ConfigurationError("invalid_observability_configuration")
        self._inventory = inventory
        self._vector_index = vector_index
        self._embedding_model = embedding_model
        self._vector_dimension = vector_dimension
        self._clock = clock
        self._event_sink = event_sink
        self._telemetry_clock = telemetry_clock
        self._user_pseudonymizer = user_pseudonymizer

    def recover(self) -> RecoveryResult:
        started_at = self._event_start()
        try:
            result = self._recover()
        except Exception as error:
            self._emit_failure(started_at, error)
            raise
        self._emit_terminal(started_at, result)
        return result

    @isolated_event
    def _emit_failure(self, started_at: int | None, error: Exception) -> None:
        reason = (
            "unavailable_embedding_configuration"
            if isinstance(error, (ConfigurationError, ConfigurationMismatchError))
            else "unavailable_rebuild"
        )
        self._emit_terminal(started_at, RecoveryResult(
            RecoveryReadiness.UNAVAILABLE, reason, False, 0, 0, 0, 0, 0,
        ))

    @isolated_event
    def _emit_terminal(self, started_at: int | None, result: RecoveryResult) -> None:
        if result.reason == "unavailable_embedding_configuration":
            self._emit_event(
                started_at=self._event_start(),
                event_name=EventName.CONFIGURATION_FAILED,
                outcome=EventOutcome.FAILED,
                reason_code=EventReasonCode.CONFIGURATION_MISMATCH,
            )
        self._emit_event(
            started_at=started_at,
            event_name=EventName.RECOVERY_COMPLETED,
            outcome=EventOutcome(result.readiness.value),
            reason_code=privacy_safe_reason_code(result.reason, fallback=EventReasonCode.UNAVAILABLE_REBUILD),
            readiness=ObservabilityReadiness(result.readiness.value),
            index_vector_count=result.vector_count,
            embedding_model=self._embedding_model,
            vector_dimension=self._vector_dimension,
            rebuilt=result.rebuilt,
            orphan_vectors_removed=result.orphan_vectors_removed,
            pending_count=result.pending_count,
            failed_count=result.failed_count,
            cleanup_pending_count=result.cleanup_pending_count,
        )

    def _recover(self) -> RecoveryResult:
        with PROCESS_WRITE_LOCK:
            inventory = self._observe_storage(
                stage=EventStage.RECOVERY_INVENTORY,
                operation=lambda: self._inventory.recovery_inventory(
                    embedding_model=self._embedding_model,
                    vector_dimension=self._vector_dimension,
                ),
            )
            if inventory.readiness is RecoveryReadiness.UNAVAILABLE:
                return _result_without_publication(inventory)
            try:
                publication = self._observe_reconciliation(inventory)
            except RecoveryIndexError as error:
                return _failed_publication(inventory, error.reason)
            except IndexingError:
                return _failed_publication(inventory, "unavailable_rebuild")
            cleanup_complete = True
            if inventory.cleanup_items:
                cleanup_complete = self._complete_forgetting(inventory.cleanup_items)
            if publication.rebuilt or inventory.cleanup_items:
                inventory = self._observe_storage(
                    stage=EventStage.RECOVERY_INVENTORY,
                    operation=lambda: self._inventory.recovery_inventory(
                        embedding_model=self._embedding_model,
                        vector_dimension=self._vector_dimension,
                    ),
                )
                if inventory.readiness is RecoveryReadiness.UNAVAILABLE:
                    return _result_without_publication(inventory)
                try:
                    verified = self._observe_reconciliation(inventory)
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
                    self._observe_storage(
                        stage=EventStage.ADOPT_FORGETTING_VECTOR,
                        memory_id=item.memory_id,
                        operation=partial(
                            self._inventory.adopt_forgetting_vector_id,
                            user_id=item.user_id,
                            memory_id=item.memory_id,
                            vector_id=vector_id,
                        ),
                    )
                completed_at = validate_trusted_utc(self._clock.now())
                self._observe_storage(
                    stage=EventStage.ACKNOWLEDGE_FORGETTING,
                    memory_id=item.memory_id,
                    operation=partial(
                        self._inventory.acknowledge_forgetting_complete,
                        user_id=item.user_id,
                        memory_id=item.memory_id,
                        vector_id=vector_id,
                        completed_at=completed_at,
                    ),
                )
            except ConfigurationError:
                self._emit_cleanup_configuration_failure()
                return False
            except (StorageError, ValueError):
                return False
        return True

    @isolated_event
    def _emit_cleanup_configuration_failure(self) -> None:
        self._emit_event(
            started_at=self._event_start(),
            event_name=EventName.CONFIGURATION_FAILED,
            outcome=EventOutcome.FAILED,
            reason_code=EventReasonCode.CONFIGURATION_MISMATCH,
        )

    def _observe_reconciliation(
        self, inventory: RecoveryInventory
    ) -> RecoveryPublication:
        started_at = self._event_start()
        try:
            publication = self._vector_index.reconcile(inventory)
        except Exception:
            self._emit_event(
                started_at=started_at,
                event_name=EventName.INDEXING_FAILED,
                outcome=EventOutcome.FAILED,
                reason_code=EventReasonCode.INDEXING_FAILED,
                stage=EventStage.GENERATION_RECONCILE,
                embedding_model=self._embedding_model,
                vector_dimension=self._vector_dimension,
            )
            raise
        self._emit_event(
            started_at=started_at,
            event_name=EventName.INDEXING_COMPLETED,
            outcome=EventOutcome.SUCCEEDED,
            reason_code=EventReasonCode.OPERATION_COMPLETED,
            stage=EventStage.GENERATION_RECONCILE,
            index_vector_count=publication.vector_count,
            embedding_model=self._embedding_model,
            vector_dimension=self._vector_dimension,
        )
        return publication

    def _observe_storage(
        self,
        *,
        stage: EventStage,
        operation: Callable[[], _ResultT],
        memory_id: str | None = None,
    ) -> _ResultT:
        started_at = self._event_start()
        try:
            result = operation()
        except Exception:
            self._emit_event(
                started_at=started_at,
                event_name=EventName.STORAGE_FAILED,
                outcome=EventOutcome.FAILED,
                reason_code=EventReasonCode.STORAGE_FAILURE,
                stage=stage,
                memory_id=memory_id,
            )
            raise
        self._emit_event(
            started_at=started_at,
            event_name=EventName.STORAGE_COMPLETED,
            outcome=EventOutcome.SUCCEEDED,
            reason_code=EventReasonCode.OPERATION_COMPLETED,
            stage=stage,
            memory_id=memory_id,
        )
        return result

    def _event_start(self) -> int | None:
        if self._telemetry_clock is None:
            return None
        try:
            started_at = self._telemetry_clock.monotonic_ns()
            if (
                isinstance(started_at, bool)
                or not isinstance(started_at, int)
                or started_at < 0
            ):
                return None
            return started_at
        except Exception:  # noqa: BLE001 - observability must not affect recovery
            return None

    def _emit_event(
        self,
        *,
        started_at: int | None,
        event_name: EventName,
        outcome: EventOutcome,
        reason_code: EventReasonCode,
        stage: EventStage | None = None,
        memory_id: str | None = None,
        index_vector_count: int | None = None,
        embedding_model: str | None = None,
        vector_dimension: int | None = None,
        readiness: ObservabilityReadiness | None = None,
        rebuilt: bool | None = None,
        orphan_vectors_removed: int | None = None,
        pending_count: int | None = None,
        failed_count: int | None = None,
        cleanup_pending_count: int | None = None,
    ) -> None:
        if (
            started_at is None
            or self._event_sink is None
            or self._telemetry_clock is None
            or self._user_pseudonymizer is None
        ):
            return
        try:
            ended_at = self._telemetry_clock.monotonic_ns()
            if (
                isinstance(ended_at, bool)
                or not isinstance(ended_at, int)
                or ended_at < 0
                or ended_at < started_at
            ):
                return
            event = MemoryEvent(
                schema_version=1,
                event_name=event_name,
                occurred_at=self._telemetry_clock.utc_now(),
                duration_ms=(ended_at - started_at) // 1_000_000,
                outcome=outcome,
                reason_code=reason_code,
                stage=stage,
                memory_id=memory_id,
                index_vector_count=index_vector_count,
                embedding_model=embedding_model,
                vector_dimension=vector_dimension,
                readiness=readiness,
                rebuilt=rebuilt,
                orphan_vectors_removed=orphan_vectors_removed,
                pending_count=pending_count,
                failed_count=failed_count,
                cleanup_pending_count=cleanup_pending_count,
            )
            self._event_sink.emit(event)
        except Exception:  # noqa: BLE001 - bound sink-failure isolation
            return


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
