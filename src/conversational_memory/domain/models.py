"""Immutable domain records and controlled vocabularies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MemoryType(StrEnum):
    """Durable memory categories approved by the admission design."""

    PREFERENCE = "preference"
    FACT = "fact"
    DECISION = "decision"
    CONSTRAINT = "constraint"


class EvidenceAuthority(StrEnum):
    """Authority attached to a memory's provenance."""

    EXPLICIT_USER = "explicit_user"
    INFERRED = "inferred"


class LifecycleStatus(StrEnum):
    """Lifecycle states represented by the approved D4 model."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class IndexingState(StrEnum):
    """Progress of a durable record through the derived index."""

    PENDING = "pending"
    INDEXED = "indexed"
    FAILED = "failed"


class AdmissionDecision(StrEnum):
    """Domain-level admission outcomes."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    TEMPORARY = "temporary"


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    """A pure admission-policy result with no side effects."""

    decision: AdmissionDecision
    reason: str


@dataclass(frozen=True, slots=True)
class Provenance:
    """Immutable evidence describing where a memory candidate came from."""

    authority: EvidenceAuthority
    source_type: str
    conversation_id: str
    turn_id: str
    source_event_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty("source_type", self.source_type)
        _require_non_empty("conversation_id", self.conversation_id)
        _require_non_empty("turn_id", self.turn_id)
        if self.source_event_at is not None:
            _require_aware("source_event_at", self.source_event_at)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Immutable logical memory record, independent of persistence details."""

    memory_id: str
    user_id: str
    content: str
    memory_type: MemoryType
    provenance: Provenance
    created_at: datetime
    lifecycle_status: LifecycleStatus
    indexing_state: IndexingState
    subject: str | None = None
    value: object | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    supersedes: tuple[str, ...] = ()
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("memory_id", self.memory_id)
        _require_non_empty("user_id", self.user_id)
        _require_non_empty("content", self.content)
        _require_aware("created_at", self.created_at)
        if self.valid_from is not None:
            _require_aware("valid_from", self.valid_from)
        if self.valid_until is not None:
            _require_aware("valid_until", self.valid_until)
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_from > self.valid_until
        ):
            raise ValueError("valid_from must not be after valid_until")


def _require_non_empty(field_name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_aware(field_name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
