"""Configuration validation and application composition."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from conversational_memory.application import (
    ClockPort,
    ConfigurationError,
    EmbeddingPort,
    EventName,
    EventOutcome,
    EventReasonCode,
    EventSinkPort,
    HmacUserPseudonymizer,
    MemoryEvent,
    MemoryIdPort,
    MemoryService,
    ObservabilityReadiness,
    RecoveryCoordinator,
    RecoveryReadiness,
    RecoveryResult,
    ServiceUnavailableError,
    StorageError,
    TelemetryClockPort,
    TokenCounterPort,
    ValidationError,
)
from conversational_memory.application.errors import ConfigurationMismatchError
from conversational_memory.application.events import isolated_event
from conversational_memory.infrastructure import (
    ALL_MPNET_BASE_V2_DIMENSION,
    ALL_MPNET_BASE_V2_MODEL_ID,
    FaissRecoveryAdapter,
    FaissVectorIndex,
    SentenceTransformerEmbedder,
    SQLiteMemoryRepository,
    TiktokenTokenCounter,
)

_MISSING_RELEVANCE_THRESHOLD = object()


@dataclass(frozen=True, slots=True)
class LocalMemoryRuntime:
    """A usable local service paired with its verified startup readiness."""

    service: MemoryService
    recovery: RecoveryResult


@dataclass(frozen=True, slots=True)
class _Observability:
    event_sink: EventSinkPort
    telemetry_clock: TelemetryClockPort
    user_pseudonymizer: HmacUserPseudonymizer


@dataclass(slots=True)
class _StartupObservation:
    recovery: RecoveryResult | None = None
    failure_reason: str = "unavailable_rebuild"
    recovery_configuration_handled: bool = False


def compose_memory_service(
    *,
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    embedder: EmbeddingPort,
    token_counter: TokenCounterPort,
    clock: ClockPort,
    memory_ids: MemoryIdPort,
    relevance_threshold: object = _MISSING_RELEVANCE_THRESHOLD,
    event_sink: EventSinkPort | None = None,
    telemetry_clock: TelemetryClockPort | None = None,
    user_hmac_key: bytes | None = None,
) -> MemoryService:
    """Connect concrete local persistence to the application workflow."""
    observability = _resolve_observability(
        event_sink=event_sink,
        telemetry_clock=telemetry_clock,
        user_hmac_key=user_hmac_key,
        startup_metadata=(vector_index._embedding_model, vector_index._vector_dimension),
    )
    started_at = _event_start(observability)
    try:
        return _compose_memory_service(
            repository=repository,
            vector_index=vector_index,
            embedder=embedder,
            token_counter=token_counter,
            clock=clock,
            memory_ids=memory_ids,
            relevance_threshold=relevance_threshold,
            observability=observability,
        )
    except Exception as error:
        _emit_startup_failure(
            observability=observability, started_at=started_at, recovery=None,
            error=error, embedding_model=vector_index._embedding_model,
            vector_dimension=vector_index._vector_dimension,
        )
        raise


def _compose_memory_service(
    *,
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    embedder: EmbeddingPort,
    token_counter: TokenCounterPort,
    clock: ClockPort,
    memory_ids: MemoryIdPort,
    relevance_threshold: object,
    observability: _Observability | None,
) -> MemoryService:
    return MemoryService(
        idempotency=repository,
        embedder=embedder,
        repository=repository,
        vector_index=vector_index,
        token_counter=token_counter,
        clock=clock,
        memory_ids=memory_ids,
        relevance_threshold=relevance_threshold,
        event_sink=None if observability is None else observability.event_sink,
        telemetry_clock=(
            None if observability is None else observability.telemetry_clock
        ),
        user_pseudonymizer=(
            None if observability is None else observability.user_pseudonymizer
        ),
    )


def compose_recovered_memory_service(
    *,
    repository: SQLiteMemoryRepository,
    index_directory: str | Path,
    embedding_model: str,
    vector_dimension: int,
    embedder: EmbeddingPort,
    token_counter: TokenCounterPort,
    clock: ClockPort,
    memory_ids: MemoryIdPort,
    relevance_threshold: object = _MISSING_RELEVANCE_THRESHOLD,
    event_sink: EventSinkPort | None = None,
    telemetry_clock: TelemetryClockPort | None = None,
    user_hmac_key: bytes | None = None,
) -> LocalMemoryRuntime:
    """Recover local durable state before exposing a usable memory service."""
    observability = _resolve_observability(
        event_sink=event_sink,
        telemetry_clock=telemetry_clock,
        user_hmac_key=user_hmac_key,
        startup_metadata=(embedding_model, vector_dimension),
    )
    return _run_startup(
        observability=observability,
        embedding_model=embedding_model,
        vector_dimension=vector_dimension,
        operation=lambda observation: _compose_recovered_memory_service(
            repository=repository, index_directory=index_directory,
            embedding_model=embedding_model, vector_dimension=vector_dimension,
            embedder=embedder, token_counter=token_counter, clock=clock,
            memory_ids=memory_ids, relevance_threshold=relevance_threshold,
            observability=observability,
            observation=observation,
        ),
    )


def _compose_recovered_memory_service(
    *, repository: SQLiteMemoryRepository, index_directory: str | Path,
    embedding_model: str, vector_dimension: int, embedder: EmbeddingPort,
    token_counter: TokenCounterPort, clock: ClockPort, memory_ids: MemoryIdPort,
    relevance_threshold: object, observability: _Observability | None,
    observation: _StartupObservation,
) -> LocalMemoryRuntime:
    coordinator = RecoveryCoordinator(
        inventory=repository,
        vector_index=FaissRecoveryAdapter(
            index_directory,
            embedding_model=embedding_model,
            vector_dimension=vector_dimension,
        ),
        embedding_model=embedding_model,
        vector_dimension=vector_dimension,
        clock=clock,
        event_sink=None if observability is None else observability.event_sink,
        telemetry_clock=(
            None if observability is None else observability.telemetry_clock
        ),
        user_pseudonymizer=(
            None if observability is None else observability.user_pseudonymizer
        ),
    )
    try:
        recovery = coordinator.recover()
    except Exception as error:
        observation.recovery_configuration_handled = isinstance(
            error, (ConfigurationError, ConfigurationMismatchError)
        )
        raise
    observation.recovery_configuration_handled = (
        recovery.reason == "unavailable_embedding_configuration"
    )
    observation.recovery = recovery
    if recovery.readiness is RecoveryReadiness.UNAVAILABLE:
        raise ServiceUnavailableError(recovery.reason)
    observation.failure_reason = "unavailable_post_publication_verification"
    vector_index = FaissVectorIndex(
        index_directory,
        embedding_model=embedding_model,
        vector_dimension=vector_dimension,
    )
    observation.failure_reason = "unavailable_rebuild"
    service = _compose_memory_service(
            repository=repository,
            vector_index=vector_index,
            embedder=embedder,
            token_counter=token_counter,
            clock=clock,
            memory_ids=memory_ids,
            relevance_threshold=relevance_threshold,
            observability=observability,
    )
    runtime = LocalMemoryRuntime(
        service=service,
        recovery=recovery,
    )
    return runtime


def compose_local_memory_service(
    *,
    database_path: str | Path,
    index_directory: str | Path,
    model_cache_directory: str | Path,
    clock: ClockPort,
    memory_ids: MemoryIdPort,
    create_index_if_missing: bool = False,
    relevance_threshold: object = _MISSING_RELEVANCE_THRESHOLD,
    event_sink: EventSinkPort | None = None,
    telemetry_clock: TelemetryClockPort | None = None,
    user_hmac_key: bytes | None = None,
) -> MemoryService:
    """Build the approved real local M1 service at the sole concrete composition point."""
    _ = create_index_if_missing
    observability = _resolve_observability(
        event_sink=event_sink, telemetry_clock=telemetry_clock,
        user_hmac_key=user_hmac_key,
        startup_metadata=(ALL_MPNET_BASE_V2_MODEL_ID, ALL_MPNET_BASE_V2_DIMENSION),
    )
    return _run_startup(
        observability=observability,
        embedding_model=ALL_MPNET_BASE_V2_MODEL_ID,
        vector_dimension=ALL_MPNET_BASE_V2_DIMENSION,
        operation=lambda observation: _compose_local_runtime(
            database_path=database_path, index_directory=index_directory,
            model_cache_directory=model_cache_directory, clock=clock,
            memory_ids=memory_ids, relevance_threshold=relevance_threshold,
            observability=observability,
            observation=observation,
        ),
    ).service


def _compose_local_runtime(
    *, database_path: str | Path, index_directory: str | Path,
    model_cache_directory: str | Path, clock: ClockPort, memory_ids: MemoryIdPort,
    relevance_threshold: object, observability: _Observability | None,
    observation: _StartupObservation,
) -> LocalMemoryRuntime:
    observation.failure_reason = "unavailable_schema_or_migration"
    try:
        repository = SQLiteMemoryRepository(database_path)
    except StorageError as error:
        raise ServiceUnavailableError("unavailable_schema_or_migration") from error
    observation.failure_reason = "unavailable_embedding_configuration"
    embedder = SentenceTransformerEmbedder(cache_directory=model_cache_directory)
    token_counter = TiktokenTokenCounter()
    observation.failure_reason = "unavailable_rebuild"
    return _compose_recovered_memory_service(
        repository=repository,
        index_directory=index_directory,
        embedding_model=ALL_MPNET_BASE_V2_MODEL_ID,
        vector_dimension=ALL_MPNET_BASE_V2_DIMENSION,
        embedder=embedder,
        token_counter=token_counter,
        clock=clock,
        memory_ids=memory_ids,
        relevance_threshold=relevance_threshold,
        observability=observability,
        observation=observation,
    )


def _empty_readiness(reason: str, readiness: RecoveryReadiness) -> RecoveryResult:
    return RecoveryResult(readiness, reason, False, 0, 0, 0, 0, 0)


def _run_startup(
    *, observability: _Observability | None, embedding_model: str,
    vector_dimension: int, operation: Callable[[_StartupObservation], LocalMemoryRuntime],
) -> LocalMemoryRuntime:
    started_at = _event_start(observability)
    observation = _StartupObservation()
    try:
        runtime = operation(observation)
    except Exception as error:
        _emit_startup_failure(
            observability=observability, started_at=started_at,
            recovery=observation.recovery, error=error,
            fallback_reason=observation.failure_reason,
            configuration_handled=observation.recovery_configuration_handled,
            embedding_model=embedding_model, vector_dimension=vector_dimension,
        )
        raise
    _emit_startup_event(
        observability=observability, started_at=started_at, recovery=runtime.recovery,
        embedding_model=embedding_model, vector_dimension=vector_dimension,
    )
    return runtime


@isolated_event
def _emit_startup_failure(
    *, observability: _Observability | None, started_at: int | None,
    recovery: RecoveryResult | None, error: Exception,
    embedding_model: str, vector_dimension: int,
    fallback_reason: str = "unavailable_rebuild",
    configuration_handled: bool = False,
) -> None:
    configuration = isinstance(error, (ConfigurationError, ConfigurationMismatchError)) or (
        isinstance(error, ValidationError) and error.reason == "invalid_tokenizer_configuration"
    )
    if configuration and not configuration_handled:
        _emit_configuration_failure(observability)
    reason = fallback_reason
    if isinstance(error, ServiceUnavailableError) and error.args:
        candidate = error.args[0]
        if isinstance(candidate, str) and candidate in {
            r.value for r in EventReasonCode if r.value.startswith("unavailable_")
        }:
            reason = candidate
        if reason == "unavailable_embedding_configuration" and not configuration and not configuration_handled:
            _emit_configuration_failure(observability)
    _emit_startup_event(
        observability=observability, started_at=started_at,
        recovery=recovery or _empty_readiness(reason, RecoveryReadiness.UNAVAILABLE),
        embedding_model=embedding_model, vector_dimension=vector_dimension,
        reason_code=EventReasonCode.CONFIGURATION_MISMATCH if configuration else EventReasonCode(reason),
        readiness_override=ObservabilityReadiness.UNAVAILABLE,
    )


def _resolve_observability(
    *,
    event_sink: EventSinkPort | None,
    telemetry_clock: TelemetryClockPort | None,
    user_hmac_key: bytes | None,
    startup_metadata: tuple[str, int] | None,
) -> _Observability | None:
    parts = (event_sink, telemetry_clock, user_hmac_key)
    if all(part is None for part in parts):
        return None
    valid_sink = event_sink is not None and callable(getattr(event_sink, "emit", None))
    valid_clock = (
        telemetry_clock is not None
        and callable(getattr(telemetry_clock, "utc_now", None))
        and callable(getattr(telemetry_clock, "monotonic_ns", None))
    )
    try:
        if not valid_sink or not valid_clock or user_hmac_key is None:
            raise ValueError("incomplete observability configuration")
        pseudonymizer = HmacUserPseudonymizer(user_hmac_key)
    except (TypeError, ValueError) as error:
        _emit_invalid_observability(
            event_sink=event_sink if valid_sink else None,
            telemetry_clock=telemetry_clock if valid_clock else None,
            startup_metadata=startup_metadata,
        )
        raise ConfigurationError("invalid_observability_configuration") from error
    assert event_sink is not None
    assert telemetry_clock is not None
    return _Observability(event_sink, telemetry_clock, pseudonymizer)


@isolated_event
def _emit_invalid_observability(
    *,
    event_sink: EventSinkPort | None,
    telemetry_clock: TelemetryClockPort | None,
    startup_metadata: tuple[str, int] | None,
) -> None:
    if event_sink is None or telemetry_clock is None:
        return
    _emit_delivery_event(
        event_sink=event_sink,
        telemetry_clock=telemetry_clock,
        started_at=_event_start_from_clock(telemetry_clock),
        event_name=EventName.CONFIGURATION_FAILED,
        outcome=EventOutcome.FAILED,
        reason_code=EventReasonCode.CONFIGURATION_MISMATCH,
    )
    if startup_metadata is None:
        return
    embedding_model, vector_dimension = startup_metadata
    _emit_delivery_event(
        event_sink=event_sink,
        telemetry_clock=telemetry_clock,
        started_at=_event_start_from_clock(telemetry_clock),
        event_name=EventName.STARTUP_UNAVAILABLE,
        outcome=EventOutcome.UNAVAILABLE,
        reason_code=EventReasonCode.CONFIGURATION_MISMATCH,
        readiness=ObservabilityReadiness.UNAVAILABLE,
        index_vector_count=0,
        embedding_model=embedding_model,
        vector_dimension=vector_dimension,
        rebuilt=False,
        orphan_vectors_removed=0,
        pending_count=0,
        failed_count=0,
        cleanup_pending_count=0,
    )


def _emit_configuration_failure(observability: _Observability | None) -> None:
    _emit_event(
        observability=observability,
        started_at=_event_start(observability),
        event_name=EventName.CONFIGURATION_FAILED,
        outcome=EventOutcome.FAILED,
        reason_code=EventReasonCode.CONFIGURATION_MISMATCH,
    )


@isolated_event
def _emit_startup_event(
    *,
    observability: _Observability | None,
    started_at: int | None,
    recovery: RecoveryResult,
    embedding_model: str,
    vector_dimension: int,
    reason_code: EventReasonCode | None = None,
    readiness_override: ObservabilityReadiness | None = None,
) -> None:
    readiness = readiness_override or ObservabilityReadiness(recovery.readiness.value)
    event_name = {
        ObservabilityReadiness.READY: EventName.STARTUP_READY,
        ObservabilityReadiness.DEGRADED: EventName.STARTUP_DEGRADED,
        ObservabilityReadiness.UNAVAILABLE: EventName.STARTUP_UNAVAILABLE,
    }[readiness]
    _emit_event(
        observability=observability,
        started_at=started_at,
        event_name=event_name,
        outcome=EventOutcome(readiness.value),
        reason_code=reason_code or EventReasonCode(recovery.reason),
        readiness=readiness,
        index_vector_count=recovery.vector_count,
        embedding_model=embedding_model,
        vector_dimension=vector_dimension,
        rebuilt=recovery.rebuilt,
        orphan_vectors_removed=recovery.orphan_vectors_removed,
        pending_count=recovery.pending_count,
        failed_count=recovery.failed_count,
        cleanup_pending_count=recovery.cleanup_pending_count,
    )


def _event_start(observability: _Observability | None) -> int | None:
    if observability is None:
        return None
    return _event_start_from_clock(observability.telemetry_clock)


def _event_start_from_clock(telemetry_clock: TelemetryClockPort) -> int | None:
    try:
        value = telemetry_clock.monotonic_ns()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value
    except Exception:  # noqa: BLE001 - observability must not affect startup
        return None


def _emit_event(
    *,
    observability: _Observability | None,
    started_at: int | None,
    event_name: EventName,
    outcome: EventOutcome,
    reason_code: EventReasonCode,
    readiness: ObservabilityReadiness | None = None,
    index_vector_count: int | None = None,
    embedding_model: str | None = None,
    vector_dimension: int | None = None,
    rebuilt: bool | None = None,
    orphan_vectors_removed: int | None = None,
    pending_count: int | None = None,
    failed_count: int | None = None,
    cleanup_pending_count: int | None = None,
) -> None:
    if observability is None or started_at is None:
        return
    _emit_delivery_event(
        event_sink=observability.event_sink,
        telemetry_clock=observability.telemetry_clock,
        started_at=started_at,
        event_name=event_name,
        outcome=outcome,
        reason_code=reason_code,
        readiness=readiness,
        index_vector_count=index_vector_count,
        embedding_model=embedding_model,
        vector_dimension=vector_dimension,
        rebuilt=rebuilt,
        orphan_vectors_removed=orphan_vectors_removed,
        pending_count=pending_count,
        failed_count=failed_count,
        cleanup_pending_count=cleanup_pending_count,
    )


def _emit_delivery_event(
    *,
    event_sink: EventSinkPort,
    telemetry_clock: TelemetryClockPort,
    started_at: int | None,
    event_name: EventName,
    outcome: EventOutcome,
    reason_code: EventReasonCode,
    readiness: ObservabilityReadiness | None = None,
    index_vector_count: int | None = None,
    embedding_model: str | None = None,
    vector_dimension: int | None = None,
    rebuilt: bool | None = None,
    orphan_vectors_removed: int | None = None,
    pending_count: int | None = None,
    failed_count: int | None = None,
    cleanup_pending_count: int | None = None,
) -> None:
    if started_at is None:
        return
    try:
        ended_at = telemetry_clock.monotonic_ns()
        if (
            isinstance(ended_at, bool)
            or not isinstance(ended_at, int)
            or ended_at < started_at
        ):
            return
        event = MemoryEvent(
            schema_version=1,
            event_name=event_name,
            occurred_at=telemetry_clock.utc_now(),
            duration_ms=(ended_at - started_at) // 1_000_000,
            outcome=outcome,
            reason_code=reason_code,
            readiness=readiness,
            index_vector_count=index_vector_count,
            embedding_model=embedding_model,
            vector_dimension=vector_dimension,
            rebuilt=rebuilt,
            orphan_vectors_removed=orphan_vectors_removed,
            pending_count=pending_count,
            failed_count=failed_count,
            cleanup_pending_count=cleanup_pending_count,
        )
        event_sink.emit(event)
    except Exception:  # noqa: BLE001 - bound sink-failure isolation
        return


__all__ = [
    "ALL_MPNET_BASE_V2_DIMENSION",
    "ALL_MPNET_BASE_V2_MODEL_ID",
    "FaissVectorIndex",
    "LocalMemoryRuntime",
    "SQLiteMemoryRepository",
    "SentenceTransformerEmbedder",
    "TiktokenTokenCounter",
    "compose_local_memory_service",
    "compose_memory_service",
    "compose_recovered_memory_service",
]
