"""Strict privacy-safe event contracts and local serialization primitives."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from enum import StrEnum
from functools import wraps
from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

_EVENT_CONFIG = ConfigDict(strict=True, extra="forbid")
StrictText = Annotated[str, Field(strict=True)]
NonNegativeStrictInt = Annotated[int, Field(strict=True, ge=0)]
PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]
StrictBoolean = Annotated[bool, Field(strict=True)]
MemoryIds = Annotated[tuple[StrictText, ...], Field(strict=True)]
_USER_REF = re.compile(r"u_[0-9a-f]{64}\Z")
_USER_HMAC_DOMAIN = b"cmi-observability-user-v1\\0"


def isolated_event[**EventArgs](operation: Callable[EventArgs, None]) -> Callable[EventArgs, None]:
    """Isolate the complete conversion/delivery boundary, not primary work."""
    @wraps(operation)
    def emit(*args: EventArgs.args, **kwargs: EventArgs.kwargs) -> None:
        try:
            operation(*args, **kwargs)
        except Exception:  # noqa: BLE001 - no fallback or recursive logging
            return
    return emit


class EventName(StrEnum):
    """Closed M9 event vocabulary."""

    ADMISSION_COMPLETED = "admission_completed"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    FORGETTING_COMPLETED = "forgetting_completed"
    RECOVERY_COMPLETED = "recovery_completed"
    STARTUP_READY = "startup_ready"
    STARTUP_DEGRADED = "startup_degraded"
    STARTUP_UNAVAILABLE = "startup_unavailable"
    STORAGE_COMPLETED = "storage_completed"
    STORAGE_FAILED = "storage_failed"
    INDEXING_COMPLETED = "indexing_completed"
    INDEXING_FAILED = "indexing_failed"
    ADMISSION_RETRY = "admission_retry"
    CONFIGURATION_FAILED = "configuration_failed"


class EventOutcome(StrEnum):
    """Closed high-level event outcomes."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    MEMORIES_SELECTED = "memories_selected"
    NO_ELIGIBLE_MEMORY = "no_eligible_memory"
    NO_RELEVANT_MEMORY = "no_relevant_memory"
    BUDGET_EXCLUDED = "budget_excluded"
    FORGOTTEN = "forgotten"
    CLEANUP_PENDING = "cleanup_pending"
    NOT_FOUND = "not_found"
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    SUCCEEDED = "succeeded"
    RETRY_STARTED = "retry_started"


class EventReasonCode(StrEnum):
    """Closed privacy-reviewed event reasons."""

    ACCEPTED_AND_INDEXED = "accepted_and_indexed"
    SENSITIVE_ADMISSION_REJECTED = "sensitive_admission_rejected"
    INVALID_ADMISSION_REQUEST = "invalid_admission_request"
    IDEMPOTENCY_KEY_CONFLICT = "idempotency_key_conflict"
    INVALID_SUPERSESSION_TARGET = "invalid_supersession_target"
    INDEXING_FAILED = "indexing_failed"
    INDEXING_ACKNOWLEDGEMENT_FAILED = "indexing_acknowledgement_failed"
    SUPERSESSION_ACKNOWLEDGEMENT_FAILED = "supersession_acknowledgement_failed"
    STORAGE_FAILURE = "storage_failure"
    CONFIGURATION_MISMATCH = "configuration_mismatch"
    MEMORIES_SELECTED = "memories_selected"
    NO_ELIGIBLE_MEMORY = "no_eligible_memory"
    NO_RELEVANT_MEMORY = "no_relevant_memory"
    BUDGET_EXCLUDED = "budget_excluded"
    MEMORY_NOT_FOUND = "memory_not_found"
    FORGOTTEN = "forgotten"
    ALREADY_FORGOTTEN = "already_forgotten"
    PHYSICAL_CLEANUP_PENDING = "physical_cleanup_pending"
    PHYSICAL_CLEANUP_IDENTITY_PENDING = "physical_cleanup_identity_pending"
    OPERATION_COMPLETED = "operation_completed"
    OPERATION_FAILED = "operation_failed"
    RETRY_STARTED = "retry_started"
    READY_EXISTING_GENERATION = "ready_existing_generation"
    READY_REBUILT_GENERATION = "ready_rebuilt_generation"
    DEGRADED_EXCLUDED_WORK_PENDING = "degraded_excluded_work_pending"
    UNAVAILABLE_SQLITE_INTEGRITY = "unavailable_sqlite_integrity"
    UNAVAILABLE_SCHEMA_OR_MIGRATION = "unavailable_schema_or_migration"
    UNAVAILABLE_AUTHORITATIVE_IDENTITY = "unavailable_authoritative_identity"
    UNAVAILABLE_EMBEDDING_CONFIGURATION = "unavailable_embedding_configuration"
    UNAVAILABLE_REBUILD = "unavailable_rebuild"
    UNAVAILABLE_PUBLICATION = "unavailable_publication"
    UNAVAILABLE_POST_PUBLICATION_VERIFICATION = (
        "unavailable_post_publication_verification"
    )


