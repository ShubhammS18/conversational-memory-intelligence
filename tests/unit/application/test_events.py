from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from conversational_memory.application.events import (
    FORBIDDEN_EVENT_FIELDS,
    CaptureEventSink,
    EventName,
    EventOutcome,
    EventReasonCode,
    EventStage,
    HmacUserPseudonymizer,
    MemoryEvent,
    ObservabilityReadiness,
    privacy_safe_reason_code,
    serialize_json_line,
)
from conversational_memory.application.ports import EventSinkPort, TelemetryClockPort

NOW = datetime(2026, 9, 10, 12, 34, 56, 123456, tzinfo=UTC)
USER_ID = "private-user-id"
HMAC_KEY = b"0123456789abcdef0123456789abcdef"
USER_REF = HmacUserPseudonymizer(HMAC_KEY).pseudonymize(USER_ID)


def test_trusted_request_id_is_serialized_exactly_with_surrounding_whitespace() -> None:
    event = MemoryEvent(schema_version=1, event_name=EventName.CONFIGURATION_FAILED,
        occurred_at=NOW, duration_ms=0, outcome=EventOutcome.FAILED,
        reason_code=EventReasonCode.CONFIGURATION_MISMATCH,
        request_id=" \ttrusted request\n ", user_ref=USER_REF)
    assert json.loads(serialize_json_line(event))["request_id"] == event.request_id


@pytest.mark.parametrize("event_name,reason", [(EventName.STORAGE_COMPLETED, EventReasonCode.OPERATION_COMPLETED), (EventName.STORAGE_FAILED, EventReasonCode.STORAGE_FAILURE)])
def test_mark_failed_is_a_strict_user_scoped_storage_stage(event_name: EventName, reason: EventReasonCode) -> None:
    event = MemoryEvent(schema_version=1, event_name=event_name,
        occurred_at=NOW, duration_ms=0,
        outcome=EventOutcome.SUCCEEDED if event_name is EventName.STORAGE_COMPLETED else EventOutcome.FAILED,
        reason_code=reason, request_id="request", user_ref=USER_REF,
        stage=EventStage.MARK_FAILED, memory_id="authoritative-id")
    assert json.loads(serialize_json_line(event))["stage"] == "mark_failed"


@pytest.mark.parametrize("field,value", [("duration_ms", True), ("event_name", "configuration_failed"), ("occurred_at", NOW.replace(tzinfo=None)), ("reason_code", EventReasonCode.OPERATION_FAILED)])
def test_serialization_revalidates_every_field_after_frozen_instance_bypass(field: str, value: object) -> None:
    event = MemoryEvent(schema_version=1, event_name=EventName.CONFIGURATION_FAILED,
        occurred_at=NOW, duration_ms=0, outcome=EventOutcome.FAILED,
        reason_code=EventReasonCode.CONFIGURATION_MISMATCH)
    object.__setattr__(event, field, value)
    with pytest.raises(PydanticValidationError):
        serialize_json_line(event)


def _base(
    event_name: EventName,
    outcome: EventOutcome,
    reason_code: EventReasonCode,
    **fields: object,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_name": event_name,
        "occurred_at": NOW,
        "duration_ms": 7,
        "outcome": outcome,
        "reason_code": reason_code,
        **fields,
    }


