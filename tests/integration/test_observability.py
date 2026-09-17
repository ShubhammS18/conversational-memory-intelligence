from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from threading import Barrier

import pytest

from conversational_memory import composition
from conversational_memory.application import (
    AdmissionRequest,
    CaptureEventSink,
    ConfigurationError,
    Embedding,
    EventName,
    EventOutcome,
    EventReasonCode,
    EventStage,
    ForgetRequest,
    ForgettingRecord,
    HmacUserPseudonymizer,
    IndexingError,
    MemoryEvent,
    MemoryService,
    ObservabilityReadiness,
    RecoveryCleanup,
    RecoveryCoordinator,
    RecoveryInventory,
    RecoveryPublication,
    RecoveryReadiness,
    RequestContext,
    RetrievalIntent,
    RetrievalOutcome,
    RetrievalRequest,
    ServiceUnavailableError,
    StorageError,
    ValidationError,
    serialize_json_line,
)
from conversational_memory.application.errors import ConfigurationMismatchError
from conversational_memory.application.events import FORBIDDEN_EVENT_FIELDS
from conversational_memory.application.recovery import RecoveryIndexError
from conversational_memory.composition import compose_recovered_memory_service
from conversational_memory.entrypoints.cli import build_parser, main
from conversational_memory.infrastructure import (
    FaissVectorIndex,
    JsonLineEventSink,
    SQLiteMemoryRepository,
    SystemTelemetryClock,
)

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
MODEL = "observability-test-model"
KEY = b"0123456789abcdef0123456789abcdef"
PRIVATE_USER = "PRIVATE-RAW-USER"


@pytest.mark.parametrize("startup", ["local", "recovered", "wired"])
def test_m9_noncallable_sink_rejected_before_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, startup: str,
) -> None:
    class InvalidSink:
        emit = 1

    service, repository, embedder, clock = _service(tmp_path, sink=CaptureEventSink())
    calls: list[str] = []
    def forbidden(*args: object, **kwargs: object) -> object:
        calls.append("startup")
        raise AssertionError("startup must not run")
    monkeypatch.setattr(composition, "_run_startup", forbidden)
    monkeypatch.setattr(composition, "_compose_memory_service", forbidden)
    options = {"event_sink": InvalidSink(), "telemetry_clock": _TelemetryClock(), "user_hmac_key": KEY}
    with pytest.raises(ConfigurationError) as caught:
        if startup == "local":
            composition.compose_local_memory_service(database_path=tmp_path / "untouched.sqlite3", index_directory=tmp_path / "untouched-index", model_cache_directory=tmp_path / "cache", clock=clock, memory_ids=_MemoryIds(), relevance_threshold=0.50, **options)
        elif startup == "recovered":
            _compose_observed_startup(tmp_path, repository=repository, sink=InvalidSink())
        else:
            composition.compose_memory_service(repository=repository, vector_index=service._vector_index, embedder=embedder, token_counter=_TokenCounter(), clock=clock, memory_ids=_MemoryIds(), relevance_threshold=0.50, **options)
    assert type(caught.value) is ConfigurationError
    assert caught.value.args == ("invalid_observability_configuration",)
    assert calls == [] and clock.calls == 0 and not embedder.calls
    assert not (tmp_path / "untouched.sqlite3").exists()
    assert not (tmp_path / "untouched-index").exists()


@pytest.mark.parametrize("composed", [False, True])
@pytest.mark.parametrize("drop_configuration", [False, True])
@pytest.mark.parametrize("failure_stage", ["clock", "acknowledgement"])
def test_m9_cleanup_configuration_degraded_ordering_and_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, composed: bool,
    drop_configuration: bool, failure_stage: str,
) -> None:
    class Sink(CaptureEventSink):
        configuration_attempts = 0
        def emit(self, event: MemoryEvent) -> None:
            if event.event_name is EventName.CONFIGURATION_FAILED:
                self.configuration_attempts += 1
                if drop_configuration:
                    raise RuntimeError("PRIVATE-SINK-CLEANUP")
            super().emit(event)
    service, repository, _, _ = _service(tmp_path, sink=CaptureEventSink())
    context = RequestContext(user_id=PRIVATE_USER, request_id="seed")
    admitted = service.admit(context, _request())
    def fail_removal(**kwargs: object) -> object:
        raise IndexingError("PRIVATE-REMOVAL")
    monkeypatch.setattr(service._vector_index, "remove", fail_removal)
    pending = service.forget(context, ForgetRequest(memory_id=admitted.memory_id))
    assert not pending.cleanup_complete
    before = repository.find_forgetting_target(user_id=PRIVATE_USER, memory_id=admitted.memory_id)
    error = ConfigurationError("PRIVATE-CLEANUP-CONFIGURATION")
    def fail(*args: object, **kwargs: object) -> object:
        raise error
    if failure_stage == "clock":
        monkeypatch.setattr(_LifecycleClock, "now", fail)
    else:
        monkeypatch.setattr(repository, "acknowledge_forgetting_complete", fail)
    sink = Sink()
    if composed:
        runtime = _compose_observed_startup(tmp_path, repository=repository, sink=sink)
        result = runtime.recovery
        assert runtime.service is not None
    else:
        result = RecoveryCoordinator(inventory=repository, vector_index=composition.FaissRecoveryAdapter(tmp_path / "index", embedding_model=MODEL, vector_dimension=2), embedding_model=MODEL, vector_dimension=2, clock=_LifecycleClock(), event_sink=sink, telemetry_clock=_TelemetryClock(), user_pseudonymizer=HmacUserPseudonymizer(KEY)).recover()
    assert result.readiness is RecoveryReadiness.DEGRADED
    assert result.reason == "degraded_excluded_work_pending"
    assert result.cleanup_pending_count == 1 and result.vector_count == 0
    assert repository.find_forgetting_target(user_id=PRIVATE_USER, memory_id=admitted.memory_id) == before
    assert repository.current_state_vector_ids(user_id=PRIVATE_USER, now=NOW) == ()
    assert sink.configuration_attempts == 1
    expected = [EventName.STORAGE_COMPLETED, EventName.INDEXING_COMPLETED]
    if failure_stage == "acknowledgement":
        expected.append(EventName.STORAGE_FAILED)
    if not drop_configuration:
        expected.append(EventName.CONFIGURATION_FAILED)
    expected.extend([EventName.STORAGE_COMPLETED, EventName.INDEXING_COMPLETED, EventName.RECOVERY_COMPLETED])
    if composed:
        expected.append(EventName.STARTUP_DEGRADED)
    assert _names(sink) == expected
    assert sum(e.event_name is EventName.RECOVERY_COMPLETED for e in sink.events) == 1
    _assert_recursive_privacy(sink, (PRIVATE_USER, KEY.decode(), "PRIVATE-CLEANUP-CONFIGURATION", "PRIVATE-SINK-CLEANUP", "PRIVATE-REMOVAL", "PRIVATE-SUBJECT", "PRIVATE-VALUE"))


@pytest.mark.parametrize("operation", ["admit", "retrieve", "forget"])
@pytest.mark.parametrize("request_id", [" request ", "\trequest\n", "\nrequest\u2003"])
def test_m9_trusted_correlation_preserves_surrounding_whitespace(
    tmp_path: Path, operation: str, request_id: str,
) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    admitted = service.admit(RequestContext(user_id=PRIVATE_USER, request_id="setup"), _request())
    offset = len(sink.events)
    context = RequestContext(user_id=PRIVATE_USER, request_id=request_id)
    request = {"admit": _request(idempotency_key="correlation-new"), "retrieve": RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=1000), "forget": ForgetRequest(memory_id=admitted.memory_id)}[operation]
    getattr(service, operation)(context, request)
    events = sink.events[offset:]
    terminal = {"admit": EventName.ADMISSION_COMPLETED, "retrieve": EventName.RETRIEVAL_COMPLETED, "forget": EventName.FORGETTING_COMPLETED}[operation]
    assert len(events) > 1
    assert events[-1].event_name is terminal
    assert sum(e.event_name is terminal for e in events) == 1
    assert all(e.request_id == request_id for e in events)
    assert all(json.loads(line)["request_id"] == request_id for line in sink.lines[offset:])
    expected_stages = {
        "admit": [EventStage.IDEMPOTENCY_LOOKUP, EventStage.EMBEDDING, EventStage.PERSIST_PENDING, EventStage.VECTOR_ADD, EventStage.MARK_INDEXED],
        "retrieve": [EventStage.EXPIRATION_TRANSITION, EventStage.CURRENT_ALLOWLIST, EventStage.EMBEDDING, EventStage.VECTOR_SEARCH, EventStage.CURRENT_HYDRATION],
        "forget": [EventStage.FORGETTING_LOOKUP, EventStage.BEGIN_FORGETTING, EventStage.VECTOR_REMOVE, EventStage.ACKNOWLEDGE_FORGETTING],
    }
    assert [e.stage for e in events[:-1]] == expected_stages[operation]
    _assert_recursive_privacy(sink, (PRIVATE_USER, KEY.decode(), "PRIVATE-QUERY"))


@pytest.mark.parametrize("write_fails", [False, True])
def test_m9_mark_failed_write_is_observed_without_changing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_fails: bool,
) -> None:
    sink = CaptureEventSink()
    service, repository, embedder, _ = _service(tmp_path, sink=sink)
    def fail_publication(**kwargs: object) -> None:
        raise IndexingError("PRIVATE-PUBLICATION-FAILURE")
    monkeypatch.setattr(service._vector_index, "add", fail_publication)
    if write_fails:
        def fail_write(**kwargs: object) -> None:
            raise StorageError("PRIVATE-FAILED-WRITE")
        monkeypatch.setattr(repository, "mark_failed", fail_write)
    context = RequestContext(user_id=PRIVATE_USER, request_id="failure-write")
    result = service.admit(context, _request())
    assert result.reason == "indexing_failed" and not result.retrievable
    assert result.indexing_state.value == ("pending" if write_fails else "failed")
    stored = repository.find(user_id=PRIVATE_USER, idempotency_key="admit-1")
    assert stored is not None and stored.result.indexing_state is result.indexing_state
    assert len(embedder.calls) == 1
    assert repository.current_state_vector_ids(user_id=PRIVATE_USER, now=NOW) == ()
    assert [e.event_name for e in sink.events[-3:]] == [EventName.INDEXING_FAILED, EventName.STORAGE_FAILED if write_fails else EventName.STORAGE_COMPLETED, EventName.ADMISSION_COMPLETED]
    assert sink.events[-2].stage.value == "mark_failed"
    assert sink.events[-2].reason_code is (EventReasonCode.STORAGE_FAILURE if write_fails else EventReasonCode.OPERATION_COMPLETED)
    assert sum(e.event_name is EventName.ADMISSION_COMPLETED for e in sink.events) == 1
    _assert_recursive_privacy(sink, (PRIVATE_USER, KEY.decode(), "PRIVATE-PUBLICATION-FAILURE", "PRIVATE-FAILED-WRITE"))


