"""Domain models and deterministic memory policies."""

from .admission import evaluate_credential_admission
from .context import ContextSelection, select_context, serialize_memory_block
from .eligibility import is_current_state_eligible
from .idempotency import RequestFingerprintInput, normalize_idempotency_key, request_fingerprint
from .models import (
    AdmissionDecision,
    AdmissionResult,
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from .ranking import RetrievalCandidate, rank_candidates
from .relevance import is_relevant, validate_relevance_threshold
from .supersession import validate_supersession_target

__all__ = [
    "AdmissionDecision",
    "AdmissionResult",
    "ContextSelection",
    "EvidenceAuthority",
    "IndexingState",
    "LifecycleStatus",
    "MemoryRecord",
    "MemoryType",
    "Provenance",
    "RequestFingerprintInput",
    "RetrievalCandidate",
    "evaluate_credential_admission",
    "is_current_state_eligible",
    "is_relevant",
    "normalize_idempotency_key",
    "rank_candidates",
    "request_fingerprint",
    "select_context",
    "serialize_memory_block",
    "validate_relevance_threshold",
    "validate_supersession_target",
]