def _event_values(event_name: EventName) -> dict[str, object]:
    user_fields = {"request_id": "request-1", "user_ref": USER_REF}
    recovery_fields = {
        "readiness": ObservabilityReadiness.READY,
        "index_vector_count": 2,
        "embedding_model": "model@revision",
        "vector_dimension": 768,
        "rebuilt": False,
        "orphan_vectors_removed": 0,
        "pending_count": 0,
        "failed_count": 0,
        "cleanup_pending_count": 0,
    }
    if event_name is EventName.ADMISSION_COMPLETED:
        return _base(
            event_name,
            EventOutcome.ACCEPTED,
            EventReasonCode.ACCEPTED_AND_INDEXED,
            **user_fields,
            memory_id="memory-1",
            retry_count=0,
        )
    if event_name is EventName.RETRIEVAL_COMPLETED:
        return _base(
            event_name,
            EventOutcome.MEMORIES_SELECTED,
            EventReasonCode.MEMORIES_SELECTED,
            **user_fields,
            memory_ids=("memory-1",),
            candidate_count=2,
            returned_count=1,
            token_budget=128,
            tokens_used=34,
        )
    if event_name is EventName.FORGETTING_COMPLETED:
        return _base(
            event_name,
            EventOutcome.FORGOTTEN,
            EventReasonCode.FORGOTTEN,
            **user_fields,
            memory_id="memory-1",
        )
    if event_name is EventName.RECOVERY_COMPLETED:
        return _base(
            event_name,
            EventOutcome.READY,
            EventReasonCode.READY_EXISTING_GENERATION,
            **recovery_fields,
        )
    if event_name is EventName.STARTUP_READY:
        return _base(
            event_name,
            EventOutcome.READY,
            EventReasonCode.READY_EXISTING_GENERATION,
            **recovery_fields,
        )
    if event_name is EventName.STARTUP_DEGRADED:
        return _base(
            event_name,
            EventOutcome.DEGRADED,
            EventReasonCode.DEGRADED_EXCLUDED_WORK_PENDING,
            **{**recovery_fields, "readiness": ObservabilityReadiness.DEGRADED},
        )
    if event_name is EventName.STARTUP_UNAVAILABLE:
        return _base(
            event_name,
            EventOutcome.UNAVAILABLE,
            EventReasonCode.UNAVAILABLE_SQLITE_INTEGRITY,
            **{**recovery_fields, "readiness": ObservabilityReadiness.UNAVAILABLE},
        )
    if event_name is EventName.STORAGE_COMPLETED:
        return _base(
            event_name,
            EventOutcome.SUCCEEDED,
            EventReasonCode.OPERATION_COMPLETED,
            **user_fields,
            memory_id="memory-1",
            stage=EventStage.PERSIST_PENDING,
        )
    if event_name is EventName.STORAGE_FAILED:
        return _base(
            event_name,
            EventOutcome.FAILED,
            EventReasonCode.STORAGE_FAILURE,
            **user_fields,
            memory_id="memory-1",
            stage=EventStage.MARK_INDEXED,
        )
    if event_name is EventName.INDEXING_COMPLETED:
        return _base(
            event_name,
            EventOutcome.SUCCEEDED,
            EventReasonCode.OPERATION_COMPLETED,
            **user_fields,
            memory_id="memory-1",
            stage=EventStage.VECTOR_ADD,
            embedding_model="model@revision",
            vector_dimension=768,
        )
    if event_name is EventName.INDEXING_FAILED:
        return _base(
            event_name,
            EventOutcome.FAILED,
            EventReasonCode.INDEXING_FAILED,
            **user_fields,
            memory_id="memory-1",
            stage=EventStage.VECTOR_ADD,
        )
    if event_name is EventName.ADMISSION_RETRY:
        return _base(
            event_name,
            EventOutcome.RETRY_STARTED,
            EventReasonCode.RETRY_STARTED,
            **user_fields,
            memory_id="memory-1",
            retry_count=1,
        )
    return _base(
        event_name,
        EventOutcome.FAILED,
        EventReasonCode.CONFIGURATION_MISMATCH,
    )


@pytest.mark.parametrize("event_name", list(EventName))
def test_every_approved_event_kind_has_one_valid_strict_shape(
    event_name: EventName,
) -> None:
    event = MemoryEvent(**_event_values(event_name))  # type: ignore[arg-type]

    assert event.event_name is event_name
    assert event.schema_version == 1
    with pytest.raises(FrozenInstanceError):
        event.duration_ms = 9  # type: ignore[misc]


