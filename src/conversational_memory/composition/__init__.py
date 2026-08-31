"""Configuration validation and application composition."""

from conversational_memory.application import (
    ClockPort,
    EmbeddingPort,
    MemoryIdPort,
    MemoryService,
)
from conversational_memory.infrastructure import FaissVectorIndex, SQLiteMemoryRepository


def compose_memory_service(
    *,
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    embedder: EmbeddingPort,
    clock: ClockPort,
    memory_ids: MemoryIdPort,
) -> MemoryService:
    """Connect concrete local persistence to the application workflow."""
    return MemoryService(
        idempotency=repository,
        embedder=embedder,
        repository=repository,
        vector_index=vector_index,
        clock=clock,
        memory_ids=memory_ids,
    )


__all__ = ["compose_memory_service"]
