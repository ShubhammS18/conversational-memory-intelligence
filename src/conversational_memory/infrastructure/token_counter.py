"""Exact downstream-tokenizer adapter for M1 context accounting."""

from __future__ import annotations

from typing import Any

import tiktoken

from conversational_memory.application.errors import ValidationError


class TiktokenTokenCounter:
    """Count text with the approved fixed ``cl100k_base`` encoding."""

    tokenizer_id = "cl100k_base"

    def __init__(self) -> None:
        try:
            self._encoding: Any = tiktoken.get_encoding(self.tokenizer_id)
        except Exception as error:
            raise ValidationError("invalid_tokenizer_configuration") from error

    def count_tokens(self, text: str) -> int:
        """Return the exact token count for one fully assembled context."""
        return len(self._encoding.encode(text, disallowed_special=()))


__all__ = ["TiktokenTokenCounter"]
