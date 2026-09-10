"""Configuration validation and application composition."""

from dataclasses import dataclass
from pathlib import Path

from conversational_memory.application import (
    ClockPort,
    EmbeddingPort,
    MemoryIdPort,
    MemoryService,
    RecoveryCoordinator,
    RecoveryReadiness,
    RecoveryResult,
    ServiceUnavailableError,
    StorageError,
    TokenCounterPort,
)
from conversational_memory.infrastructure import (
    ALL_MPNET_BASE_V2_DIMENSION,
    ALL_MPNET_BASE_V2_MODEL_ID,
    FaissRecoveryAdapter,
    FaissVectorIndex,
    SentenceTransformerEmbedder,
    SQLiteMemoryRepository,
    TiktokenTokenCounter,
)

_MISSING_RELEVANCE_THRESHOLD = object()


@dataclass(frozen=True, slots=True)
class LocalMemoryRuntime:
    """A usable local service paired with its verified startup readiness."""

    service: MemoryService
    recovery: RecoveryResult


def compose_memory_service(
    *,
    repository: SQLiteMemoryRepository,
    vector_index: FaissVectorIndex,
    embedder: EmbeddingPort,
    token_counter: TokenCounterPort,
    clock: ClockPort,
    memory_ids: MemoryIdPort,
    relevance_threshold: object = _MISSING_RELEVANCE_THRESHOLD,
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
        relevance_threshold=relevance_threshold,
    )


def compose_recovered_memory_service(
    *,
    repository: SQLiteMemoryRepository,
    index_directory: str | Path,
    embedding_model: str,
    vector_dimension: int,
    embedder: EmbeddingPort,
    token_counter: TokenCounterPort,
    clock: ClockPort,
    memory_ids: MemoryIdPort,
    relevance_threshold: object = _MISSING_RELEVANCE_THRESHOLD,
) -> LocalMemoryRuntime:
    """Recover local durable state before exposing a usable memory service."""
    recovery = RecoveryCoordinator(
        inventory=repository,
        vector_index=FaissRecoveryAdapter(
            index_directory,
            embedding_model=embedding_model,
            vector_dimension=vector_dimension,
        ),
        embedding_model=embedding_model,
        vector_dimension=vector_dimension,
        clock=clock,
    ).recover()
    if recovery.readiness is RecoveryReadiness.UNAVAILABLE:
        raise ServiceUnavailableError(recovery.reason)
    vector_index = FaissVectorIndex(
        index_directory,
        embedding_model=embedding_model,
        vector_dimension=vector_dimension,
    )
    return LocalMemoryRuntime(
        service=compose_memory_service(
            repository=repository,
            vector_index=vector_index,
            embedder=embedder,
            token_counter=token_counter,
            clock=clock,
            memory_ids=memory_ids,
            relevance_threshold=relevance_threshold,
        ),
        recovery=recovery,
    )


def compose_local_memory_service(
    *,
    database_path: str | Path,
    index_directory: str | Path,
    model_cache_directory: str | Path,
    clock: ClockPort,
    memory_ids: MemoryIdPort,
    create_index_if_missing: bool = False,
    relevance_threshold: object = _MISSING_RELEVANCE_THRESHOLD,
) -> MemoryService:
    """Build the approved real local M1 service at the sole concrete composition point."""
    _ = create_index_if_missing
    try:
        repository = SQLiteMemoryRepository(database_path)
    except StorageError as error:
        raise ServiceUnavailableError("unavailable_schema_or_migration") from error
    return compose_recovered_memory_service(
        repository=repository,
        index_directory=index_directory,
        embedding_model=ALL_MPNET_BASE_V2_MODEL_ID,
        vector_dimension=ALL_MPNET_BASE_V2_DIMENSION,
        embedder=SentenceTransformerEmbedder(cache_directory=model_cache_directory),
        token_counter=TiktokenTokenCounter(),
        clock=clock,
        memory_ids=memory_ids,
        relevance_threshold=relevance_threshold,
    ).service


__all__ = [
    "ALL_MPNET_BASE_V2_DIMENSION",
    "ALL_MPNET_BASE_V2_MODEL_ID",
    "FaissVectorIndex",
    "LocalMemoryRuntime",
    "SQLiteMemoryRepository",
    "SentenceTransformerEmbedder",
    "TiktokenTokenCounter",
    "compose_local_memory_service",
    "compose_memory_service",
    "compose_recovered_memory_service",
]
