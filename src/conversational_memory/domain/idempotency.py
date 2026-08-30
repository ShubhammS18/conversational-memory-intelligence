"""Canonical idempotency input normalization and request fingerprints."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class RequestFingerprintInput:
    """Caller-supplied fields included in the deterministic request body digest."""

    conversation_id: str
    turn_id: str
    content: str
    memory_type: str
    subject: str | None
    value: object
    source_type: str
    source_event_at: datetime | None
    valid_from: datetime | None
    valid_until: datetime | None


def normalize_text(value: str) -> str:
    """Apply the approved text normalization without altering interior whitespace."""
    normalized_newlines = value.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", normalized_newlines).strip()


def normalize_idempotency_key(value: str) -> str:
    """Normalize an idempotency key while preserving case and interior characters."""
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        raise ValueError("idempotency_key must not be empty")
    return normalized


def canonical_request_json(request: RequestFingerprintInput) -> str:
    """Return canonical JSON for exactly the approved fingerprint fields."""
    body = {
        "content": _required_text("content", request.content),
        "conversation_id": _required_text("conversation_id", request.conversation_id),
        "memory_type": _required_text("memory_type", request.memory_type),
        "source_event_at": _normalize_timestamp(request.source_event_at),
        "source_type": _required_text("source_type", request.source_type),
        "subject": None if request.subject is None else normalize_text(request.subject),
        "turn_id": _required_text("turn_id", request.turn_id),
        "valid_from": _normalize_timestamp(request.valid_from),
        "valid_until": _normalize_timestamp(request.valid_until),
        "value": _normalize_json_value(request.value),
    }
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def request_fingerprint(request: RequestFingerprintInput) -> str:
    """Return the lowercase SHA-256 digest of the canonical UTF-8 request body."""
    canonical_bytes = canonical_request_json(request).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def _required_text(field_name: str, value: str) -> str:
    normalized = normalize_text(value)
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _normalize_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise TypeError("JSON object keys must be strings")
            key = normalize_text(raw_key)
            if key in normalized:
                raise ValueError("JSON object keys collide after normalization")
            normalized[key] = _normalize_json_value(raw_value)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_json_value(item) for item in value]
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")