class EventStage(StrEnum):
    """Closed internal operation-stage vocabulary."""

    IDEMPOTENCY_LOOKUP = "idempotency_lookup"
    SUPERSESSION_LOOKUP = "supersession_lookup"
    PERSIST_PENDING = "persist_pending"
    MARK_PENDING = "mark_pending"
    MARK_FAILED = "mark_failed"
    MARK_INDEXED = "mark_indexed"
    ACKNOWLEDGE_SUPERSESSION = "acknowledge_supersession"
    EXPIRATION_TRANSITION = "expiration_transition"
    CURRENT_ALLOWLIST = "current_allowlist"
    HISTORICAL_ALLOWLIST = "historical_allowlist"
    CURRENT_HYDRATION = "current_hydration"
    HISTORICAL_HYDRATION = "historical_hydration"
    FORGETTING_LOOKUP = "forgetting_lookup"
    BEGIN_FORGETTING = "begin_forgetting"
    ACKNOWLEDGE_FORGETTING = "acknowledge_forgetting"
    RECOVERY_INVENTORY = "recovery_inventory"
    ADOPT_FORGETTING_VECTOR = "adopt_forgetting_vector"
    EMBEDDING = "embedding"
    VECTOR_ADD = "vector_add"
    VECTOR_SEARCH = "vector_search"
    VECTOR_REMOVE = "vector_remove"
    GENERATION_RECONCILE = "generation_reconcile"


class ObservabilityReadiness(StrEnum):
    """Readiness values allowed in observability events."""

    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


FORBIDDEN_EVENT_FIELDS = frozenset(
    {
        "user_id",
        "conversation_id",
        "turn_id",
        "idempotency_key",
        "request_fingerprint",
        "source_event_at",
        "content",
        "query",
        "subject",
        "value",
        "embedding",
        "embedding_values",
        "authorization",
        "authentication",
        "authorization_header",
        "cookie",
        "password",
        "passcode",
        "api_key",
        "secret_key",
        "access_token",
        "refresh_token",
        "retryable_error",
        "exception",
        "traceback",
        "stack_trace",
        "message",
    }
)

