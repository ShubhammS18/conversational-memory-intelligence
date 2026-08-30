"""Deterministic credential-focused admission policy."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime

from .idempotency import RequestFingerprintInput, normalize_text
from .models import AdmissionDecision, AdmissionResult

_PRIVATE_KEY = re.compile(
    r"-----BEGIN[ \t]+(?:[A-Z0-9]+[ \t]+)*PRIVATE[ \t]+KEY(?:[ \t]+BLOCK)?-----",
    re.IGNORECASE,
)
_LABELED_CREDENTIAL = re.compile(
    r"""(?ix)
    (?<![\w-])
    (?:password|passcode|api[ _-]*key|access[ _-]*token|refresh[ _-]*token|secret[ _-]*key)
    [ \t]*(?:[:=]|\bis\b)[ \t]*
    (?:"[^"\r\n]{4,}"|'[^'\r\n]{4,}'|[^\s,;]{4,})
    """
)
_OPENAI_STYLE_KEY = re.compile(
    r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9][A-Za-z0-9_-]{7,}(?![A-Za-z0-9_-])"
)
_CREDENTIAL_PATTERNS = (_PRIVATE_KEY, _LABELED_CREDENTIAL, _OPENAI_STYLE_KEY)


def evaluate_credential_admission(request: RequestFingerprintInput) -> AdmissionResult:
    """Reject minimum-policy credential forms without performing any side effect."""
    try:
        for value in _stored_caller_values(request):
            for text in _iter_normalized_strings(value):
                if any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS):
                    return AdmissionResult(
                        decision=AdmissionDecision.REJECTED,
                        reason="sensitive_credential",
                    )
    except Exception:  # noqa: BLE001
        return AdmissionResult(
            decision=AdmissionDecision.REJECTED,
            reason="sensitive_check_unavailable",
        )

    return AdmissionResult(
        decision=AdmissionDecision.ACCEPTED,
        reason="credential_policy_passed",
    )


def _stored_caller_values(request: RequestFingerprintInput) -> tuple[object, ...]:
    return (
        request.conversation_id,
        request.turn_id,
        request.content,
        request.memory_type,
        request.subject,
        request.value,
        request.source_type,
    )


def _iter_normalized_strings(value: object) -> Iterator[str]:
    if value is None or isinstance(value, (bool, int, float, datetime)):
        return
    if isinstance(value, str):
        yield normalize_text(value)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise TypeError("stored object keys must be strings")
            yield normalize_text(key)
            yield from _iter_normalized_strings(nested_value)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _iter_normalized_strings(item)
        return
    raise TypeError(f"unscannable stored value type: {type(value).__name__}")
