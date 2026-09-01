from __future__ import annotations

from datetime import UTC, datetime

import pytest
import tiktoken

from conversational_memory.application import ValidationError
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
from conversational_memory.infrastructure import TiktokenTokenCounter

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _memory(memory_id: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id="user-1",
        content=content,
        memory_type=MemoryType.FACT,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id=f"turn-{memory_id}",
        ),
        created_at=NOW,
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.INDEXED,
    )


def test_exact_fit_includes_the_complete_memory_block() -> None:
    counter = TiktokenTokenCounter()
    memory = _memory("exact", "I prefer FAISS.")
    block = serialize_memory_block(memory)
    budget = counter.count_tokens(block)

    result = select_context([memory], budget, counter.count_tokens)

    assert result.context == block
    assert result.tokens_used == budget
    assert result.selected_memories == (memory,)
    assert result.exclusions == ()


def test_one_token_under_exact_fit_excludes_without_truncation() -> None:
    counter = TiktokenTokenCounter()
    memory = _memory("overflow", "This complete memory must not be truncated.")
    block = serialize_memory_block(memory)

    result = select_context(
        [memory],
        counter.count_tokens(block) - 1,
        counter.count_tokens,
    )

    assert result.context == ""
    assert result.tokens_used == 0
    assert result.selected_memories == ()
    assert result.exclusions[0].memory_id == "overflow"
    assert result.exclusions[0].reason is ContextExclusionReason.BUDGET_EXCEEDED


def test_oversized_block_is_skipped_and_later_smaller_block_is_considered() -> None:
    counter = TiktokenTokenCounter()
    oversized = _memory("oversized", "long memory " * 100)
    smaller = _memory("smaller", "short")
    smaller_block = serialize_memory_block(smaller)

    result = select_context(
        [oversized, smaller],
        counter.count_tokens(smaller_block),
        counter.count_tokens,
    )

    assert result.context == smaller_block
    assert result.selected_memories == (smaller,)
    assert [exclusion.memory_id for exclusion in result.exclusions] == ["oversized"]


def test_empty_candidates_with_zero_budget_produce_empty_zero_token_context() -> None:
    counter = TiktokenTokenCounter()

    result = select_context([], 0, counter.count_tokens)

    assert result.context == ""
    assert result.tokens_used == 0
    assert result.selected_memories == ()
    assert result.exclusions == ()


def test_zero_budget_excludes_a_nonempty_complete_block() -> None:
    counter = TiktokenTokenCounter()
    memory = _memory("zero", "still complete")

    result = select_context([memory], 0, counter.count_tokens)

    assert result.context == ""
    assert result.tokens_used == 0
    assert result.selected_memories == ()
    assert result.exclusions[0].memory_id == "zero"


def test_unicode_uses_real_cl100k_base_tokenization() -> None:
    counter = TiktokenTokenCounter()
    memory = _memory("unicode", "नमस्ते 🌍 café")
    block = serialize_memory_block(memory)
    expected_tokens = len(tiktoken.get_encoding("cl100k_base").encode(block))

    result = select_context([memory], expected_tokens, counter.count_tokens)

    assert counter.tokenizer_id == "cl100k_base"
    assert result.context == block
    assert result.tokens_used == expected_tokens


def test_special_token_looking_text_is_counted_as_ordinary_text() -> None:
    counter = TiktokenTokenCounter()
    memory = _memory("special", "The literal marker is <|endoftext|>.")
    block = serialize_memory_block(memory)
    expected_tokens = len(
        tiktoken.get_encoding("cl100k_base").encode(block, disallowed_special=())
    )

    result = select_context([memory], expected_tokens, counter.count_tokens)

    assert result.context == block
    assert result.tokens_used == expected_tokens
    assert result.selected_memories == (memory,)


def test_tokenizer_initialization_failure_is_validation_error_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fail_get_encoding(name: str) -> None:
        calls.append(name)
        raise RuntimeError("tokenizer data unavailable")

    monkeypatch.setattr(tiktoken, "get_encoding", fail_get_encoding)

    with pytest.raises(
        ValidationError,
        match="invalid_tokenizer_configuration",
    ) as captured:
        TiktokenTokenCounter()

    assert calls == ["cl100k_base"]
    assert isinstance(captured.value.__cause__, RuntimeError)


def test_exact_count_uses_the_fully_assembled_context_with_separator() -> None:
    counter = TiktokenTokenCounter()
    first = _memory("first", "alpha")
    second = _memory("second", "βeta")
    expected_context = (
        f"{serialize_memory_block(first)}\n\n{serialize_memory_block(second)}"
    )
    exact_budget = counter.count_tokens(expected_context)

    result = select_context([first, second], exact_budget, counter.count_tokens)

    assert result.context == expected_context
    assert result.tokens_used == counter.count_tokens(result.context)
    assert result.tokens_used == exact_budget
