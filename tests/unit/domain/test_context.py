from datetime import UTC, datetime

import pytest

from conversational_memory.domain.context import (
    ContextExclusionReason,
    select_context,
    serialize_memory_block,
)
from conversational_memory.domain.models import (
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)


def _memory(memory_id: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id="user-1",
        content=content,
        memory_type=MemoryType.FACT,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="conversation",
            conversation_id="conversation-1",
            turn_id=f"turn-{memory_id}",
        ),
        created_at=datetime(2026, 8, 30, tzinfo=UTC),
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.INDEXED,
    )


def test_memory_block_has_the_exact_approved_serialization() -> None:
    assert serialize_memory_block(_memory("memory-1", "I prefer FAISS.")) == (
        "Memory memory-1:\nI prefer FAISS."
    )


def test_context_counts_complete_prospective_serialization_and_never_truncates() -> None:
    long_memory = _memory("long", "1234567890")
    short_memory = _memory("short", "x")
    counted: list[str] = []

    def count_characters(text: str) -> int:
        counted.append(text)
        return len(text)

    result = select_context(
        [long_memory, short_memory],
        token_budget=len(serialize_memory_block(short_memory)),
        count_tokens=count_characters,
    )

    assert result.context == serialize_memory_block(short_memory)
    assert result.selected_memories == (short_memory,)
    assert result.tokens_used == len(result.context)
    assert result.exclusions[0].memory_id == "long"
    assert result.exclusions[0].reason is ContextExclusionReason.BUDGET_EXCEEDED
    assert counted == [serialize_memory_block(long_memory), serialize_memory_block(short_memory)]
    assert "1234567890" not in result.context


def test_context_counts_labels_and_separators_in_the_whole_candidate_context() -> None:
    first = _memory("one", "A")
    second = _memory("two", "B")
    expected = f"{serialize_memory_block(first)}\n\n{serialize_memory_block(second)}"

    result = select_context([first, second], len(expected), len)

    assert result.context == expected
    assert result.tokens_used == len(expected)


@pytest.mark.parametrize("invalid_budget", [True, -1, 1.5, "1", None])
def test_context_rejects_invalid_token_budgets(invalid_budget: object) -> None:
    with pytest.raises(ValueError, match="token_budget must be a non-negative integer"):
        select_context([], invalid_budget, len)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid_count", [True, -1, 1.5, "1", None])
def test_context_rejects_invalid_token_counter_results(invalid_count: object) -> None:
    def invalid_counter(_text: str) -> int:
        return invalid_count  # type: ignore[return-value]

    with pytest.raises(ValueError, match="token counter must return a non-negative integer"):
        select_context([_memory("one", "A")], 100, invalid_counter)