@pytest.mark.parametrize("composed", [False, True])
def test_m9_recovery_configuration_event_precedes_terminals_once(tmp_path: Path, composed: bool) -> None:
    sink = CaptureEventSink()
    service, repository, _, _ = _service(tmp_path, sink=CaptureEventSink())
    context = RequestContext(user_id=PRIVATE_USER, request_id="seed")
    service.admit(context, _request())
    before = repository.current_state_vector_ids(user_id=PRIVATE_USER, now=NOW)
    if composed:
        with pytest.raises(ServiceUnavailableError, match="unavailable_embedding_configuration"):
            compose_recovered_memory_service(repository=repository, index_directory=tmp_path / "index", embedding_model=MODEL, vector_dimension=3, embedder=_Embedder(), token_counter=_TokenCounter(), clock=_LifecycleClock(), memory_ids=_MemoryIds(), relevance_threshold=0.50, event_sink=sink, telemetry_clock=_TelemetryClock(), user_hmac_key=KEY)
    else:
        result = RecoveryCoordinator(inventory=repository, vector_index=composition.FaissRecoveryAdapter(tmp_path / "index", embedding_model=MODEL, vector_dimension=3), embedding_model=MODEL, vector_dimension=3, clock=_LifecycleClock(), event_sink=sink, telemetry_clock=_TelemetryClock(), user_pseudonymizer=HmacUserPseudonymizer(KEY)).recover()
        assert result.readiness is RecoveryReadiness.UNAVAILABLE
        assert result.reason == "unavailable_embedding_configuration"
    names = _names(sink)
    expected = [EventName.CONFIGURATION_FAILED, EventName.RECOVERY_COMPLETED]
    if composed:
        expected.append(EventName.STARTUP_UNAVAILABLE)
    assert names == [EventName.STORAGE_COMPLETED, *expected]
    assert names.count(EventName.CONFIGURATION_FAILED) == 1
    assert repository.current_state_vector_ids(user_id=PRIVATE_USER, now=NOW) == before
    assert service.retrieve(context, RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=1000)).memories
    _assert_recursive_privacy(sink, (PRIVATE_USER, KEY.decode(), "PRIVATE-QUERY", "PRIVATE-SUBJECT", "PRIVATE-VALUE"))


@pytest.mark.parametrize("composed", [False, True])
@pytest.mark.parametrize("drop_configuration", [False, True])
def test_m9_raised_recovery_configuration_preserves_exception_and_no_duplicate_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, composed: bool, drop_configuration: bool,
) -> None:
    class Sink(CaptureEventSink):
        configuration_attempts = 0
        def emit(self, event: MemoryEvent) -> None:
            if event.event_name is EventName.CONFIGURATION_FAILED:
                self.configuration_attempts += 1
                if drop_configuration:
                    raise RuntimeError("PRIVATE-SINK-CONFIGURATION")
            super().emit(event)
    sink = Sink()
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    error = ConfigurationMismatchError("PRIVATE-RECOVERY-CONFIGURATION")
    def fail(**kwargs: object) -> object:
        raise error
    monkeypatch.setattr(repository, "recovery_inventory", fail)
    with pytest.raises(ConfigurationMismatchError) as caught:
        if composed:
            _compose_observed_startup(tmp_path, repository=repository, sink=sink)
        else:
            RecoveryCoordinator(inventory=repository, vector_index=composition.FaissRecoveryAdapter(tmp_path / "index", embedding_model=MODEL, vector_dimension=2), embedding_model=MODEL, vector_dimension=2, clock=_LifecycleClock(), event_sink=sink, telemetry_clock=_TelemetryClock(), user_pseudonymizer=HmacUserPseudonymizer(KEY)).recover()
    assert caught.value is error
    assert sink.configuration_attempts == 1
    expected = [EventName.STORAGE_FAILED]
    if not drop_configuration:
        expected.append(EventName.CONFIGURATION_FAILED)
    expected.append(EventName.RECOVERY_COMPLETED)
    if composed:
        expected.append(EventName.STARTUP_UNAVAILABLE)
    assert _names(sink) == expected
    _assert_recursive_privacy(sink, ("PRIVATE-RECOVERY-CONFIGURATION", "PRIVATE-SINK-CONFIGURATION", KEY.decode()))


@pytest.mark.parametrize("operation", ["admit", "retrieve", "forget"])
@pytest.mark.parametrize("error", [RuntimeError("PRIVATE-EXCEPTION"), ValidationError("invalid_retrieval_query"), ConfigurationError("PRIVATE-CONFIG"), StorageError("PRIVATE-STORAGE")])
def test_l4_all_ordinary_failures_have_one_valid_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, error: Exception,
) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    def fail(*args: object) -> object:
        raise error
    monkeypatch.setattr(service, "_" + operation, fail)
    request = {"admit": _request(), "retrieve": RetrievalRequest(query="PRIVATE-QUERY", limit=1, token_budget=128), "forget": ForgetRequest(memory_id="PRIVATE-CALLER-ID")}[operation]
    with pytest.raises(type(error)) as caught:
        getattr(service, operation)(RequestContext(user_id=PRIVATE_USER, request_id="failure"), request)
    assert caught.value is error
    terminals = [e for e in sink.events if e.event_name.value == {"admit": "admission_completed", "retrieve": "retrieval_completed", "forget": "forgetting_completed"}[operation]]
    assert len(terminals) == 1
    assert terminals[0].outcome is EventOutcome.FAILED
    expected = EventReasonCode.CONFIGURATION_MISMATCH if isinstance(error, ConfigurationError) else EventReasonCode.STORAGE_FAILURE if isinstance(error, StorageError) else EventReasonCode.INVALID_ADMISSION_REQUEST if operation == "admit" and isinstance(error, ValidationError) else EventReasonCode.OPERATION_FAILED
    assert terminals[0].reason_code is expected
    assert b"PRIVATE-EXCEPTION" not in b"".join(sink.lines)
    assert b"PRIVATE-CALLER-ID" not in b"".join(sink.lines)


def test_l4_serializer_revalidates_frozen_event() -> None:
    event = MemoryEvent(schema_version=1, event_name=EventName.CONFIGURATION_FAILED,
        occurred_at=NOW, duration_ms=0, outcome=EventOutcome.FAILED,
        reason_code=EventReasonCode.CONFIGURATION_MISMATCH)
    object.__setattr__(event, "duration_ms", True)
    with pytest.raises(ValueError):
        serialize_json_line(event)


def _assert_recursive_privacy(sink: CaptureEventSink, sentinels: tuple[str, ...]) -> None:
    def scan(value: object) -> None:
        if is_dataclass(value) and not isinstance(value, type):
            scan({field.name: getattr(value, field.name) for field in fields(value)})
        elif isinstance(value, dict):
            assert not FORBIDDEN_EVENT_FIELDS.intersection(value)
            assert set(value) <= {field.name for field in fields(MemoryEvent)}
            for key, item in value.items():
                scan(key)
                scan(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                scan(item)
        elif isinstance(value, (str, bytes)):
            raw = value.encode() if isinstance(value, str) else value
            assert all(sentinel.encode() not in raw for sentinel in sentinels)
    for event, line in zip(sink.events, sink.lines, strict=True):
        scan(event)
        scan(json.loads(line))
        scan(line)


@pytest.mark.parametrize("failure_stage", ["publication", "acknowledgement"])
def test_l4_pending_and_failed_admission_retry_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str) -> None:
    sink = CaptureEventSink()
    service, repository, embedder, _ = _service(tmp_path, sink=sink)
    adapter, method = (service._vector_index, "add") if failure_stage == "publication" else (repository, "mark_indexed")
    original = getattr(adapter, method)
    error = IndexingError("PRIVATE-INDEX-ERROR") if failure_stage == "publication" else StorageError("PRIVATE-ACK-ERROR")
    def fail(**kwargs: object) -> None:
        raise error
    monkeypatch.setattr(adapter, method, fail)
    context = RequestContext(user_id=PRIVATE_USER, request_id="retry")
    first = service.admit(context, _request())
    assert not first.retrievable
    monkeypatch.setattr(adapter, method, original)
    offset = len(sink.events)
    second = service.admit(context, _request())
    assert second.retrievable and second.memory_id == first.memory_id
    assert len(embedder.calls) == 1
    names = _names(sink, after=offset)
    assert names.count(EventName.ADMISSION_RETRY) == 1
    assert names.count(EventName.ADMISSION_COMPLETED) == 1
    assert names[-1] is EventName.ADMISSION_COMPLETED
    assert sink.events[-1].retry_count == 1
    _assert_recursive_privacy(sink, (PRIVATE_USER, KEY.decode(), "PRIVATE-INDEX-ERROR", "PRIVATE-ACK-ERROR", "PRIVATE-SUBJECT", "PRIVATE-VALUE"))


def test_l4_historical_retrieval_has_scoped_events_and_complete_privacy_scan(tmp_path: Path) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="history")
    original = service.admit(context, _request(content="PRIVATE-CONTENT"))
    service.admit(context, replace(_request(idempotency_key="PRIVATE-IDEMPOTENCY", content="PRIVATE-REPLACEMENT"), supersedes_memory_id=original.memory_id))
    offset = len(sink.events)
    result = service.retrieve(context, RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=1000, intent=RetrievalIntent.HISTORICAL))
    assert original.memory_id in result.included_memory_ids
    assert [e.stage for e in sink.events[offset:] if e.event_name is EventName.STORAGE_COMPLETED] == [EventStage.HISTORICAL_ALLOWLIST, EventStage.HISTORICAL_HYDRATION]
    assert _names(sink, after=offset).count(EventName.RETRIEVAL_COMPLETED) == 1
    _assert_recursive_privacy(sink, (PRIVATE_USER, KEY.decode(), "PRIVATE-CONTENT", "PRIVATE-QUERY", "PRIVATE-SUBJECT", "PRIVATE-VALUE", "PRIVATE-REPLACEMENT", "PRIVATE-IDEMPOTENCY", "PRIVATE-CONVERSATION", "PRIVATE-TURN"))