_USER_SCOPE = frozenset({"request_id", "user_ref"})
_MEMORY = frozenset({"memory_id"})
_RECOVERY = frozenset(
    {
        "readiness",
        "index_vector_count",
        "embedding_model",
        "vector_dimension",
        "rebuilt",
        "orphan_vectors_removed",
        "pending_count",
        "failed_count",
        "cleanup_pending_count",
    }
)
_PROFILE_ALLOWED: dict[EventName, frozenset[str]] = {
    EventName.ADMISSION_COMPLETED: _USER_SCOPE | _MEMORY | {"retry_count"},
    EventName.RETRIEVAL_COMPLETED: _USER_SCOPE
    | {
        "memory_ids",
        "candidate_count",
        "returned_count",
        "token_budget",
        "tokens_used",
    },
    EventName.FORGETTING_COMPLETED: _USER_SCOPE | _MEMORY,
    EventName.RECOVERY_COMPLETED: _RECOVERY,
    EventName.STARTUP_READY: _RECOVERY,
    EventName.STARTUP_DEGRADED: _RECOVERY,
    EventName.STARTUP_UNAVAILABLE: _RECOVERY,
    EventName.STORAGE_COMPLETED: _USER_SCOPE | _MEMORY | {"stage"},
    EventName.STORAGE_FAILED: _USER_SCOPE | _MEMORY | {"stage"},
    EventName.INDEXING_COMPLETED: _USER_SCOPE
    | _MEMORY
    | {"stage", "index_vector_count", "embedding_model", "vector_dimension"},
    EventName.INDEXING_FAILED: _USER_SCOPE
    | _MEMORY
    | {"stage", "index_vector_count", "embedding_model", "vector_dimension"},
    EventName.ADMISSION_RETRY: _USER_SCOPE | _MEMORY | {"retry_count"},
    EventName.CONFIGURATION_FAILED: _USER_SCOPE,
}
_PROFILE_REQUIRED: dict[EventName, frozenset[str]] = {
    EventName.ADMISSION_COMPLETED: _USER_SCOPE | {"retry_count"},
    EventName.RETRIEVAL_COMPLETED: _USER_SCOPE
    | {
        "memory_ids",
        "candidate_count",
        "returned_count",
        "token_budget",
        "tokens_used",
    },
    EventName.FORGETTING_COMPLETED: _USER_SCOPE,
    EventName.RECOVERY_COMPLETED: _RECOVERY,
    EventName.STARTUP_READY: _RECOVERY,
    EventName.STARTUP_DEGRADED: _RECOVERY,
    EventName.STARTUP_UNAVAILABLE: _RECOVERY,
    EventName.STORAGE_COMPLETED: frozenset({"stage"}),
    EventName.STORAGE_FAILED: frozenset({"stage"}),
    EventName.INDEXING_COMPLETED: frozenset({"stage"}),
    EventName.INDEXING_FAILED: frozenset({"stage"}),
    EventName.ADMISSION_RETRY: _USER_SCOPE | _MEMORY | {"retry_count"},
    EventName.CONFIGURATION_FAILED: frozenset(),
}
_PROFILE_OUTCOMES: dict[EventName, frozenset[EventOutcome]] = {
    EventName.ADMISSION_COMPLETED: frozenset(
        {EventOutcome.ACCEPTED, EventOutcome.REJECTED, EventOutcome.FAILED}
    ),
    EventName.RETRIEVAL_COMPLETED: frozenset(
        {
            EventOutcome.MEMORIES_SELECTED,
            EventOutcome.NO_ELIGIBLE_MEMORY,
            EventOutcome.NO_RELEVANT_MEMORY,
            EventOutcome.BUDGET_EXCLUDED,
            EventOutcome.FAILED,
        }
    ),
    EventName.FORGETTING_COMPLETED: frozenset(
        {
            EventOutcome.FORGOTTEN,
            EventOutcome.CLEANUP_PENDING,
            EventOutcome.NOT_FOUND,
            EventOutcome.FAILED,
        }
    ),
    EventName.RECOVERY_COMPLETED: frozenset(
        {EventOutcome.READY, EventOutcome.DEGRADED, EventOutcome.UNAVAILABLE}
    ),
    EventName.STARTUP_READY: frozenset({EventOutcome.READY}),
    EventName.STARTUP_DEGRADED: frozenset({EventOutcome.DEGRADED}),
    EventName.STARTUP_UNAVAILABLE: frozenset({EventOutcome.UNAVAILABLE}),
    EventName.STORAGE_COMPLETED: frozenset({EventOutcome.SUCCEEDED}),
    EventName.STORAGE_FAILED: frozenset({EventOutcome.FAILED}),
    EventName.INDEXING_COMPLETED: frozenset({EventOutcome.SUCCEEDED}),
    EventName.INDEXING_FAILED: frozenset({EventOutcome.FAILED}),
    EventName.ADMISSION_RETRY: frozenset({EventOutcome.RETRY_STARTED}),
    EventName.CONFIGURATION_FAILED: frozenset({EventOutcome.FAILED}),
}
_PROFILE_REASONS: dict[EventName, frozenset[EventReasonCode]] = {
    EventName.ADMISSION_COMPLETED: frozenset(
        {
            EventReasonCode.ACCEPTED_AND_INDEXED,
            EventReasonCode.SENSITIVE_ADMISSION_REJECTED,
            EventReasonCode.INVALID_ADMISSION_REQUEST,
            EventReasonCode.IDEMPOTENCY_KEY_CONFLICT,
            EventReasonCode.INVALID_SUPERSESSION_TARGET,
            EventReasonCode.INDEXING_FAILED,
            EventReasonCode.INDEXING_ACKNOWLEDGEMENT_FAILED,
            EventReasonCode.SUPERSESSION_ACKNOWLEDGEMENT_FAILED,
            EventReasonCode.STORAGE_FAILURE,
            EventReasonCode.CONFIGURATION_MISMATCH,
            EventReasonCode.OPERATION_FAILED,
        }
    ),
    EventName.RETRIEVAL_COMPLETED: frozenset(
        {
            EventReasonCode.MEMORIES_SELECTED,
            EventReasonCode.NO_ELIGIBLE_MEMORY,
            EventReasonCode.NO_RELEVANT_MEMORY,
            EventReasonCode.BUDGET_EXCLUDED,
            EventReasonCode.INDEXING_FAILED,
            EventReasonCode.STORAGE_FAILURE,
            EventReasonCode.CONFIGURATION_MISMATCH,
            EventReasonCode.OPERATION_FAILED,
        }
    ),
    EventName.FORGETTING_COMPLETED: frozenset(
        {
            EventReasonCode.MEMORY_NOT_FOUND,
            EventReasonCode.FORGOTTEN,
            EventReasonCode.ALREADY_FORGOTTEN,
            EventReasonCode.PHYSICAL_CLEANUP_PENDING,
            EventReasonCode.PHYSICAL_CLEANUP_IDENTITY_PENDING,
            EventReasonCode.INDEXING_FAILED,
            EventReasonCode.STORAGE_FAILURE,
            EventReasonCode.OPERATION_FAILED,
            EventReasonCode.CONFIGURATION_MISMATCH,
        }
    ),
    EventName.RECOVERY_COMPLETED: frozenset(
        reason
        for reason in EventReasonCode
        if reason.value.startswith(("ready_", "degraded_", "unavailable_"))
    ),
    EventName.STARTUP_READY: frozenset(
        {EventReasonCode.READY_EXISTING_GENERATION, EventReasonCode.READY_REBUILT_GENERATION}
    ),
    EventName.STARTUP_DEGRADED: frozenset(
        {EventReasonCode.DEGRADED_EXCLUDED_WORK_PENDING}
    ),
    EventName.STARTUP_UNAVAILABLE: frozenset(
        {EventReasonCode.CONFIGURATION_MISMATCH}
        | {
            reason
            for reason in EventReasonCode
            if reason.value.startswith("unavailable_")
        }
    ),
    EventName.STORAGE_COMPLETED: frozenset({EventReasonCode.OPERATION_COMPLETED}),
    EventName.STORAGE_FAILED: frozenset(
        {EventReasonCode.STORAGE_FAILURE, EventReasonCode.OPERATION_FAILED}
    ),
    EventName.INDEXING_COMPLETED: frozenset({EventReasonCode.OPERATION_COMPLETED}),
    EventName.INDEXING_FAILED: frozenset(
        {EventReasonCode.INDEXING_FAILED, EventReasonCode.OPERATION_FAILED}
    ),
    EventName.ADMISSION_RETRY: frozenset({EventReasonCode.RETRY_STARTED}),
    EventName.CONFIGURATION_FAILED: frozenset({EventReasonCode.CONFIGURATION_MISMATCH}),
}
_RECOVERY_STAGES = frozenset(
    {
        EventStage.RECOVERY_INVENTORY,
        EventStage.ADOPT_FORGETTING_VECTOR,
        EventStage.ACKNOWLEDGE_FORGETTING,
        EventStage.GENERATION_RECONCILE,
    }
)


