"""Typed request, result, and boundary records for application workflows."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Self

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from conversational_memory.domain.context import ContextExclusion
from conversational_memory.domain.forgetting import ForgetOutcome, ForgettingCleanupState
from conversational_memory.domain.models import AdmissionDecision, IndexingState, MemoryRecord

_BOUNDARY_CONFIG = ConfigDict(strict=True, extra="forbid", arbitrary_types_allowed=True)
StrictText = Annotated[str, Field(strict=True)]
PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]
NonNegativeStrictInt = Annotated[int, Field(strict=True, ge=0)]
StrictBoolean = Annotated[bool, Field(strict=True)]


@pydantic_dataclass(frozen=True, slots=True, config=_BOUNDARY_CONFIG)
class RequestContext:
    """Identity established by the trusted calling adapter."""

    user_id: StrictText
    request_id: StrictText

    @field_validator("user_id", "request_id")
    @classmethod
    def _require_nonempty_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trusted request context fields must not be empty")
        return value


@pydantic_dataclass(frozen=True, slots=True, config=_BOUNDARY_CONFIG)
class AdmissionRequest:
    """Untrusted admission payload; authoritative identity is deliberately absent."""

    idempotency_key: StrictText
    conversation_id: StrictText
    turn_id: StrictText
    content: StrictText
    memory_type: StrictText
    subject: StrictText | None
    value: object
    source_type: StrictText
    source_event_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    supersedes_memory_id: StrictText | None = None

    @field_validator("supersedes_memory_id")
    @classmethod
    def _require_nonempty_supersession_target(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()):
            raise ValueError(
                "supersedes_memory_id must not contain surrounding whitespace"
            )
        return value


class RetrievalIntent(StrEnum):
    """Caller-declared lifecycle view for retrieval."""

    CURRENT = "current"
    HISTORICAL = "historical"


@pydantic_dataclass(frozen=True, slots=True, config=_BOUNDARY_CONFIG)
class RetrievalRequest:
    """Untrusted retrieval payload with a memory-only context allowance."""

    query: StrictText
    limit: PositiveStrictInt
    token_budget: NonNegativeStrictInt
    intent: RetrievalIntent = RetrievalIntent.CURRENT

    @field_validator("query")
    @classmethod
    def _require_nonempty_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be empty")
        return value


@pydantic_dataclass(frozen=True, slots=True, config=_BOUNDARY_CONFIG)
class ForgetRequest:
    """Untrusted request naming one opaque memory ID; owner identity is absent."""

    memory_id: StrictText

    @field_validator("memory_id")
    @classmethod
    def _require_exact_memory_id(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("memory_id must not contain surrounding whitespace")
        return value


@pydantic_dataclass(frozen=True, slots=True, config=_BOUNDARY_CONFIG)
class ForgetResult:
    """Strict structured result for owner-scoped forgetting."""

    outcome: ForgetOutcome
    reason: StrictText
    memory_id: StrictText | None
    deleted_at: datetime | None
    retrievable: StrictBoolean
    cleanup_complete: StrictBoolean
    retryable: StrictBoolean

    @model_validator(mode="after")
    def _validate_outcome_shape(self) -> Self:
        if not self.reason.strip():
            raise ValueError("reason must not be empty")
        if self.memory_id is not None and (
            not self.memory_id or self.memory_id != self.memory_id.strip()
        ):
            raise ValueError("memory_id must not contain surrounding whitespace")
        if self.deleted_at is not None and (
            self.deleted_at.tzinfo is None
            or self.deleted_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("deleted_at must be timezone-aware UTC")

        if self.outcome is ForgetOutcome.NOT_FOUND:
            valid = (
                self.reason == "memory_not_found"
                and self.memory_id is None
                and self.deleted_at is None
                and not self.retrievable
                and not self.cleanup_complete
                and not self.retryable
            )
        elif self.outcome is ForgetOutcome.FORGOTTEN:
            valid = (
                self.reason in {"forgotten", "already_forgotten"}
                and self.memory_id is not None
                and self.deleted_at is not None
                and not self.retrievable
                and self.cleanup_complete
                and not self.retryable
            )
        else:
            valid = (
                self.reason
                in {"physical_cleanup_pending", "physical_cleanup_identity_pending"}
                and self.memory_id is not None
                and self.deleted_at is not None
                and not self.retrievable
                and not self.cleanup_complete
                and self.retryable
            )
        if not valid:
            raise ValueError("forget result fields are inconsistent with outcome")
        return self


@dataclass(frozen=True, slots=True)
class ForgettingRecord:
    """Authoritative durable state for one forgetting operation."""

    memory_id: str
    user_id: str
    vector_id: int | None
    cleanup_state: ForgettingCleanupState
    requested_at: datetime
    completed_at: datetime | None

    def __post_init__(self) -> None:
        if not self.memory_id or self.memory_id != self.memory_id.strip():
            raise ValueError("memory_id must be an exact non-empty string")
        if not self.user_id.strip():
            raise ValueError("user_id must not be empty")
        _validate_optional_vector_id(self.vector_id)
        _require_utc_datetime("requested_at", self.requested_at)
        if self.completed_at is not None:
            _require_utc_datetime("completed_at", self.completed_at)
        if self.cleanup_state is ForgettingCleanupState.CLEANUP_PENDING:
            if self.completed_at is not None:
                raise ValueError("pending cleanup must not have completed_at")
        elif self.vector_id is None or self.completed_at is None:
            raise ValueError("complete cleanup requires vector_id and completed_at")


@dataclass(frozen=True, slots=True)
class ForgettingTarget:
    """One owner-scoped memory with its mapping and forgetting state."""

    memory: MemoryRecord
    vector_id: int | None
    forgetting: ForgettingRecord | None

    def __post_init__(self) -> None:
        _validate_optional_vector_id(self.vector_id)
        if self.forgetting is None:
            return
        if (
            self.forgetting.memory_id != self.memory.memory_id
            or self.forgetting.user_id != self.memory.user_id
        ):
            raise ValueError("forgetting state must match its memory")
        if (
            self.vector_id is not None
            and self.forgetting.vector_id is not None
            and self.vector_id != self.forgetting.vector_id
        ):
            raise ValueError("stored forgetting and mapping vector IDs must match")


def _validate_optional_vector_id(vector_id: int | None) -> None:
    if vector_id is not None and (
        isinstance(vector_id, bool)
        or not isinstance(vector_id, int)
        or vector_id <= 0
        or vector_id > 2**63 - 1
    ):
        raise ValueError("vector_id must be a positive signed-int64 integer")


def _require_utc_datetime(field_name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class Embedding:
    """Embedding output passed from the application to persistence and indexing ports."""

    values: tuple[float, ...]
    model_id: str
    dimension: int

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must not be empty")
        if (
            isinstance(self.dimension, bool)
            or not isinstance(self.dimension, int)
            or self.dimension <= 0
        ):
            raise ValueError("dimension must be a positive integer")
        if len(self.values) != self.dimension:
            raise ValueError("embedding length must match dimension")
        if not all(math.isfinite(value) for value in self.values):
            raise ValueError("embedding values must be finite")


@pydantic_dataclass(frozen=True, slots=True, config=_BOUNDARY_CONFIG)
class AdmissionResult:
    """Structured result of the admission and indexing workflow."""

    decision: AdmissionDecision
    reason: StrictText
    memory_id: StrictText | None
    indexing_state: IndexingState | None
    retrievable: StrictBoolean
    retryable_error: StrictText | None = None
    supersedes_memory_ids: tuple[StrictText, ...] = ()
    superseded_by_memory_id: StrictText | None = None


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    """One vector-index result expressed without infrastructure-specific types."""

    vector_id: int
    score: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.vector_id, bool)
            or not isinstance(self.vector_id, int)
            or self.vector_id <= 0
            or self.vector_id > 2**63 - 1
        ):
            raise ValueError("vector_id must be a positive signed-int64 integer")
        if not math.isfinite(self.score):
            raise ValueError("score must be finite")


@dataclass(frozen=True, slots=True)
class HydratedMemory:
    """An authoritative memory hydrated from its stable vector mapping."""

    vector_id: int
    memory: MemoryRecord


@pydantic_dataclass(frozen=True, slots=True, config=_BOUNDARY_CONFIG)
class RetrievedMemory:
    """An owner-authorized memory selected by vector similarity."""

    memory: MemoryRecord
    score: float


class RetrievalOutcome(StrEnum):
    """Mutually exclusive high-level result of the retrieval workflow."""

    MEMORIES_SELECTED = "memories_selected"
    NO_ELIGIBLE_MEMORY = "no_eligible_memory"
    NO_RELEVANT_MEMORY = "no_relevant_memory"
    BUDGET_EXCLUDED = "budget_excluded"


@pydantic_dataclass(frozen=True, slots=True, config=_BOUNDARY_CONFIG)
class RetrievalResult:
    """Ordered selected memories and exact bounded M1 context evidence."""

    memories: tuple[RetrievedMemory, ...]
    context: StrictText
    tokenizer: StrictText
    token_budget: NonNegativeStrictInt
    tokens_used: NonNegativeStrictInt
    included_memory_ids: tuple[StrictText, ...]
    exclusions: tuple[ContextExclusion, ...]
    outcome: RetrievalOutcome


@dataclass(frozen=True, slots=True)
class IndexingWork:
    """Stored inputs required to retry indexing without recomputation."""

    memory_id: str
    vector_id: int
    embedding: Embedding

    def __post_init__(self) -> None:
        if not self.memory_id.strip():
            raise ValueError("memory_id must not be empty")
        if (
            isinstance(self.vector_id, bool)
            or not isinstance(self.vector_id, int)
            or self.vector_id <= 0
            or self.vector_id > 2**63 - 1
        ):
            raise ValueError("vector_id must be a positive signed-int64 integer")


@dataclass(frozen=True, slots=True)
class ExistingAdmission:
    """Owner-scoped idempotency record returned by the lookup port."""

    request_fingerprint: str
    result: AdmissionResult
    indexing_work: IndexingWork | None = None


@dataclass(frozen=True, slots=True)
class PersistedPendingMemory:
    """Stable identifiers returned after authoritative pending persistence."""

    memory: MemoryRecord
    vector_id: int

    def __post_init__(self) -> None:
        if self.memory.indexing_state is not IndexingState.PENDING:
            raise ValueError("persisted memory must be pending")
        if (
            isinstance(self.vector_id, bool)
            or not isinstance(self.vector_id, int)
            or self.vector_id <= 0
            or self.vector_id > 2**63 - 1
        ):
            raise ValueError("vector_id must be a positive signed-int64 integer")