def test_l4_concurrent_requests_keep_correlation(tmp_path: Path) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    barrier = Barrier(2)
    def admit(number: int) -> object:
        barrier.wait(timeout=5)
        return service.admit(RequestContext(user_id=f"PRIVATE-USER-{number}", request_id=f"request-{number}"), _request(idempotency_key=f"key-{number}"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(result.retrievable for result in pool.map(admit, (1, 2)))
    for number in (1, 2):
        events = [e for e in sink.events if e.request_id == f"request-{number}"]
        assert events[-1].event_name is EventName.ADMISSION_COMPLETED
        assert sum(e.event_name is EventName.ADMISSION_COMPLETED for e in events) == 1
        assert all(e.user_ref == HmacUserPseudonymizer(KEY).pseudonymize(f"PRIVATE-USER-{number}") for e in events)
    _assert_recursive_privacy(sink, ("PRIVATE-USER-1", "PRIVATE-USER-2", KEY.decode()))


@pytest.mark.parametrize("operation,seam", [("admit", "lookup"), ("retrieve", "lookup"), ("forget", "lookup"), ("retrieve", "clock"), ("forget", "clock"), ("retrieve", "tokens")])
def test_l4_real_failure_seams_preserve_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, seam: str) -> None:
    sink = CaptureEventSink()
    service, repository, _, clock = _service(tmp_path, sink=sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="seam")
    admitted = service.admit(context, _request())
    offset = len(sink.events)
    error = RuntimeError("PRIVATE-SEAM-ERROR")
    def fail(*args: object, **kwargs: object) -> object:
        raise error
    target, method = (clock, "now") if seam == "clock" else (service._token_counter, "count_tokens") if seam == "tokens" else (repository, {"admit": "find", "retrieve": "current_state_vector_ids", "forget": "find_forgetting_target"}[operation])
    monkeypatch.setattr(target, method, fail)
    request = {"admit": _request(idempotency_key="new"), "retrieve": RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=128), "forget": ForgetRequest(memory_id=admitted.memory_id)}[operation]
    with pytest.raises(RuntimeError) as caught:
        getattr(service, operation)(context, request)
    assert caught.value is error
    terminal = {"admit": EventName.ADMISSION_COMPLETED, "retrieve": EventName.RETRIEVAL_COMPLETED, "forget": EventName.FORGETTING_COMPLETED}[operation]
    assert sum(e.event_name is terminal for e in sink.events[offset:]) == 1
    assert sink.events[-1].event_name is terminal
    _assert_recursive_privacy(sink, (PRIVATE_USER, KEY.decode(), "PRIVATE-SEAM-ERROR", "PRIVATE-QUERY"))


def test_l4_supersession_lookup_never_emits_unresolved_caller_id(tmp_path: Path) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    with pytest.raises(ValidationError, match="invalid_supersession_target"):
        service.admit(RequestContext(user_id=PRIVATE_USER, request_id="target"), replace(_request(), supersedes_memory_id="PRIVATE-UNRESOLVED-TARGET"))
    _assert_recursive_privacy(sink, ("PRIVATE-UNRESOLVED-TARGET", PRIVATE_USER, KEY.decode()))
    assert sink.events[-1].reason_code is EventReasonCode.INVALID_SUPERSESSION_TARGET


@pytest.mark.parametrize("operation", ["admit", "retrieve", "forget"])
def test_l4_baseexception_propagates_without_conversion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    error = KeyboardInterrupt()
    def fail(*args: object) -> object:
        raise error
    monkeypatch.setattr(service, "_" + operation, fail)
    with pytest.raises(KeyboardInterrupt) as caught:
        getattr(service, operation)(RequestContext(user_id=PRIVATE_USER, request_id="control"), object())
    assert caught.value is error
    assert not sink.events


@pytest.mark.parametrize("constructor", ["SQLiteMemoryRepository", "SentenceTransformerEmbedder", "TiktokenTokenCounter", "FaissRecoveryAdapter", "FaissVectorIndex", "MemoryService"])
@pytest.mark.parametrize("configuration", [False, True])
def test_l4_complete_local_startup_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constructor: str, configuration: bool) -> None:
    sink = CaptureEventSink()
    error = ConfigurationError("PRIVATE-STARTUP-CONFIG") if configuration else RuntimeError("PRIVATE-STARTUP-ERROR")
    def fail(*args: object, **kwargs: object) -> object:
        raise error
    monkeypatch.setattr(composition, "SentenceTransformerEmbedder", lambda **kwargs: _Embedder())
    monkeypatch.setattr(composition, "TiktokenTokenCounter", _TokenCounter)
    monkeypatch.setattr(composition, constructor, fail)
    with pytest.raises(type(error)) as caught:
        composition.compose_local_memory_service(database_path=tmp_path / "memory.sqlite3", index_directory=tmp_path / "index", model_cache_directory=tmp_path / "cache", clock=_LifecycleClock(), memory_ids=_MemoryIds(), relevance_threshold=0.50, event_sink=sink, telemetry_clock=_TelemetryClock(), user_hmac_key=KEY)
    assert caught.value is error
    assert _names(sink).count(EventName.STARTUP_UNAVAILABLE) == 1
    assert sink.events[-1].event_name is EventName.STARTUP_UNAVAILABLE
    assert _names(sink).count(EventName.CONFIGURATION_FAILED) == int(configuration)
    if configuration:
        assert _names(sink)[-2:] == [EventName.CONFIGURATION_FAILED, EventName.STARTUP_UNAVAILABLE]
    _assert_recursive_privacy(sink, (KEY.decode(), "PRIVATE-STARTUP-ERROR", "PRIVATE-STARTUP-CONFIG"))


@pytest.mark.parametrize("operation", ["admit", "retrieve", "forget"])
@pytest.mark.parametrize("failure", ["conversion", "validation"])
def test_l4_complete_event_conversion_is_noninterfering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, failure: str) -> None:
    from conversational_memory.application import service as service_module
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="isolated")
    admitted = service.admit(context, _request())
    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("PRIVATE-CONVERSION-ERROR")
    monkeypatch.setattr(service_module, "privacy_safe_reason_code" if failure == "conversion" else "MemoryEvent", fail)
    request = {"admit": _request(idempotency_key="isolated-new"), "retrieve": RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=1000), "forget": ForgetRequest(memory_id=admitted.memory_id)}[operation]
    result = getattr(service, operation)(context, request)
    assert result is not None
    error = RuntimeError("PRIVATE-PRIMARY-ERROR")
    def primary_fail(*args: object) -> object:
        raise error
    monkeypatch.setattr(service, "_" + operation, primary_fail)
    with pytest.raises(RuntimeError) as caught:
        getattr(service, operation)(context, request)
    assert caught.value is error
    _assert_recursive_privacy(sink, ("PRIVATE-CONVERSION-ERROR", "PRIVATE-PRIMARY-ERROR", PRIVATE_USER, KEY.decode()))


def test_l4_unknown_recovery_reason_does_not_change_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from conversational_memory.application.recovery import RecoveryResult
    sink = CaptureEventSink()
    coordinator = _observed_recovery(repository=_RecoveryRepository(_recovery_inventory()), vector_index=_RecoveryIndex(RecoveryPublication(False, 0, 0)), sink=sink)
    result = RecoveryResult(RecoveryReadiness.UNAVAILABLE, "PRIVATE-UNKNOWN-REASON", False, 0, 0, 0, 0, 0)
    monkeypatch.setattr(coordinator, "_recover", lambda: result)
    assert coordinator.recover() is result
    assert sink.events[-1].reason_code is EventReasonCode.UNAVAILABLE_REBUILD
    _assert_recursive_privacy(sink, ("PRIVATE-UNKNOWN-REASON", KEY.decode()))


@pytest.mark.parametrize("operation", ["retrieve", "forget"])
def test_l4_invalid_lifecycle_clock_emits_configuration_then_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    sink = CaptureEventSink()
    service, _, _, clock = _service(tmp_path, sink=sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="clock")
    admitted = service.admit(context, _request())
    offset = len(sink.events)
    calls = clock.calls
    def invalid() -> datetime:
        clock.calls += 1
        return NOW.replace(tzinfo=None)
    monkeypatch.setattr(clock, "now", invalid)
    request = RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=128) if operation == "retrieve" else ForgetRequest(memory_id=admitted.memory_id)
    with pytest.raises(ConfigurationError, match="invalid_trusted_clock"):
        getattr(service, operation)(context, request)
    assert clock.calls == calls + 1
    names = _names(sink, after=offset)
    assert names[-2:] == [EventName.CONFIGURATION_FAILED, EventName.RETRIEVAL_COMPLETED if operation == "retrieve" else EventName.FORGETTING_COMPLETED]
    assert sink.events[-1].reason_code is EventReasonCode.CONFIGURATION_MISMATCH


@pytest.mark.parametrize("constructor", ["direct", "recovered"])
def test_l4_composition_threshold_failure_is_observed(tmp_path: Path, constructor: str) -> None:
    sink = CaptureEventSink()
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    common = {"repository": repository, "embedder": _Embedder(), "token_counter": _TokenCounter(), "clock": _LifecycleClock(), "memory_ids": _MemoryIds(), "relevance_threshold": None, "event_sink": sink, "telemetry_clock": _TelemetryClock(), "user_hmac_key": KEY}
    with pytest.raises(ConfigurationError, match="invalid_relevance_threshold"):
        if constructor == "direct":
            composition.compose_memory_service(vector_index=FaissVectorIndex(tmp_path / "index", embedding_model=MODEL, vector_dimension=2, create_if_missing=True), **common)
        else:
            compose_recovered_memory_service(index_directory=tmp_path / "index", embedding_model=MODEL, vector_dimension=2, **common)
    assert _names(sink)[-2:] == [EventName.CONFIGURATION_FAILED, EventName.STARTUP_UNAVAILABLE]
    assert _names(sink).count(EventName.STARTUP_UNAVAILABLE) == 1
    _assert_recursive_privacy(sink, (KEY.decode(), PRIVATE_USER))


@pytest.mark.parametrize("content", ["password=PRIVATE-PASSWORD", "api_key=PRIVATE-API-KEY", "Authorization: Bearer PRIVATE-AUTH-TOKEN"])
def test_l4_credential_forms_have_recursive_object_and_byte_scans(tmp_path: Path, content: str) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="credentials")
    service.admit(context, _request(content=content))
    service.retrieve(context, RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=1000))
    _assert_recursive_privacy(sink, (PRIVATE_USER, KEY.decode(), "PRIVATE-PASSWORD", "PRIVATE-API-KEY", "PRIVATE-AUTH-TOKEN", "PRIVATE-QUERY", "PRIVATE-SUBJECT", "PRIVATE-VALUE", "PRIVATE-CONVERSATION", "PRIVATE-TURN"))


