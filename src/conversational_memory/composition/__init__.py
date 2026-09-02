"""Configuration validation and application composition."""

from pathlib import Path

from conversational_memory.application import (
    ClockPort,
    EmbeddingPort,
    MemoryIdPort,
    MemoryService,
    TokenCounterPort,
)
from conversational_memory.infrastructure import (
    ALL_MPNET_BASE_V2_DIMENSION,
    ALL_MPNET_BASE_V2_MODEL_ID,
    FaissVectorIndex,
    SentenceTransformerEmbedder,
    SQLiteMemoryRepository,
    TiktokenTokenCounter,
)


def compose_memory_service(
    *,
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    embedder: EmbeddingPort,
    token_counter: TokenCounterPort,
    clock: ClockPort,
    memory_ids: MemoryIdPort,
) -> MemoryService:
    """Connect concrete local persistence to the application workflow."""
    return MemoryService(
        idempotency=repository,
        embedder=embedder,
        repository=repository,
        vector_index=vector_index,
        token_counter=token_counter,
        clock=clock,
        memory_ids=memory_ids,
    )


def compose_local_memory_service(
    *,
    database_path: str | Path,
    index_directory: str | Path,
    model_cache_directory: str | Path,
    clock: ClockPort,
    memory_ids: MemoryIdPort,
    create_index_if_missing: bool = False,
) -> MemoryService:
    """Build the approved real local M1 service at the sole concrete composition point."""
    repository = SQLiteMemoryRepository(database_path)
    vector_index = FaissVectorIndex(
        index_directory,
        embedding_model=ALL_MPNET_BASE_V2_MODEL_ID,
        vector_dimension=ALL_MPNET_BASE_V2_DIMENSION,
        create_if_missing=create_index_if_missing,
    )
    return compose_memory_service(
        repository=repository,
        vector_index=vector_index,
        embedder=SentenceTransformerEmbedder(cache_directory=model_cache_directory),
        token_counter=TiktokenTokenCounter(),
        clock=clock,
        memory_ids=memory_ids,
    )


__all__ = ["compose_local_memory_service", "compose_memory_service"]