@pytest.mark.parametrize(
    ("event_name", "required_field"),
    [
        (EventName.ADMISSION_COMPLETED, "request_id"),
        (EventName.RETRIEVAL_COMPLETED, "candidate_count"),
        (EventName.FORGETTING_COMPLETED, "user_ref"),
        (EventName.RECOVERY_COMPLETED, "index_vector_count"),
        (EventName.STARTUP_READY, "readiness"),
        (EventName.STARTUP_DEGRADED, "pending_count"),
        (EventName.STARTUP_UNAVAILABLE, "embedding_model"),
        (EventName.STORAGE_COMPLETED, "stage"),
        (EventName.STORAGE_FAILED, "stage"),
        (EventName.INDEXING_COMPLETED, "stage"),
        (EventName.INDEXING_FAILED, "stage"),
        (EventName.ADMISSION_RETRY, "retry_count"),
    ],
)
def test_each_profile_rejects_a_missing_required_field(
    event_name: EventName,
    required_field: str,
) -> None:
    values = _event_values(event_name)
    del values[required_field]

    with pytest.raises(PydanticValidationError):
        MemoryEvent(**values)  # type: ignore[arg-type]


def test_configuration_event_accepts_only_a_complete_optional_user_scope() -> None:
    values = _event_values(EventName.CONFIGURATION_FAILED)
    MemoryEvent(**values)  # type: ignore[arg-type]

    values["request_id"] = "request-1"
    with pytest.raises(PydanticValidationError):
        MemoryEvent(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("forbidden_field", sorted(FORBIDDEN_EVENT_FIELDS))
def test_forbidden_fields_are_rejected_at_construction(forbidden_field: str) -> None:
    values = _event_values(EventName.ADMISSION_COMPLETED)
    values[forbidden_field] = "PRIVATE-SENTINEL"

    with pytest.raises(PydanticValidationError):
        MemoryEvent(**values)  # type: ignore[arg-type]


def test_unknown_fields_and_wrong_types_are_rejected_without_coercion() -> None:
    values = _event_values(EventName.ADMISSION_COMPLETED)
    with pytest.raises(PydanticValidationError):
        MemoryEvent(**values, arbitrary_metadata={"content": "secret"})  # type: ignore[arg-type]

    for field, invalid in (
        ("schema_version", True),
        ("event_name", "admission_completed"),
        ("occurred_at", NOW.isoformat()),
        ("duration_ms", True),
        ("retry_count", "0"),
    ):
        invalid_values = {**values, field: invalid}
        with pytest.raises(PydanticValidationError):
            MemoryEvent(**invalid_values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "occurred_at",
    [
        datetime(2026, 9, 10, 12),  # noqa: DTZ001 - deliberately invalid
        NOW.astimezone(timezone(timedelta(hours=1))),
    ],
)
def test_event_time_must_be_aware_utc(occurred_at: datetime) -> None:
    values = {**_event_values(EventName.ADMISSION_COMPLETED), "occurred_at": occurred_at}
    with pytest.raises(PydanticValidationError):
        MemoryEvent(**values)  # type: ignore[arg-type]


def test_profile_rejects_known_but_inapplicable_fields_and_outcomes() -> None:
    values = _event_values(EventName.ADMISSION_COMPLETED)
    values["token_budget"] = 100
    with pytest.raises(PydanticValidationError):
        MemoryEvent(**values)  # type: ignore[arg-type]

    values = _event_values(EventName.RETRIEVAL_COMPLETED)
    values["outcome"] = EventOutcome.ACCEPTED
    with pytest.raises(PydanticValidationError):
        MemoryEvent(**values)  # type: ignore[arg-type]


def test_forgetting_not_found_is_opaque_and_other_outcomes_require_memory_id() -> None:
    values = _event_values(EventName.FORGETTING_COMPLETED)
    values.update(
        outcome=EventOutcome.NOT_FOUND,
        reason_code=EventReasonCode.MEMORY_NOT_FOUND,
    )
    values.pop("memory_id")
    MemoryEvent(**values)  # type: ignore[arg-type]

    values["memory_id"] = "guessed-cross-owner-id"
    with pytest.raises(PydanticValidationError):
        MemoryEvent(**values)  # type: ignore[arg-type]


def test_memory_identifiers_are_exact_ordered_and_unique() -> None:
    values = _event_values(EventName.RETRIEVAL_COMPLETED)
    event = MemoryEvent(**values)  # type: ignore[arg-type]
    assert event.memory_ids == ("memory-1",)

    for invalid in (("memory-1", "memory-1"), (" memory-1",), ["memory-1"]):
        values["memory_ids"] = invalid
        with pytest.raises(PydanticValidationError):
            MemoryEvent(**values)  # type: ignore[arg-type]


def test_hmac_pseudonyms_are_stable_domain_separated_and_key_safe() -> None:
    pseudonymizer = HmacUserPseudonymizer(HMAC_KEY)
    same = HmacUserPseudonymizer(HMAC_KEY)
    other_key = HmacUserPseudonymizer(b"fedcba9876543210fedcba9876543210")

    first = pseudonymizer.pseudonymize(USER_ID)
    assert first == same.pseudonymize(USER_ID)
    assert first != pseudonymizer.pseudonymize("other-user")
    assert first != other_key.pseudonymize(USER_ID)
    assert first.startswith("u_") and len(first) == 66
    assert HMAC_KEY.decode() not in repr(pseudonymizer)


@pytest.mark.parametrize("invalid_key", [b"short", bytearray(HMAC_KEY), "not-bytes"])
def test_hmac_pseudonymizer_rejects_invalid_keys(invalid_key: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        HmacUserPseudonymizer(invalid_key)  # type: ignore[arg-type]


def test_sensitive_and_unknown_reasons_collapse_without_leaking_text() -> None:
    fallback = EventReasonCode.OPERATION_FAILED
    assert (
        privacy_safe_reason_code("sensitive_credential", fallback=fallback)
        is EventReasonCode.SENSITIVE_ADMISSION_REJECTED
    )
    assert (
        privacy_safe_reason_code("sensitive_check_unavailable", fallback=fallback)
        is EventReasonCode.SENSITIVE_ADMISSION_REJECTED
    )
    assert (
        privacy_safe_reason_code("password=PRIVATE-CREDENTIAL", fallback=fallback)
        is fallback
    )


def test_serialization_is_deterministic_canonical_utf8_json_lines() -> None:
    event = MemoryEvent(**_event_values(EventName.RETRIEVAL_COMPLETED))  # type: ignore[arg-type]

    first = serialize_json_line(event)
    second = serialize_json_line(event)
    payload = json.loads(first)

    assert first == second
    assert first.endswith(b"\n") and first.count(b"\n") == 1
    assert b'"duration_ms":7,"event_name":"retrieval_completed"' in first
    assert payload["occurred_at"] == "2026-09-10T12:34:56.123456Z"
    assert payload["memory_ids"] == ["memory-1"]
    assert list(payload) == sorted(payload)


def test_capture_sink_keeps_immutable_events_and_exact_serialized_lines() -> None:
    event = MemoryEvent(**_event_values(EventName.ADMISSION_COMPLETED))  # type: ignore[arg-type]
    sink = CaptureEventSink()

    sink.emit(event)

    assert isinstance(sink, EventSinkPort)
    assert sink.events == (event,)
    assert sink.lines == (serialize_json_line(event),)


def test_protocols_describe_injected_sink_and_separate_telemetry_clock() -> None:
    assert EventSinkPort.emit.__annotations__["event"] == "MemoryEvent"
    assert TelemetryClockPort.utc_now.__annotations__["return"] == "datetime"
    assert TelemetryClockPort.monotonic_ns.__annotations__["return"] == "int"


def test_serialized_events_exclude_all_private_sentinels_and_forbidden_keys() -> None:
    private_values = {
        USER_ID,
        HMAC_KEY.decode(),
        "PRIVATE-CONTENT",
        "PRIVATE-QUERY",
        "PRIVATE-CREDENTIAL",
        "PRIVATE-EXCEPTION-TEXT",
    }
    event = MemoryEvent(**_event_values(EventName.ADMISSION_COMPLETED))  # type: ignore[arg-type]
    serialized = serialize_json_line(event).decode("utf-8")
    payload: dict[str, Any] = json.loads(serialized)

    assert not FORBIDDEN_EVENT_FIELDS.intersection(payload)
    for private_value in private_values:
        assert private_value not in serialized


def test_strict_event_rejects_replacement_with_invalid_fields() -> None:
    event = MemoryEvent(**_event_values(EventName.ADMISSION_COMPLETED))  # type: ignore[arg-type]

    with pytest.raises(PydanticValidationError):
        replace(event, request_id=" ")