def test_l4_real_admission_validation_has_terminal_without_side_effects(tmp_path: Path) -> None:
    sink = CaptureEventSink()
    service, repository, embedder, clock = _service(tmp_path, sink=sink)
    with pytest.raises(ValidationError, match="invalid_admission_request"):
        service.admit(RequestContext(user_id=PRIVATE_USER, request_id="validation"), _request(content="   "))
    assert _names(sink) == [EventName.ADMISSION_COMPLETED]
    assert sink.events[0].reason_code is EventReasonCode.INVALID_ADMISSION_REQUEST
    assert not embedder.calls and clock.calls == 0
    assert repository.find(user_id=PRIVATE_USER, idempotency_key="admit-1") is None


def test_l4_invalid_completion_clock_preserves_pending_cleanup_and_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sink = CaptureEventSink()
    service, repository, _, clock = _service(tmp_path, sink=sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="completion-clock")
    admitted = service.admit(context, _request())
    offset = len(sink.events)
    values = iter((NOW, NOW.replace(tzinfo=None)))
    monkeypatch.setattr(clock, "now", lambda: next(values))
    with pytest.raises(ConfigurationError, match="invalid_trusted_clock"):
        service.forget(context, ForgetRequest(memory_id=admitted.memory_id))
    assert _names(sink, after=offset)[-2:] == [EventName.CONFIGURATION_FAILED, EventName.FORGETTING_COMPLETED]
    assert sink.events[-1].reason_code is EventReasonCode.CONFIGURATION_MISMATCH
    target = repository.find_forgetting_target(user_id=PRIVATE_USER, memory_id=admitted.memory_id)
    assert target is not None and target.forgetting is not None
    assert target.forgetting.completed_at is None
    assert target.memory.deleted_at == NOW


@pytest.mark.parametrize("constructor,error,reason,config", [
    ("SQLiteMemoryRepository", StorageError("PRIVATE-SQLITE"), EventReasonCode.UNAVAILABLE_SCHEMA_OR_MIGRATION, False),
    ("SentenceTransformerEmbedder", ServiceUnavailableError("PRIVATE-MODEL"), EventReasonCode.UNAVAILABLE_EMBEDDING_CONFIGURATION, True),
    ("TiktokenTokenCounter", ValidationError("invalid_tokenizer_configuration"), EventReasonCode.CONFIGURATION_MISMATCH, True),
    ("FaissVectorIndex", ServiceUnavailableError("PRIVATE-FAISS"), EventReasonCode.UNAVAILABLE_POST_PUBLICATION_VERIFICATION, False),
])
def test_l4_startup_constructor_failure_families(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constructor: str, error: Exception, reason: EventReasonCode, config: bool) -> None:
    sink = CaptureEventSink()
    def fail(*args: object, **kwargs: object) -> object:
        raise error
    monkeypatch.setattr(composition, "SentenceTransformerEmbedder", lambda **kwargs: _Embedder())
    monkeypatch.setattr(composition, "TiktokenTokenCounter", _TokenCounter)
    monkeypatch.setattr(composition, constructor, fail)
    with pytest.raises(Exception) as caught:
        composition.compose_local_memory_service(database_path=tmp_path / "memory.sqlite3", index_directory=tmp_path / "index", model_cache_directory=tmp_path / "cache", clock=_LifecycleClock(), memory_ids=_MemoryIds(), relevance_threshold=0.50, event_sink=sink, telemetry_clock=_TelemetryClock(), user_hmac_key=KEY)
    if constructor == "SQLiteMemoryRepository":
        assert isinstance(caught.value, ServiceUnavailableError)
        assert caught.value.__cause__ is error
    else:
        assert caught.value is error
    assert sink.events[-1].reason_code is reason
    assert _names(sink).count(EventName.STARTUP_UNAVAILABLE) == 1
    assert _names(sink).count(EventName.CONFIGURATION_FAILED) == int(config)
    _assert_recursive_privacy(sink, ("PRIVATE-SQLITE", "PRIVATE-MODEL", "PRIVATE-FAISS", KEY.decode()))


class _Embedder:
    def __init__(self, vectors: dict[str, tuple[float, float]] | None = None) -> None:
        self._vectors = vectors or {}
        self.calls: list[str] = []

    def embed(self, content: str) -> Embedding:
        self.calls.append(content)
        return Embedding(
            values=self._vectors.get(content, (1.0, 0.0)),
            model_id=MODEL,
            dimension=2,
        )


class _TokenCounter:
    tokenizer_id = "cl100k_base"

    @staticmethod
    def count_tokens(text: str) -> int:
        return len(text)


class _InvalidTokenCounter(_TokenCounter):
    tokenizer_id = "wrong-tokenizer"


class _LifecycleClock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return NOW


class _TelemetryClock:
    def __init__(self) -> None:
        self._monotonic = 1_000_000

    def utc_now(self) -> datetime:
        return NOW

    def monotonic_ns(self) -> int:
        self._monotonic += 1_000_000
        return self._monotonic


class _RaisingTelemetryClock(_TelemetryClock):
    def monotonic_ns(self) -> int:
        raise RuntimeError("PRIVATE-TIMING-FAILURE")


class _MemoryIds:
    def __init__(self) -> None:
        self._next = 0

    def new_id(self) -> str:
        self._next += 1
        return f"memory-{self._next}"


class _RaisingSink:
    def emit(self, event: object) -> None:
        del event
        raise RuntimeError("PRIVATE-SINK-FAILURE")


class _RaisingPseudonymizer(HmacUserPseudonymizer):
    def pseudonymize(self, user_id: str) -> str:
        del user_id
        raise RuntimeError("PRIVATE-PSEUDONYM-FAILURE")


def _request(
    *,
    idempotency_key: str = "admit-1",
    content: str = "I prefer SQLite.",
) -> AdmissionRequest:
    return AdmissionRequest(
        idempotency_key=idempotency_key,
        conversation_id="PRIVATE-CONVERSATION",
        turn_id="PRIVATE-TURN",
        content=content,
        memory_type="preference",
        subject="PRIVATE-SUBJECT",
        value={"private": "PRIVATE-VALUE"},
        source_type="explicit_user",
    )


def _service(
    tmp_path: Path,
    *,
    sink: object,
    telemetry_clock: object | None = None,
    pseudonymizer: object | None = None,
    embedder: _Embedder | None = None,
    token_counter: object | None = None,
) -> tuple[MemoryService, SQLiteMemoryRepository, _Embedder, _LifecycleClock]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    vector_index = FaissVectorIndex(
        tmp_path / "index",
        embedding_model=MODEL,
        vector_dimension=2,
        create_if_missing=True,
    )
    actual_embedder = embedder or _Embedder()
    lifecycle_clock = _LifecycleClock()
    service = MemoryService(
        idempotency=repository,
        embedder=actual_embedder,
        repository=repository,
        vector_index=vector_index,
        token_counter=token_counter or _TokenCounter(),
        clock=lifecycle_clock,
        memory_ids=_MemoryIds(),
        relevance_threshold=0.50,
        event_sink=sink,  # type: ignore[arg-type]
        telemetry_clock=telemetry_clock or _TelemetryClock(),  # type: ignore[arg-type]
        user_pseudonymizer=pseudonymizer or HmacUserPseudonymizer(KEY),  # type: ignore[arg-type]
    )
    return service, repository, actual_embedder, lifecycle_clock


def _names(sink: CaptureEventSink, *, after: int = 0) -> list[EventName]:
    return [event.event_name for event in sink.events[after:]]


def test_admission_emits_ordered_internal_events_then_one_terminal(tmp_path: Path) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)

    result = service.admit(
        RequestContext(user_id=PRIVATE_USER, request_id="request-admit"),
        _request(),
    )

    assert result.reason == "accepted_and_indexed"
    assert _names(sink) == [
        EventName.STORAGE_COMPLETED,
        EventName.INDEXING_COMPLETED,
        EventName.STORAGE_COMPLETED,
        EventName.INDEXING_COMPLETED,
        EventName.STORAGE_COMPLETED,
        EventName.ADMISSION_COMPLETED,
    ]
    assert [event.stage for event in sink.events[:-1]] == [
        EventStage.IDEMPOTENCY_LOOKUP,
        EventStage.EMBEDDING,
        EventStage.PERSIST_PENDING,
        EventStage.VECTOR_ADD,
        EventStage.MARK_INDEXED,
    ]
    terminal = sink.events[-1]
    assert terminal.outcome is EventOutcome.ACCEPTED
    assert terminal.reason_code is EventReasonCode.ACCEPTED_AND_INDEXED
    assert terminal.request_id == "request-admit"
    assert terminal.user_ref == HmacUserPseudonymizer(KEY).pseudonymize(PRIVATE_USER)
    assert terminal.memory_id == result.memory_id
    assert terminal.retry_count == 0
    assert sum(event.event_name is EventName.ADMISSION_COMPLETED for event in sink.events) == 1


def test_exact_admission_replay_emits_retry_before_terminal_without_side_effects(
    tmp_path: Path,
) -> None:
    sink = CaptureEventSink()
    service, _, embedder, _ = _service(tmp_path, sink=sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="request-replay")
    request = _request()
    first = service.admit(context, request)
    before = len(sink.events)
    embed_calls = len(embedder.calls)

    replay = service.admit(context, request)

    assert replay == first
    assert len(embedder.calls) == embed_calls
    assert _names(sink, after=before) == [
        EventName.STORAGE_COMPLETED,
        EventName.ADMISSION_RETRY,
        EventName.ADMISSION_COMPLETED,
    ]
    assert sink.events[-1].retry_count == 1


def test_admission_application_exception_is_unchanged_and_terminal_is_last(
    tmp_path: Path,
) -> None:
    sink = CaptureEventSink()
    service, _, embedder, _ = _service(tmp_path, sink=sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="request-conflict")
    service.admit(context, _request())
    before = len(sink.events)
    embed_calls = len(embedder.calls)

    with pytest.raises(ValidationError, match="idempotency_key_conflict") as raised:
        service.admit(context, _request(content="Changed private content"))

    assert raised.value.reason == "idempotency_key_conflict"
    assert len(embedder.calls) == embed_calls
    assert _names(sink, after=before) == [
        EventName.STORAGE_COMPLETED,
        EventName.ADMISSION_COMPLETED,
    ]
    terminal = sink.events[-1]
    assert terminal.outcome is EventOutcome.FAILED
    assert terminal.reason_code is EventReasonCode.IDEMPOTENCY_KEY_CONFLICT
    assert terminal.retry_count == 0


