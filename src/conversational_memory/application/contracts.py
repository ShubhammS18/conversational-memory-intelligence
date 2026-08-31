"""Typed request, result, and boundary records for application workflows."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from conversational_memory.domain.models import AdmissionDecision, IndexingState, MemoryRecord


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Identity established by the trusted calling adapter."""

    user_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    """Untrusted admission payload; authoritative identity is deliberately absent."""

    idempotency_key: str
    conversation_id: str
    turn_id: str
    content: str
    memory_type: str
    subject: str | None
    value: object
    source_type: str
    source_event_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None


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


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    """Structured result of the admission and indexing workflow."""

    decision: AdmissionDecision
    reason: str
    memory_id: str | None
    indexing_state: IndexingState | None
    retrievable: bool
    retryable_error: str | None = None


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
