from dataclasses import fields
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError
from pydantic.dataclasses import is_pydantic_dataclass

from conversational_memory.application import ForgetRequest, ForgetResult
from conversational_memory.domain.forgetting import ForgetOutcome

DELETED_AT = datetime(2026, 9, 8, 14, 0, tzinfo=UTC)


def test_forget_request_is_strict_owner_free_and_preserves_opaque_id() -> None:
    request = ForgetRequest(memory_id="Memory-007_A")

    assert is_pydantic_dataclass(ForgetRequest)
    assert {field.name for field in fields(ForgetRequest)} == {"memory_id"}
    assert request.memory_id == "Memory-007_A"


@pytest.mark.parametrize("memory_id", ["", " ", " memory-1", "memory-1 "])
def test_forget_request_rejects_empty_or_surrounding_whitespace(
    memory_id: str,
) -> None:
    with pytest.raises(PydanticValidationError):
        ForgetRequest(memory_id=memory_id)


def test_forget_request_rejects_coercion_and_owner_fields() -> None:
    with pytest.raises(PydanticValidationError):
        ForgetRequest(memory_id=7)  # type: ignore[arg-type]
    with pytest.raises(PydanticValidationError):
        ForgetRequest(memory_id="memory-1", user_id="other")  # type: ignore[call-arg]


@pytest.mark.parametrize("reason", ["forgotten", "already_forgotten"])
def test_completed_result_has_one_strict_shape(reason: str) -> None:
    result = ForgetResult(
        outcome=ForgetOutcome.FORGOTTEN,
        reason=reason,
        memory_id="memory-1",
        deleted_at=DELETED_AT,
        retrievable=False,
        cleanup_complete=True,
        retryable=False,
    )

    assert is_pydantic_dataclass(ForgetResult)
    assert result.deleted_at is DELETED_AT


@pytest.mark.parametrize(
    "reason", ["physical_cleanup_pending", "physical_cleanup_identity_pending"]
)
def test_pending_result_has_one_strict_shape(reason: str) -> None:
    ForgetResult(
        outcome=ForgetOutcome.CLEANUP_PENDING,
        reason=reason,
        memory_id="memory-1",
        deleted_at=DELETED_AT,
        retrievable=False,
        cleanup_complete=False,
        retryable=True,
    )


def test_not_found_result_is_opaque() -> None:
    ForgetResult(
        outcome=ForgetOutcome.NOT_FOUND,
        reason="memory_not_found",
        memory_id=None,
        deleted_at=None,
        retrievable=False,
        cleanup_complete=False,
        retryable=False,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": "wrong"},
        {"memory_id": "memory-1"},
        {"deleted_at": DELETED_AT},
        {"retrievable": True},
        {"cleanup_complete": True},
        {"retryable": True},
    ],
)
def test_not_found_rejects_nonopaque_or_inconsistent_shapes(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "outcome": ForgetOutcome.NOT_FOUND,
        "reason": "memory_not_found",
        "memory_id": None,
        "deleted_at": None,
        "retrievable": False,
        "cleanup_complete": False,
        "retryable": False,
    }
    values.update(changes)
    with pytest.raises(PydanticValidationError):
        ForgetResult(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"memory_id": None},
        {"memory_id": " memory-1"},
        {"deleted_at": None},
        {"deleted_at": DELETED_AT.replace(tzinfo=None)},
        {"retrievable": True},
        {"cleanup_complete": False},
        {"retryable": True},
    ],
)
def test_completed_result_rejects_invalid_shapes(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "outcome": ForgetOutcome.FORGOTTEN,
        "reason": "forgotten",
        "memory_id": "memory-1",
        "deleted_at": DELETED_AT,
        "retrievable": False,
        "cleanup_complete": True,
        "retryable": False,
    }
    values.update(changes)
    with pytest.raises(PydanticValidationError):
        ForgetResult(**values)  # type: ignore[arg-type]


def test_result_rejects_type_coercion_and_invalid_reason() -> None:
    with pytest.raises(PydanticValidationError):
        ForgetResult(
            outcome=ForgetOutcome.CLEANUP_PENDING,
            reason="",
            memory_id="memory-1",
            deleted_at=DELETED_AT,
            retrievable=0,  # type: ignore[arg-type]
            cleanup_complete=False,
            retryable=True,
        )