def test_sensitive_rejection_collapses_reason_and_logs_no_sensitive_data(
    tmp_path: Path,
) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    credential = "password=PRIVATE-CREDENTIAL"

    result = service.admit(
        RequestContext(user_id=PRIVATE_USER, request_id="request-sensitive"),
        _request(content=credential),
    )

    assert result.reason == "sensitive_credential"
    assert _names(sink) == [EventName.STORAGE_COMPLETED, EventName.ADMISSION_COMPLETED]
    terminal = sink.events[-1]
    assert terminal.outcome is EventOutcome.REJECTED
    assert terminal.reason_code is EventReasonCode.SENSITIVE_ADMISSION_REJECTED
    serialized = b"".join(sink.lines).decode()
    for private in (credential, "PRIVATE-CREDENTIAL", PRIVATE_USER, KEY.decode()):
        assert private not in serialized


def test_retrieval_emits_selected_metadata_and_one_terminal(tmp_path: Path) -> None:
    sink = CaptureEventSink()
    service, _, _, lifecycle_clock = _service(tmp_path, sink=sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="request-admit")
    admitted = service.admit(context, _request())
    before = len(sink.events)
    clock_calls = lifecycle_clock.calls

    result = service.retrieve(
        RequestContext(user_id=PRIVATE_USER, request_id="request-retrieve"),
        RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=1000),
    )

    assert result.outcome is RetrievalOutcome.MEMORIES_SELECTED
    assert lifecycle_clock.calls == clock_calls + 1
    assert _names(sink, after=before) == [
        EventName.STORAGE_COMPLETED,
        EventName.STORAGE_COMPLETED,
        EventName.INDEXING_COMPLETED,
        EventName.INDEXING_COMPLETED,
        EventName.STORAGE_COMPLETED,
        EventName.RETRIEVAL_COMPLETED,
    ]
    terminal = sink.events[-1]
    assert terminal.outcome is EventOutcome.MEMORIES_SELECTED
    assert terminal.reason_code is EventReasonCode.MEMORIES_SELECTED
    assert terminal.candidate_count == 1
    assert terminal.returned_count == 1
    assert terminal.memory_ids == (admitted.memory_id,)
    assert terminal.token_budget == 1000
    assert terminal.tokens_used == result.tokens_used


def test_retrieval_no_eligible_and_no_relevant_outcomes_are_distinct(
    tmp_path: Path,
) -> None:
    empty_sink = CaptureEventSink()
    empty_service, _, _, _ = _service(tmp_path / "empty", sink=empty_sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="request-empty")

    empty = empty_service.retrieve(
        context,
        RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=100),
    )

    assert empty.outcome is RetrievalOutcome.NO_ELIGIBLE_MEMORY
    assert _names(empty_sink)[-1] is EventName.RETRIEVAL_COMPLETED
    assert empty_sink.events[-1].outcome is EventOutcome.NO_ELIGIBLE_MEMORY
    assert empty_sink.events[-1].candidate_count == 0

    irrelevant_sink = CaptureEventSink()
    embedder = _Embedder(
        {
            "I prefer SQLite.": (0.0, 1.0),
            "PRIVATE-UNRELATED-QUERY": (1.0, 0.0),
        }
    )
    service, _, _, _ = _service(
        tmp_path / "irrelevant",
        sink=irrelevant_sink,
        embedder=embedder,
    )
    service.admit(context, _request())
    before = len(irrelevant_sink.events)

    irrelevant = service.retrieve(
        context,
        RetrievalRequest(query="PRIVATE-UNRELATED-QUERY", limit=5, token_budget=100),
    )

    assert irrelevant.outcome is RetrievalOutcome.NO_RELEVANT_MEMORY
    terminal = irrelevant_sink.events[-1]
    assert _names(irrelevant_sink, after=before)[-1] is EventName.RETRIEVAL_COMPLETED
    assert terminal.outcome is EventOutcome.NO_RELEVANT_MEMORY
    assert terminal.reason_code is EventReasonCode.NO_RELEVANT_MEMORY
    assert terminal.candidate_count == 1
    assert terminal.returned_count == 0
    assert terminal.memory_ids == ()


def test_retrieval_budget_exclusion_retains_exact_zero_token_metadata(
    tmp_path: Path,
) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    context = RequestContext(user_id=PRIVATE_USER, request_id="request-budget")
    service.admit(context, _request())
    before = len(sink.events)

    result = service.retrieve(
        context,
        RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=0),
    )

    assert result.outcome is RetrievalOutcome.BUDGET_EXCLUDED
    assert _names(sink, after=before)[-1] is EventName.RETRIEVAL_COMPLETED
    terminal = sink.events[-1]
    assert terminal.outcome is EventOutcome.BUDGET_EXCLUDED
    assert terminal.reason_code is EventReasonCode.BUDGET_EXCLUDED
    assert terminal.candidate_count == 1
    assert terminal.returned_count == 0
    assert terminal.memory_ids == ()
    assert terminal.token_budget == 0
    assert terminal.tokens_used == 0


def test_ordinary_application_exception_is_unchanged_and_has_one_terminal(
    tmp_path: Path,
) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(
        tmp_path,
        sink=sink,
        token_counter=_InvalidTokenCounter(),
    )

    with pytest.raises(ValidationError, match="invalid_tokenizer_configuration") as raised:
        service.retrieve(
            RequestContext(user_id=PRIVATE_USER, request_id="request-error"),
            RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=100),
        )

    assert raised.value.reason == "invalid_tokenizer_configuration"
    assert _names(sink) == [
        EventName.CONFIGURATION_FAILED,
        EventName.RETRIEVAL_COMPLETED,
    ]
    assert sink.events[-1].outcome is EventOutcome.FAILED
    assert sink.events[-1].reason_code is EventReasonCode.CONFIGURATION_MISMATCH


def test_captured_objects_and_lines_pass_complete_privacy_scan(tmp_path: Path) -> None:
    sink = CaptureEventSink()
    service, _, _, _ = _service(tmp_path, sink=sink)
    service.admit(
        RequestContext(user_id=PRIVATE_USER, request_id="request-private"),
        _request(content="password=PRIVATE-CREDENTIAL"),
    )
    service.retrieve(
        RequestContext(user_id=PRIVATE_USER, request_id="request-query-private"),
        RetrievalRequest(query="PRIVATE-QUERY", limit=5, token_budget=100),
    )
    serialized = b"".join(sink.lines).decode("utf-8")

    assert not FORBIDDEN_EVENT_FIELDS.intersection(
        key for line in sink.lines for key in json.loads(line)
    )
    for private in (
        PRIVATE_USER,
        KEY.decode(),
        "PRIVATE-CONVERSATION",
        "PRIVATE-TURN",
        "PRIVATE-SUBJECT",
        "PRIVATE-VALUE",
        "PRIVATE-CREDENTIAL",
        "PRIVATE-EXCEPTION-TEXT",
    ):
        assert private not in serialized
    assert all(PRIVATE_USER not in repr(event) for event in sink.events)


def test_storage_exception_text_is_not_emitted(tmp_path: Path) -> None:
    class _FailingRepository(SQLiteMemoryRepository):
        def find(self, *, user_id: str, idempotency_key: str) -> object:
            del user_id, idempotency_key
            raise StorageError("PRIVATE-EXCEPTION-TEXT")

    tmp_path.mkdir(parents=True, exist_ok=True)
    repository = _FailingRepository(tmp_path / "memory.sqlite3")
    sink = CaptureEventSink()
    service = MemoryService(
        idempotency=repository,
        embedder=_Embedder(),
        repository=repository,
        vector_index=FaissVectorIndex(
            tmp_path / "index",
            embedding_model=MODEL,
            vector_dimension=2,
            create_if_missing=True,
        ),
        token_counter=_TokenCounter(),
        clock=_LifecycleClock(),
        memory_ids=_MemoryIds(),
        relevance_threshold=0.50,
        event_sink=sink,
        telemetry_clock=_TelemetryClock(),
        user_pseudonymizer=HmacUserPseudonymizer(KEY),
    )

    with pytest.raises(StorageError, match="PRIVATE-EXCEPTION-TEXT"):
        service.admit(
            RequestContext(user_id=PRIVATE_USER, request_id="request-storage-error"),
            _request(),
        )

    assert _names(sink) == [EventName.STORAGE_FAILED, EventName.ADMISSION_COMPLETED]
    assert "PRIVATE-EXCEPTION-TEXT" not in b"".join(sink.lines).decode()


@pytest.mark.parametrize("failure", ["sink", "timing", "pseudonym", "serialization"])
def test_observability_failures_do_not_change_primary_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    sink: object = CaptureEventSink()
    telemetry: object = _TelemetryClock()
    pseudonymizer: object = HmacUserPseudonymizer(KEY)
    if failure == "sink":
        sink = _RaisingSink()
    elif failure == "timing":
        telemetry = _RaisingTelemetryClock()
    elif failure == "pseudonym":
        pseudonymizer = _RaisingPseudonymizer(KEY)
    else:
        def _fail_serialization(event: object) -> bytes:
            del event
            raise ValueError("PRIVATE-SERIALIZATION-FAILURE")

        monkeypatch.setattr(
            "conversational_memory.application.events.serialize_json_line",
            _fail_serialization,
        )
    service, repository, _, _ = _service(
        tmp_path,
        sink=sink,
        telemetry_clock=telemetry,
        pseudonymizer=pseudonymizer,
    )
    request = _request(content="password=PRIVATE-CREDENTIAL")

    result = service.admit(
        RequestContext(user_id=PRIVATE_USER, request_id="request-failure"),
        request,
    )

    assert result.reason == "sensitive_credential"
    assert result.memory_id is None
    assert repository.find(user_id=PRIVATE_USER, idempotency_key="admit-1") is None


