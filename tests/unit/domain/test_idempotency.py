from datetime import UTC, datetime, timedelta, timezone

import pytest

from conversational_memory.domain.idempotency import (
    RequestFingerprintInput,
    canonical_request_json,
    normalize_idempotency_key,
    request_fingerprint,
)


def _request(**changes: object) -> RequestFingerprintInput:
    values: dict[str, object] = {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "content": "I prefer Cafe\u0301.\r\nPlease remember it.",
        "memory_type": "preference",
        "subject": "database",
        "value": {"choice": "FAISS", "priority": 1},
        "source_type": "explicit_user",
        "source_event_at": datetime(2026, 8, 30, 10, 30, tzinfo=UTC),
        "valid_from": None,
        "valid_until": None,
    }
    values.update(changes)
    return RequestFingerprintInput(**values)  # type: ignore[arg-type]


def test_idempotency_key_normalization_is_nfc_trimmed_and_case_sensitive() -> None:
    assert normalize_idempotency_key("  Cafe\u0301-Key  ") == "Café-Key"
    assert normalize_idempotency_key("Key") != normalize_idempotency_key("key")
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_idempotency_key(" \t\n ")


def test_fingerprint_is_stable_across_approved_normalizations() -> None:
    equivalent = _request(
        content="I prefer Café.\nPlease remember it.",
        value={"priority": 1, "choice": "FAISS"},
        source_event_at=datetime(
            2026, 8, 30, 16, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))
        ),
    )

    assert canonical_request_json(_request()) == canonical_request_json(equivalent)
    assert request_fingerprint(_request()) == request_fingerprint(equivalent)


def test_fingerprint_changes_when_a_canonical_body_field_changes() -> None:
    original = request_fingerprint(_request())

    assert request_fingerprint(_request(turn_id="turn-2")) != original
    assert request_fingerprint(_request(value={"choice": "Qdrant", "priority": 1})) != original


def test_fingerprint_rejects_unsafe_or_ambiguous_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        request_fingerprint(
            _request(source_event_at=datetime(2026, 8, 30, 10, 30, tzinfo=UTC).replace(tzinfo=None))
        )

    with pytest.raises(ValueError, match="finite"):
        request_fingerprint(_request(value={"score": float("nan")}))

    with pytest.raises(ValueError, match="collide after normalization"):
        request_fingerprint(_request(value={"Café": 1, "Cafe\u0301": 2}))