@pydantic_dataclass(frozen=True, slots=True, config=_EVENT_CONFIG)
class MemoryEvent:
    """One immutable event containing only approved decision metadata."""

    schema_version: Annotated[int, Field(strict=True)]
    event_name: EventName
    occurred_at: Annotated[datetime, Field(strict=True)]
    duration_ms: NonNegativeStrictInt
    outcome: EventOutcome
    reason_code: EventReasonCode
    request_id: StrictText | None = None
    user_ref: StrictText | None = None
    memory_id: StrictText | None = None
    memory_ids: MemoryIds | None = None
    stage: EventStage | None = None
    retry_count: NonNegativeStrictInt | None = None
    candidate_count: NonNegativeStrictInt | None = None
    returned_count: NonNegativeStrictInt | None = None
    token_budget: NonNegativeStrictInt | None = None
    tokens_used: NonNegativeStrictInt | None = None
    index_vector_count: NonNegativeStrictInt | None = None
    embedding_model: StrictText | None = None
    vector_dimension: PositiveStrictInt | None = None
    readiness: ObservabilityReadiness | None = None
    rebuilt: StrictBoolean | None = None
    orphan_vectors_removed: NonNegativeStrictInt | None = None
    pending_count: NonNegativeStrictInt | None = None
    failed_count: NonNegativeStrictInt | None = None
    cleanup_pending_count: NonNegativeStrictInt | None = None

    @model_validator(mode="after")
    def _validate_event_shape(self) -> Self:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != timedelta(0):
            raise ValueError("occurred_at must be timezone-aware UTC")
        if self.request_id is not None and not self.request_id.strip():
            raise ValueError("request_id must be exact and non-empty")
        if self.user_ref is not None and _USER_REF.fullmatch(self.user_ref) is None:
            raise ValueError("user_ref must be an HMAC pseudonym")
        if self.memory_id is not None and (
            not self.memory_id or self.memory_id != self.memory_id.strip()
        ):
            raise ValueError("memory_id must be exact and non-empty")
        if self.memory_ids is not None:
            if any(not item or item != item.strip() for item in self.memory_ids):
                raise ValueError("memory_ids must contain exact non-empty identifiers")
            if len(set(self.memory_ids)) != len(self.memory_ids):
                raise ValueError("memory_ids must be unique")
        if self.embedding_model is not None and not self.embedding_model.strip():
            raise ValueError("embedding_model must not be empty")

        present = {
            field.name
            for field in fields(self)
            if field.name not in _COMMON_EVENT_FIELDS and getattr(self, field.name) is not None
        }
        allowed = _PROFILE_ALLOWED[self.event_name]
        required = _PROFILE_REQUIRED[self.event_name]
        if present - allowed or required - present:
            raise ValueError("event fields do not match the event profile")
        if self.outcome not in _PROFILE_OUTCOMES[self.event_name]:
            raise ValueError("outcome does not match the event profile")
        if self.reason_code not in _PROFILE_REASONS[self.event_name]:
            raise ValueError("reason_code does not match the event profile")

        self._validate_cross_field_invariants()
        return self

    def _validate_cross_field_invariants(self) -> None:
        if (self.request_id is None) is not (self.user_ref is None):
            raise ValueError("request_id and user_ref must be present together")
        if (
            self.event_name in {EventName.STORAGE_COMPLETED, EventName.STORAGE_FAILED}
            and self.stage not in _RECOVERY_STAGES
            and self.request_id is None
        ):
            raise ValueError("user-scoped storage events require request identity")
        if (
            self.event_name in {EventName.INDEXING_COMPLETED, EventName.INDEXING_FAILED}
            and self.stage not in _RECOVERY_STAGES
            and self.request_id is None
        ):
            raise ValueError("user-scoped indexing events require request identity")
        if self.event_name is EventName.ADMISSION_RETRY and self.retry_count != 1:
            raise ValueError("admission_retry requires retry_count=1")
        if self.event_name is EventName.ADMISSION_COMPLETED and self.retry_count not in {0, 1}:
            raise ValueError("admission retry_count must be 0 or 1")
        if self.event_name is EventName.FORGETTING_COMPLETED:
            if self.outcome is EventOutcome.NOT_FOUND and self.memory_id is not None:
                raise ValueError("not_found forgetting events must omit memory_id")
            if self.outcome not in {EventOutcome.NOT_FOUND, EventOutcome.FAILED} and self.memory_id is None:
                raise ValueError("non-not_found forgetting events require memory_id")
        if self.event_name is EventName.RETRIEVAL_COMPLETED:
            assert self.candidate_count is not None
            assert self.returned_count is not None
            assert self.token_budget is not None
            assert self.tokens_used is not None
            if self.returned_count > self.candidate_count:
                raise ValueError("returned_count must not exceed candidate_count")
            if self.tokens_used > self.token_budget:
                raise ValueError("tokens_used must not exceed token_budget")
            if self.memory_ids is None or len(self.memory_ids) != self.returned_count:
                raise ValueError("memory_ids must match returned_count")
        if self.readiness is not None and self.readiness.value != self.outcome.value:
            raise ValueError("readiness must match outcome")


