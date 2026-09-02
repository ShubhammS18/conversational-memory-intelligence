"""Deterministic ordering for already-scored retrieval candidates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .models import EvidenceAuthority, MemoryRecord

_AUTHORITY_ORDER = {
    EvidenceAuthority.EXPLICIT_USER: 1,
    EvidenceAuthority.INFERRED: 0,
}


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    """A memory plus external relevance and an upstream eligibility decision."""

    memory: MemoryRecord
    relevance: float
    eligible: bool

    def __post_init__(self) -> None:
        if not math.isfinite(self.relevance):
            raise ValueError("relevance must be finite")


def rank_candidates(candidates: list[RetrievalCandidate]) -> tuple[RetrievalCandidate, ...]:
    """Filter by eligibility, then order by the approved deterministic hierarchy."""
    eligible = (candidate for candidate in candidates if candidate.eligible)
    return tuple(
        sorted(
            eligible,
            key=lambda candidate: (
                -candidate.relevance,
                -_recency_time(candidate.memory).timestamp(),
                -_AUTHORITY_ORDER[candidate.memory.provenance.authority],
                candidate.memory.memory_id,
            ),
        )
    )


def _recency_time(memory: MemoryRecord) -> datetime:
    return memory.valid_from if memory.valid_from is not None else memory.created_at