def test_forgetting_success_and_completed_replay_have_exact_ordering(
    tmp_path: Path,
) -> None:
    sink = CaptureEventSink()
    service, repository, _, lifecycle_clock = _service(tmp_path, sink=sink)
    owner = RequestContext(user_id=PRIVATE_USER, request_id="request-forget")
    admitted = service.admit(owner, _request())
    assert admitted.memory_id is not None
    before = len(sink.events)
    before_clock = lifecycle_clock.calls

    forgotten = service.forget(owner, ForgetRequest(memory_id=admitted.memory_id))

    assert forgotten.reason == "forgotten"
    assert lifecycle_clock.calls == before_clock + 2
    forgetting_events = sink.events[before:]
    assert [event.event_name for event in forgetting_events] == [
        EventName.STORAGE_COMPLETED,
        EventName.STORAGE_COMPLETED,
        EventName.INDEXING_COMPLETED,
        EventName.STORAGE_COMPLETED,
        EventName.FORGETTING_COMPLETED,
    ]
    assert [event.stage for event in forgetting_events[:-1]] == [
        EventStage.FORGETTING_LOOKUP,
        EventStage.BEGIN_FORGETTING,
        EventStage.VECTOR_REMOVE,
        EventStage.ACKNOWLEDGE_FORGETTING,
    ]
    assert forgetting_events[0].memory_id is None
    assert all(
        event.memory_id == admitted.memory_id for event in forgetting_events[1:]
    )
    terminal = forgetting_events[-1]
    assert terminal.outcome is EventOutcome.FORGOTTEN
    assert terminal.reason_code is EventReasonCode.FORGOTTEN
    assert repository.find_forgetting_target(
        user_id=PRIVATE_USER,
        memory_id=admitted.memory_id,
    ) is not None

    before = len(sink.events)
    before_clock = lifecycle_clock.calls
    replay = service.forget(owner, ForgetRequest(memory_id=admitted.memory_id))

    assert replay.reason == "already_forgotten"
    assert lifecycle_clock.calls == before_clock
    assert _names(sink, after=before) == [
        EventName.STORAGE_COMPLETED,
        EventName.FORGETTING_COMPLETED,
    ]
    assert sink.events[-1].reason_code is EventReasonCode.ALREADY_FORGOTTEN
    assert sink.events[-1].memory_id == admitted.memory_id


def test_nonexistent_and_cross_owner_forgetting_are_equally_opaque(
    tmp_path: Path,
) -> None:
    sink = CaptureEventSink()
    service, _, _, lifecycle_clock = _service(tmp_path, sink=sink)
    owner = RequestContext(user_id=PRIVATE_USER, request_id="request-owner")
    admitted = service.admit(owner, _request())
    assert admitted.memory_id is not None
    before_clock = lifecycle_clock.calls

    results = []
    event_groups = []
    for context, memory_id in (
        (owner, "nonexistent-memory"),
        (
            RequestContext(user_id="other-private-user", request_id="request-other"),
            admitted.memory_id,
        ),
    ):
        before = len(sink.events)
        results.append(service.forget(context, ForgetRequest(memory_id=memory_id)))
        event_groups.append(sink.events[before:])

    assert results[0] == results[1]
    assert lifecycle_clock.calls == before_clock
    for events in event_groups:
        assert [event.event_name for event in events] == [
            EventName.STORAGE_COMPLETED,
            EventName.FORGETTING_COMPLETED,
        ]
        assert all(event.memory_id is None for event in events)
        assert events[-1].outcome is EventOutcome.NOT_FOUND
        assert events[-1].reason_code is EventReasonCode.MEMORY_NOT_FOUND
        assert sum(
            event.event_name is EventName.FORGETTING_COMPLETED for event in events
        ) == 1


def test_vector_removal_failure_emits_cleanup_pending_without_changing_semantics(
    tmp_path: Path,
) -> None:
    class _FailingRemoveIndex(FaissVectorIndex):
        def remove(self, *, vector_id: int) -> None:
            del vector_id
            raise IndexingError("PRIVATE-REMOVE-FAILURE")

    initial_sink = CaptureEventSink()
    initial, repository, embedder, lifecycle_clock = _service(
        tmp_path,
        sink=initial_sink,
    )
    owner = RequestContext(user_id=PRIVATE_USER, request_id="request-remove-failure")
    admitted = initial.admit(owner, _request())
    assert admitted.memory_id is not None
    sink = CaptureEventSink()
    service = MemoryService(
        idempotency=repository,
        embedder=embedder,
        repository=repository,
        vector_index=_FailingRemoveIndex(
            tmp_path / "index",
            embedding_model=MODEL,
            vector_dimension=2,
        ),
        token_counter=_TokenCounter(),
        clock=lifecycle_clock,
        memory_ids=_MemoryIds(),
        relevance_threshold=0.50,
        event_sink=sink,
        telemetry_clock=_TelemetryClock(),
        user_pseudonymizer=HmacUserPseudonymizer(KEY),
    )
    before_clock = lifecycle_clock.calls

    result = service.forget(owner, ForgetRequest(memory_id=admitted.memory_id))

    assert result.reason == "physical_cleanup_pending"
    assert result.retryable is True
    assert lifecycle_clock.calls == before_clock + 1
    assert _names(sink) == [
        EventName.STORAGE_COMPLETED,
        EventName.STORAGE_COMPLETED,
        EventName.INDEXING_FAILED,
        EventName.FORGETTING_COMPLETED,
    ]
    assert sink.events[-2].stage is EventStage.VECTOR_REMOVE
    assert sink.events[-1].outcome is EventOutcome.CLEANUP_PENDING
    assert sink.events[-1].reason_code is EventReasonCode.PHYSICAL_CLEANUP_PENDING
    assert "PRIVATE-REMOVE-FAILURE" not in b"".join(sink.lines).decode()


def test_acknowledgement_failure_and_retry_emit_existing_cleanup_state(
    tmp_path: Path,
) -> None:
    class _FailingAcknowledgementRepository(SQLiteMemoryRepository):
        def acknowledge_forgetting_complete(
            self,
            *,
            user_id: str,
            memory_id: str,
            vector_id: int,
            completed_at: datetime,
        ) -> ForgettingRecord:
            del user_id, memory_id, vector_id, completed_at
            raise StorageError("PRIVATE-ACKNOWLEDGEMENT-FAILURE")

    initial_sink = CaptureEventSink()
    initial, _, embedder, lifecycle_clock = _service(tmp_path, sink=initial_sink)
    owner = RequestContext(user_id=PRIVATE_USER, request_id="request-ack-failure")
    admitted = initial.admit(owner, _request())
    assert admitted.memory_id is not None
    failing_repository = _FailingAcknowledgementRepository(tmp_path / "memory.sqlite3")
    failing_sink = CaptureEventSink()
    failing_service = MemoryService(
        idempotency=failing_repository,
        embedder=embedder,
        repository=failing_repository,
        vector_index=FaissVectorIndex(
            tmp_path / "index",
            embedding_model=MODEL,
            vector_dimension=2,
        ),
        token_counter=_TokenCounter(),
        clock=lifecycle_clock,
        memory_ids=_MemoryIds(),
        relevance_threshold=0.50,
        event_sink=failing_sink,
        telemetry_clock=_TelemetryClock(),
        user_pseudonymizer=HmacUserPseudonymizer(KEY),
    )

    pending = failing_service.forget(owner, ForgetRequest(memory_id=admitted.memory_id))

    assert pending.reason == "physical_cleanup_pending"
    assert _names(failing_sink) == [
        EventName.STORAGE_COMPLETED,
        EventName.STORAGE_COMPLETED,
        EventName.INDEXING_COMPLETED,
        EventName.STORAGE_FAILED,
        EventName.FORGETTING_COMPLETED,
    ]
    assert failing_sink.events[-2].stage is EventStage.ACKNOWLEDGE_FORGETTING
    assert "PRIVATE-ACKNOWLEDGEMENT-FAILURE" not in b"".join(failing_sink.lines).decode()

    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    retry_sink = CaptureEventSink()
    retry_service = MemoryService(
        idempotency=repository,
        embedder=embedder,
        repository=repository,
        vector_index=FaissVectorIndex(
            tmp_path / "index",
            embedding_model=MODEL,
            vector_dimension=2,
        ),
        token_counter=_TokenCounter(),
        clock=lifecycle_clock,
        memory_ids=_MemoryIds(),
        relevance_threshold=0.50,
        event_sink=retry_sink,
        telemetry_clock=_TelemetryClock(),
        user_pseudonymizer=HmacUserPseudonymizer(KEY),
    )

    completed = retry_service.forget(owner, ForgetRequest(memory_id=admitted.memory_id))

    assert completed.reason == "forgotten"
    assert _names(retry_sink) == [
        EventName.STORAGE_COMPLETED,
        EventName.INDEXING_COMPLETED,
        EventName.STORAGE_COMPLETED,
        EventName.FORGETTING_COMPLETED,
    ]


@pytest.mark.parametrize("failure", ["sink", "timing", "pseudonym", "serialization"])
def test_forgetting_observability_failures_do_not_change_physical_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    sink: object = CaptureEventSink()
    telemetry: object = _TelemetryClock()
    pseudonymizer: object = HmacUserPseudonymizer(KEY)
    if failure == "sink":
        sink = _RaisingSink()
    elif failure == "timing":
        telemetry = _RaisingTelemetryClock()
    elif failure == "pseudonym":
        pseudonymizer = _RaisingPseudonymizer(KEY)
    else:
        def _fail_serialization(event: object) -> bytes:
            del event
            raise ValueError("PRIVATE-SERIALIZATION-FAILURE")

        monkeypatch.setattr(
            "conversational_memory.application.events.serialize_json_line",
            _fail_serialization,
        )
    service, repository, _, lifecycle_clock = _service(
        tmp_path,
        sink=sink,
        telemetry_clock=telemetry,
        pseudonymizer=pseudonymizer,
    )

    owner = RequestContext(user_id=PRIVATE_USER, request_id="request-event-failure")
    admitted = service.admit(owner, _request())
    assert admitted.memory_id is not None
    before_clock = lifecycle_clock.calls

    result = service.forget(owner, ForgetRequest(memory_id=admitted.memory_id))

    assert result.reason == "forgotten"
    assert result.cleanup_complete is True
    assert lifecycle_clock.calls == before_clock + 2
    target = repository.find_forgetting_target(
        user_id=PRIVATE_USER,
        memory_id=admitted.memory_id,
    )
    assert target is not None
    assert target.forgetting is not None
    assert target.forgetting.cleanup_state.value == "complete"


