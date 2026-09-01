"""Application workflows and their required interfaces."""

from .contracts import (
    AdmissionRequest,
    AdmissionResult,
    Embedding,
    ExistingAdmission,
    HydratedMemory,
    IndexingWork,
    PersistedPendingMemory,
    RequestContext,
    RetrievalRequest,
    RetrievalResult,
    RetrievedMemory,
    VectorSearchHit,
)
from .errors import (
    AuthorizationError,
    ConfigurationMismatchError,
    IndexingError,
    ServiceUnavailableError,
    StorageError,
    ValidationError,
)
from .ports import (
    ClockPort,
    EmbeddingPort,
    IdempotencyPort,
    MemoryIdPort,
    MemoryRepositoryPort,
    TokenCounterPort,
    VectorIndexPort,
)
from .service import MemoryService

__all__ = [
    "AdmissionRequest",
    "AdmissionResult",
    "AuthorizationError",
    "ClockPort",
    "ConfigurationMismatchError",
    "Embedding",
    "EmbeddingPort",
    "ExistingAdmission",
    "HydratedMemory",
    "IdempotencyPort",
    "IndexingError",
    "IndexingWork",
    "MemoryIdPort",
    "MemoryRepositoryPort",
    "MemoryService",
    "PersistedPendingMemory",
    "RequestContext",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievedMemory",
    "ServiceUnavailableError",
    "StorageError",
    "TokenCounterPort",
    "ValidationError",
    "VectorIndexPort",
    "VectorSearchHit",
]
