"""Application-owned interfaces for effects required by memory workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from conversational_memory.domain.models import MemoryRecord

from .contracts import Embedding, ExistingAdmission, PersistedPendingMemory


class IdempotencyPort(Protocol):
    """Resolve an admission key within a trusted owner scope."""

    def find(
        self, *, user_id: str, idempotency_key: str
    ) -> ExistingAdmission | None: ...


class EmbeddingPort(Protocol):
    """Generate an embedding for normalized durable content."""

    def embed(self, content: str) -> Embedding: ...


class MemoryRepositoryPort(Protocol):
    """Persist and acknowledge authoritative owner-scoped memory state."""

    def persist_pending(
        self,
        *,
        memory: MemoryRecord,
        embedding: Embedding,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> PersistedPendingMemory: ...

    def mark_indexed(self, *, user_id: str, memory_id: str) -> None: ...

    def mark_pending(self, *, user_id: str, memory_id: str) -> None: ...

    def mark_failed(
        self, *, user_id: str, memory_id: str, reason: str
    ) -> None: ...


class VectorIndexPort(Protocol):
    """Durably add one stable vector identifier to the derived index."""

    def add(self, *, vector_id: int, embedding: Embedding) -> None: ...


class ClockPort(Protocol):
    """Provide trusted lifecycle time."""

    def now(self) -> datetime: ...


class MemoryIdPort(Protocol):
    """Generate opaque stable domain identifiers."""

    def new_id(self) -> str: ...