def _recovery_inventory(
    *,
    readiness: RecoveryReadiness = RecoveryReadiness.READY,
    reason: str = "ready_existing_generation",
    pending_count: int = 0,
    failed_count: int = 0,
    cleanup_items: tuple[RecoveryCleanup, ...] = (),
) -> RecoveryInventory:
    return RecoveryInventory(
        readiness=readiness,
        reason=reason,
        rebuild_items=(),
        pending_count=pending_count,
        failed_count=failed_count,
        cleanup_pending_count=len(cleanup_items),
        cleanup_items=cleanup_items,
    )


class _RecoveryRepository:
    def __init__(self, *inventories: RecoveryInventory) -> None:
        self._inventories = iter(inventories)
        self.adoptions: list[tuple[str, str, int]] = []
        self.acknowledgements: list[tuple[str, str, int, datetime]] = []

    def recovery_inventory(
        self, *, embedding_model: str, vector_dimension: int
    ) -> RecoveryInventory:
        assert embedding_model == MODEL
        assert vector_dimension == 2
        return next(self._inventories)

    def adopt_forgetting_vector_id(
        self, *, user_id: str, memory_id: str, vector_id: int
    ) -> None:
        self.adoptions.append((user_id, memory_id, vector_id))

    def acknowledge_forgetting_complete(
        self,
        *,
        user_id: str,
        memory_id: str,
        vector_id: int,
        completed_at: datetime,
    ) -> object:
        self.acknowledgements.append(
            (user_id, memory_id, vector_id, completed_at)
        )
        return object()


class _RecoveryIndex:
    def __init__(
        self,
        *publications: RecoveryPublication,
        failure_reason: str | None = None,
    ) -> None:
        self._publications = iter(publications)
        self._failure_reason = failure_reason

    def reconcile(self, inventory: RecoveryInventory) -> RecoveryPublication:
        del inventory
        if self._failure_reason is not None:
            raise RecoveryIndexError(self._failure_reason)
        return next(self._publications)


def _observed_recovery(
    *,
    repository: object,
    vector_index: object,
    sink: object,
    telemetry_clock: object | None = None,
    pseudonymizer: object | None = None,
) -> RecoveryCoordinator:
    return RecoveryCoordinator(
        inventory=repository,  # type: ignore[arg-type]
        vector_index=vector_index,  # type: ignore[arg-type]
        embedding_model=MODEL,
        vector_dimension=2,
        clock=_LifecycleClock(),
        event_sink=sink,  # type: ignore[arg-type]
        telemetry_clock=telemetry_clock or _TelemetryClock(),  # type: ignore[arg-type]
        user_pseudonymizer=pseudonymizer or HmacUserPseudonymizer(KEY),  # type: ignore[arg-type]
    )


def test_recovery_emits_ordered_rebuild_reuse_and_exact_terminal_metadata() -> None:
    inventory = _recovery_inventory()
    repository = _RecoveryRepository(inventory, inventory, inventory)
    vector_index = _RecoveryIndex(
        RecoveryPublication(rebuilt=True, vector_count=3, orphan_vectors_removed=2),
        RecoveryPublication(rebuilt=False, vector_count=3, orphan_vectors_removed=0),
        RecoveryPublication(rebuilt=False, vector_count=3, orphan_vectors_removed=0),
    )
    sink = CaptureEventSink()
    coordinator = _observed_recovery(
        repository=repository,
        vector_index=vector_index,
        sink=sink,
    )

    rebuilt = coordinator.recover()
    first_count = len(sink.events)
    reused = coordinator.recover()

    assert rebuilt.reason == "ready_rebuilt_generation"
    assert reused.reason == "ready_existing_generation"
    assert _names(sink, after=0)[:first_count] == [
        EventName.STORAGE_COMPLETED,
        EventName.INDEXING_COMPLETED,
        EventName.STORAGE_COMPLETED,
        EventName.INDEXING_COMPLETED,
        EventName.RECOVERY_COMPLETED,
    ]
    assert _names(sink, after=first_count) == [
        EventName.STORAGE_COMPLETED,
        EventName.INDEXING_COMPLETED,
        EventName.RECOVERY_COMPLETED,
    ]
    first_terminal = sink.events[first_count - 1]
    second_terminal = sink.events[-1]
    assert first_terminal.readiness is ObservabilityReadiness.READY
    assert first_terminal.reason_code is EventReasonCode.READY_REBUILT_GENERATION
    assert first_terminal.index_vector_count == 3
    assert first_terminal.embedding_model == MODEL
    assert first_terminal.vector_dimension == 2
    assert first_terminal.rebuilt is True
    assert first_terminal.orphan_vectors_removed == 2
    assert first_terminal.pending_count == 0
    assert first_terminal.failed_count == 0
    assert first_terminal.cleanup_pending_count == 0
    assert second_terminal.rebuilt is False
    assert second_terminal.orphan_vectors_removed == 0
    assert sum(
        event.event_name is EventName.RECOVERY_COMPLETED for event in sink.events
    ) == 2


def test_recovery_emits_forgetting_adoption_and_acknowledgement_before_terminal() -> None:
    cleanup = RecoveryCleanup(
        memory_id="PRIVATE-MEMORY-ID",
        user_id=PRIVATE_USER,
        stored_vector_id=None,
        live_vector_id=41,
    )
    initial = _recovery_inventory(
        readiness=RecoveryReadiness.DEGRADED,
        reason="degraded_excluded_work_pending",
        cleanup_items=(cleanup,),
    )
    refreshed = _recovery_inventory()
    repository = _RecoveryRepository(initial, refreshed)
    vector_index = _RecoveryIndex(
        RecoveryPublication(rebuilt=False, vector_count=0, orphan_vectors_removed=1),
        RecoveryPublication(rebuilt=False, vector_count=0, orphan_vectors_removed=0),
    )
    sink = CaptureEventSink()

    result = _observed_recovery(
        repository=repository,
        vector_index=vector_index,
        sink=sink,
    ).recover()

    assert result.readiness is RecoveryReadiness.READY
    assert [event.stage for event in sink.events[:-1]] == [
        EventStage.RECOVERY_INVENTORY,
        EventStage.GENERATION_RECONCILE,
        EventStage.ADOPT_FORGETTING_VECTOR,
        EventStage.ACKNOWLEDGE_FORGETTING,
        EventStage.RECOVERY_INVENTORY,
        EventStage.GENERATION_RECONCILE,
    ]
    assert repository.adoptions == [(PRIVATE_USER, "PRIVATE-MEMORY-ID", 41)]
    assert repository.acknowledgements == [
        (PRIVATE_USER, "PRIVATE-MEMORY-ID", 41, NOW)
    ]
    serialized = b"".join(sink.lines).decode()
    assert PRIVATE_USER not in serialized
    assert KEY.decode() not in serialized
    assert not FORBIDDEN_EVENT_FIELDS.intersection(
        key for line in sink.lines for key in json.loads(line)
    )


@pytest.mark.parametrize(
    "reason",
    [
        "unavailable_sqlite_integrity",
        "unavailable_schema_or_migration",
        "unavailable_authoritative_identity",
        "unavailable_embedding_configuration",
        "unavailable_rebuild",
        "unavailable_publication",
        "unavailable_post_publication_verification",
    ],
)
def test_recovery_emits_every_approved_unavailable_reason(reason: str) -> None:
    if reason in {
        "unavailable_rebuild",
        "unavailable_publication",
        "unavailable_post_publication_verification",
    }:
        repository = _RecoveryRepository(_recovery_inventory())
        vector_index = _RecoveryIndex(failure_reason=reason)
    else:
        repository = _RecoveryRepository(
            _recovery_inventory(
                readiness=RecoveryReadiness.UNAVAILABLE,
                reason=reason,
                pending_count=2,
                failed_count=1,
            )
        )
        vector_index = _RecoveryIndex()
    sink = CaptureEventSink()

    result = _observed_recovery(
        repository=repository,
        vector_index=vector_index,
        sink=sink,
    ).recover()

    assert result.readiness is RecoveryReadiness.UNAVAILABLE
    assert result.reason == reason
    assert sink.events[-1].event_name is EventName.RECOVERY_COMPLETED
    assert sink.events[-1].outcome is EventOutcome.UNAVAILABLE
    assert sink.events[-1].reason_code.value == reason
    assert sum(
        event.event_name is EventName.RECOVERY_COMPLETED for event in sink.events
    ) == 1


def test_recovery_degraded_terminal_reports_exact_unresolved_counts() -> None:
    inventory = _recovery_inventory(
        readiness=RecoveryReadiness.DEGRADED,
        reason="degraded_excluded_work_pending",
        pending_count=4,
        failed_count=2,
    )
    sink = CaptureEventSink()

    result = _observed_recovery(
        repository=_RecoveryRepository(inventory),
        vector_index=_RecoveryIndex(
            RecoveryPublication(rebuilt=False, vector_count=5, orphan_vectors_removed=0)
        ),
        sink=sink,
    ).recover()

    assert result.readiness is RecoveryReadiness.DEGRADED
    terminal = sink.events[-1]
    assert terminal.readiness is ObservabilityReadiness.DEGRADED
    assert terminal.pending_count == 4
    assert terminal.failed_count == 2
    assert terminal.cleanup_pending_count == 0


