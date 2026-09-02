"""Typed request, result, and boundary records for application workflows."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from conversational_memory.domain.context import ContextExclusion
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


@pydantic_dataclass(frozen=True, slots=True, config=_BOUNDARY_CONFIG)
class RetrievalRequest:
    """Untrusted retrieval payload with a memory-only context allowance."""

    query: StrictText
    limit: PositiveStrictInt
    token_budget: NonNegativeStrictInt

    @field_validator("query")
    @classmethod
    def _require_nonempty_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be empty")
        return value


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