_COMMON_EVENT_FIELDS = frozenset(
    {"schema_version", "event_name", "occurred_at", "duration_ms", "outcome", "reason_code"}
)


class HmacUserPseudonymizer:
    """Create stable domain-separated user references without exposing the key."""

    __slots__ = ("_key",)

    def __init__(self, key: bytes) -> None:
        if type(key) is not bytes:
            raise TypeError("HMAC key must be bytes")
        if len(key) < 32:
            raise ValueError("HMAC key must contain at least 32 bytes")
        self._key = key

    def pseudonymize(self, user_id: str) -> str:
        if type(user_id) is not str:
            raise TypeError("user_id must be a string")
        if not user_id:
            raise ValueError("user_id must not be empty")
        digest = hmac.new(
            self._key,
            _USER_HMAC_DOMAIN + user_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"u_{digest}"

    def __repr__(self) -> str:
        return "HmacUserPseudonymizer(<redacted>)"


def privacy_safe_reason_code(
    reason: object, *, fallback: EventReasonCode
) -> EventReasonCode:
    """Map existing result reasons to a closed code without retaining free text."""
    if not isinstance(fallback, EventReasonCode):
        raise TypeError("fallback must be an EventReasonCode")
    if isinstance(reason, str) and reason in {"sensitive_credential", "sensitive_check_unavailable"}:
        return EventReasonCode.SENSITIVE_ADMISSION_REJECTED
    if isinstance(reason, EventReasonCode):
        return reason
    if isinstance(reason, str):
        try:
            return EventReasonCode(reason)
        except ValueError:
            pass
    return fallback


def serialize_json_line(event: MemoryEvent) -> bytes:
    """Serialize one validated event as deterministic canonical UTF-8 JSONL."""
    if type(event) is not MemoryEvent:
        raise TypeError("event must be a MemoryEvent")
    event = MemoryEvent(**{field.name: getattr(event, field.name) for field in fields(event)})
    payload: dict[str, object] = {}
    for field in fields(event):
        value = getattr(event, field.name)
        if value is None:
            continue
        if isinstance(value, StrEnum):
            payload[field.name] = value.value
        elif field.name == "occurred_at":
            payload[field.name] = value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        elif isinstance(value, tuple):
            payload[field.name] = list(value)
        else:
            payload[field.name] = value
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


@dataclass(slots=True)
class CaptureEventSink:
    """In-process sink retaining validated events and their exact JSON lines."""

    _events: list[MemoryEvent]
    _lines: list[bytes]

    def __init__(self) -> None:
        self._events = []
        self._lines = []

    def emit(self, event: MemoryEvent) -> None:
        line = serialize_json_line(event)
        self._events.append(event)
        self._lines.append(line)

    @property
    def events(self) -> tuple[MemoryEvent, ...]:
        return tuple(self._events)

    @property
    def lines(self) -> tuple[bytes, ...]:
        return tuple(self._lines)