@pytest.mark.parametrize("failure", ["sink", "timing", "pseudonym", "serialization"])
def test_recovery_observability_failures_do_not_change_readiness(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    sink: object = CaptureEventSink()
    telemetry: object = _TelemetryClock()
    pseudonymizer: object = HmacUserPseudonymizer(KEY)
    if failure == "sink":
        sink = _RaisingSink()
    elif failure == "timing":
        telemetry = _RaisingTelemetryClock()
    elif failure == "pseudonym":
        pseudonymizer = _RaisingPseudonymizer(KEY)
    else:
        def _fail_serialization(event: object) -> bytes:
            del event
            raise ValueError("PRIVATE-SERIALIZATION-FAILURE")

        monkeypatch.setattr(
            "conversational_memory.application.events.serialize_json_line",
            _fail_serialization,
        )
    inventory = _recovery_inventory()

    result = _observed_recovery(
        repository=_RecoveryRepository(inventory),
        vector_index=_RecoveryIndex(
            RecoveryPublication(rebuilt=False, vector_count=0, orphan_vectors_removed=0)
        ),
        sink=sink,
        telemetry_clock=telemetry,
        pseudonymizer=pseudonymizer,
    ).recover()

    assert result.readiness is RecoveryReadiness.READY
    assert result.reason == "ready_existing_generation"


def _compose_observed_startup(
    tmp_path: Path,
    *,
    repository: SQLiteMemoryRepository,
    sink: object,
    telemetry_clock: object | None = None,
    hmac_key: object = KEY,
):
    return compose_recovered_memory_service(
        repository=repository,
        index_directory=tmp_path / "index",
        embedding_model=MODEL,
        vector_dimension=2,
        embedder=_Embedder(),
        token_counter=_TokenCounter(),
        clock=_LifecycleClock(),
        memory_ids=_MemoryIds(),
        relevance_threshold=0.50,
        event_sink=sink,  # type: ignore[arg-type]
        telemetry_clock=telemetry_clock or _TelemetryClock(),  # type: ignore[arg-type]
        user_hmac_key=hmac_key,
    )


def test_observed_startup_orders_rebuilt_recovery_before_one_ready_terminal(
    tmp_path: Path,
) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    sink = CaptureEventSink()

    runtime = _compose_observed_startup(
        tmp_path,
        repository=repository,
        sink=sink,
    )

    assert runtime.recovery.readiness is RecoveryReadiness.READY
    assert runtime.recovery.rebuilt is True
    assert sink.events[-2].event_name is EventName.RECOVERY_COMPLETED
    assert sink.events[-1].event_name is EventName.STARTUP_READY
    assert sink.events[-1].reason_code is EventReasonCode.READY_REBUILT_GENERATION
    assert sink.events[-1].readiness is ObservabilityReadiness.READY
    assert sum(
        event.event_name
        in {
            EventName.STARTUP_READY,
            EventName.STARTUP_DEGRADED,
            EventName.STARTUP_UNAVAILABLE,
        }
        for event in sink.events
    ) == 1

    operation_start = len(sink.events)
    result = runtime.service.admit(
        RequestContext(user_id=PRIVATE_USER, request_id="PRIVATE-STARTUP-REQUEST"),
        _request(content="PRIVATE-STARTUP-CONTENT"),
    )
    assert result.memory_id is not None
    assert sink.events[operation_start].user_ref == HmacUserPseudonymizer(KEY).pseudonymize(
        PRIVATE_USER
    )
    serialized = b"".join(sink.lines).decode()
    assert PRIVATE_USER not in serialized
    assert KEY.decode() not in serialized
    assert "PRIVATE-STARTUP-CONTENT" not in serialized
    assert not FORBIDDEN_EVENT_FIELDS.intersection(
        key for line in sink.lines for key in json.loads(line)
    )


def test_observed_second_startup_emits_ready_existing_generation(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.sqlite3")
    _compose_observed_startup(
        tmp_path,
        repository=repository,
        sink=CaptureEventSink(),
    )
    sink = CaptureEventSink()

    runtime = _compose_observed_startup(
        tmp_path,
        repository=SQLiteMemoryRepository(tmp_path / "memory.sqlite3"),
        sink=sink,
    )

    assert runtime.recovery.rebuilt is False
    assert _names(sink)[-2:] == [
        EventName.RECOVERY_COMPLETED,
        EventName.STARTUP_READY,
    ]
    assert sink.events[-1].reason_code is EventReasonCode.READY_EXISTING_GENERATION


def test_observed_degraded_startup_exposes_service_then_one_terminal(
    tmp_path: Path,
) -> None:
    service, repository, _, _ = _service(tmp_path, sink=CaptureEventSink())
    seeded = service.admit(
        RequestContext(user_id=PRIVATE_USER, request_id="seed"),
        _request(),
    )
    assert seeded.memory_id is not None
    with sqlite3.connect(tmp_path / "memory.sqlite3") as connection:
        connection.execute(
            "UPDATE memories SET indexing_state = 'pending' WHERE memory_id = ?",
            (seeded.memory_id,),
        )
        connection.commit()
    sink = CaptureEventSink()

    runtime = _compose_observed_startup(
        tmp_path,
        repository=repository,
        sink=sink,
    )

    assert runtime.recovery.readiness is RecoveryReadiness.DEGRADED
    assert runtime.recovery.pending_count == 1
    assert _names(sink)[-2:] == [
        EventName.RECOVERY_COMPLETED,
        EventName.STARTUP_DEGRADED,
    ]
    assert sink.events[-1].reason_code is EventReasonCode.DEGRADED_EXCLUDED_WORK_PENDING


def test_observed_unavailable_startup_emits_terminal_and_refuses_service(
    tmp_path: Path,
) -> None:
    service, repository, _, _ = _service(tmp_path, sink=CaptureEventSink())
    seeded = service.admit(
        RequestContext(user_id=PRIVATE_USER, request_id="seed"),
        _request(),
    )
    assert seeded.memory_id is not None
    with sqlite3.connect(tmp_path / "memory.sqlite3") as connection:
        connection.execute(
            "DELETE FROM memory_vector_mappings WHERE memory_id = ?",
            (seeded.memory_id,),
        )
        connection.commit()
    sink = CaptureEventSink()

    with pytest.raises(
        ServiceUnavailableError, match="unavailable_authoritative_identity"
    ):
        _compose_observed_startup(tmp_path, repository=repository, sink=sink)

    assert _names(sink)[-2:] == [
        EventName.RECOVERY_COMPLETED,
        EventName.STARTUP_UNAVAILABLE,
    ]
    assert sink.events[-1].reason_code is EventReasonCode.UNAVAILABLE_AUTHORITATIVE_IDENTITY
    assert sum(
        event.event_name is EventName.STARTUP_UNAVAILABLE for event in sink.events
    ) == 1


def test_invalid_observability_emits_configuration_then_unavailable_startup(
    tmp_path: Path,
) -> None:
    sink = CaptureEventSink()

    with pytest.raises(ConfigurationError, match="invalid_observability_configuration"):
        _compose_observed_startup(
            tmp_path,
            repository=SQLiteMemoryRepository(tmp_path / "memory.sqlite3"),
            sink=sink,
            hmac_key=b"too-short",
        )

    assert _names(sink) == [
        EventName.CONFIGURATION_FAILED,
        EventName.STARTUP_UNAVAILABLE,
    ]
    assert sink.events[-1].reason_code is EventReasonCode.CONFIGURATION_MISMATCH
    assert not (tmp_path / "index").exists()


@pytest.mark.parametrize("failure", ["sink", "timing", "serialization"])
def test_startup_observability_failure_does_not_change_ready_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    sink: object = CaptureEventSink()
    telemetry: object = _TelemetryClock()
    if failure == "sink":
        sink = _RaisingSink()
    elif failure == "timing":
        telemetry = _RaisingTelemetryClock()
    else:
        def _fail_serialization(event: object) -> bytes:
            del event
            raise ValueError("PRIVATE-STARTUP-SERIALIZATION-FAILURE")

        monkeypatch.setattr(
            "conversational_memory.application.events.serialize_json_line",
            _fail_serialization,
        )

    runtime = _compose_observed_startup(
        tmp_path,
        repository=SQLiteMemoryRepository(tmp_path / "memory.sqlite3"),
        sink=sink,
        telemetry_clock=telemetry,
    )

    assert runtime.recovery.readiness is RecoveryReadiness.READY
    assert runtime.service is not None


def test_local_json_line_sink_and_system_telemetry_clock_are_bounded() -> None:
    stream = BytesIO()
    sink = JsonLineEventSink(stream)
    clock = SystemTelemetryClock()
    event = MemoryEvent(
        schema_version=1,
        event_name=EventName.CONFIGURATION_FAILED,
        occurred_at=NOW,
        duration_ms=0,
        outcome=EventOutcome.FAILED,
        reason_code=EventReasonCode.CONFIGURATION_MISMATCH,
    )

    sink.emit(event)

    assert stream.getvalue() == serialize_json_line(event)
    assert clock.utc_now().tzinfo is UTC
    assert type(clock.monotonic_ns()) is int


class _DemoObservedEmbedder:
    def embed(self, content: str) -> Embedding:
        values = [0.0] * 768
        values[0] = 1.0
        return Embedding(
            values=tuple(values),
            model_id=(
                "sentence-transformers/all-mpnet-base-v2@"
                "e8c3b32edf5434bc2275fc9bab85f82640a19130"
            ),
            dimension=768,
        )


def test_cli_exposes_and_runs_privacy_safe_observability_demo(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        composition,
        "SentenceTransformerEmbedder",
        lambda *, cache_directory: _DemoObservedEmbedder(),
    )
    monkeypatch.setattr(composition, "TiktokenTokenCounter", lambda: _TokenCounter())

    assert build_parser().parse_args(["demo-observability"]).command == (
        "demo-observability"
    )
    assert main(["demo-observability"]) == 0

    output = capsys.readouterr().out
    events = [json.loads(line) for line in output.splitlines()]
    names = [event["event_name"] for event in events]
    startup_names = {
        "startup_ready",
        "startup_degraded",
        "startup_unavailable",
    }
    assert events
    assert "admission_completed" in names
    assert "retrieval_completed" in names
    assert "forgetting_completed" in names
    assert "recovery_completed" in names
    assert "startup_ready" in names
    assert "startup_degraded" in names
    assert "startup_unavailable" in names
    assert any(
        event["reason_code"] == "sensitive_admission_rejected" for event in events
    )
    assert names.count("admission_completed") == 4
    assert names.count("retrieval_completed") == 1
    assert names.count("forgetting_completed") == 1
    assert names.count("recovery_completed") == 3
    assert sum(name in startup_names for name in names) == 3
    for index, name in enumerate(names):
        if name in startup_names:
            assert names[index - 1] == "recovery_completed"
    assert not FORBIDDEN_EVENT_FIELDS.intersection(
        key for event in events for key in event
    )
    allowed_keys = {
        "schema_version",
        "event_name",
        "occurred_at",
        "duration_ms",
        "outcome",
        "reason_code",
        "request_id",
        "user_ref",
        "memory_id",
        "memory_ids",
        "stage",
        "retry_count",
        "candidate_count",
        "returned_count",
        "token_budget",
        "tokens_used",
        "index_vector_count",
        "embedding_model",
        "vector_dimension",
        "readiness",
        "rebuilt",
        "orphan_vectors_removed",
        "pending_count",
        "failed_count",
        "cleanup_pending_count",
    }
    assert all(set(event) <= allowed_keys for event in events)
    for sentinel in (
        "M9-PRIVATE-USER",
        "M9-PRIVATE-MEMORY-CONTENT",
        "M9-PRIVATE-QUERY",
        "M9-PRIVATE-CREDENTIAL",
        "M9-PRIVATE-AUTH",
        "m9-demo-only-hmac-key-material-2026",
    ):
        assert sentinel not in output
