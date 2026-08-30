"""Complete-memory context serialization and bounded selection."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .models import MemoryRecord


class ContextExclusionReason(StrEnum):
    """Deterministic reasons a context candidate was not included."""

    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(frozen=True, slots=True)
class ContextExclusion:
    """A memory excluded from context and the deterministic reason."""

    memory_id: str
    reason: ContextExclusionReason


@dataclass(frozen=True, slots=True)
class ContextSelection:
    """The complete bounded context and its selection evidence."""

    context: str
    tokens_used: int
    selected_memories: tuple[MemoryRecord, ...]
    exclusions: tuple[ContextExclusion, ...]


def serialize_memory_block(memory: MemoryRecord) -> str:
    """Serialize a complete memory using the approved M1 format."""
    return f"Memory {memory.memory_id}:\n{memory.content}"


def select_context(
    memories: Sequence[MemoryRecord],
    token_budget: int,
    count_tokens: Callable[[str], int],
) -> ContextSelection:
    """Greedily include complete blocks without exceeding the supplied allowance."""
    if (
        isinstance(token_budget, bool)
        or not isinstance(token_budget, int)
        or token_budget < 0
    ):
        raise ValueError("token_budget must be a non-negative integer")

    selected: list[MemoryRecord] = []
    exclusions: list[ContextExclusion] = []
    context = ""
    tokens_used = 0

    for memory in memories:
        block = serialize_memory_block(memory)
        prospective = block if not context else f"{context}\n\n{block}"
        prospective_tokens = count_tokens(prospective)
        if (
            isinstance(prospective_tokens, bool)
            or not isinstance(prospective_tokens, int)
            or prospective_tokens < 0
        ):
            raise ValueError("token counter must return a non-negative integer")
        if prospective_tokens <= token_budget:
            selected.append(memory)
            context = prospective
            tokens_used = prospective_tokens
        else:
            exclusions.append(
                ContextExclusion(
                    memory_id=memory.memory_id,
                    reason=ContextExclusionReason.BUDGET_EXCEEDED,
                )
            )

    return ContextSelection(
        context=context,
        tokens_used=tokens_used,
        selected_memories=tuple(selected),
        exclusions=tuple(exclusions),
    )
