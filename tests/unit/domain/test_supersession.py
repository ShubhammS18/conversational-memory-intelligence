from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from conversational_memory.application.contracts import (
    AdmissionRequest,
    AdmissionResult,
    ExistingAdmission,
    RequestContext,
)
from conversational_memory.application.errors import ValidationError
from conversational_memory.application.service import MemoryService
from conversational_memory.domain.eligibility import is_current_state_eligible
from conversational_memory.domain.idempotency import (
    RequestFingerprintInput,
    canonical_request_json,
    request_fingerprint,
)
from conversational_memory.domain.models import (
    AdmissionDecision,
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)
from conversational_memory.domain.supersession import validate_supersession_target

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)


def _memory(
    memory_id: str,
    *,
    user_id: str = "user-1",
    authority: EvidenceAuthority = EvidenceAuthority.EXPLICIT_USER,
    memory_type: MemoryType = MemoryType.PREFERENCE,
    subject: str | None = "vector database",
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE,
    indexing_state: IndexingState = IndexingState.INDEXED,
    superseded_by: str | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        content=f"Memory {memory_id}",
        memory_type=memory_type,
        provenance=Provenance(
            authority=authority,
            source_type=authority.value,
            conversation_id="conversation-1",
            turn_id=f"turn-{memory_id}",
        ),
        created_at=NOW,
        lifecycle_status=lifecycle_status,
        indexing_state=indexing_state,
        subject=subject,
        valid_from=NOW,
        superseded_by=superseded_by,
    )


def test_explicit_replacement_accepts_same_owner_current_target_and_normalized_subject() -> None:
    validate_supersession_target(
        target=_memory("old", subject="vector database"),
        user_id="user-1",
        replacement_memory_id="new",
        replacement_authority=EvidenceAuthority.EXPLICIT_USER,
        replacement_subject="  vector database  ",
        replacement_memory_type=MemoryType.PREFERENCE,
    )


@pytest.mark.parametrize(
    ("case", "target", "changes"),
    [
        (
            "inferred replacement",
            _memory("old"),
            {"replacement_authority": EvidenceAuthority.INFERRED},
        ),
        ("cross-owner target", _memory("old", user_id="user-2"), {}),
        (
            "pending target",
            _memory("old", indexing_state=IndexingState.PENDING),
            {},
        ),
        (
            "failed target",
            _memory("old", indexing_state=IndexingState.FAILED),
            {},
        ),
        (
            "inactive target",
            _memory("old", lifecycle_status=LifecycleStatus.SUPERSEDED),
            {},
        ),
        (
            "already-superseded target",
            _memory("old", superseded_by="other"),
            {},
        ),
        ("missing replacement subject", _memory("old"), {"replacement_subject": None}),
        ("missing target subject", _memory("old", subject=None), {}),
        (
            "subject mismatch",
            _memory("old", subject="vector database"),
            {"replacement_subject": "programming language"},
        ),
        (
            "memory-type mismatch",
            _memory("old", memory_type=MemoryType.DECISION),
            {},
        ),
        ("self-reference", _memory("new"), {}),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_invalid_explicit_target_fails_closed(
    case: str,
    target: MemoryRecord,
    changes: dict[str, object],
) -> None:
    del case
    arguments: dict[str, object] = {
        "target": target,
        "user_id": "user-1",
        "replacement_memory_id": "new",
        "replacement_authority": EvidenceAuthority.EXPLICIT_USER,
        "replacement_subject": "vector database",
        "replacement_memory_type": MemoryType.PREFERENCE,
    }
    arguments.update(changes)

    with pytest.raises(ValueError, match="invalid supersession target"):
        validate_supersession_target(**arguments)  # type: ignore[arg-type]


def test_pending_replacement_cannot_enter_current_state_retrieval() -> None:
    replacement = replace(
        _memory("new"),
        indexing_state=IndexingState.PENDING,
        supersedes=(),
    )

    assert not is_current_state_eligible(replacement, user_id="user-1", now=NOW)


def _admission_request(**changes: object) -> AdmissionRequest:
    values: dict[str, object] = {
        "idempotency_key": "admission-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "content": "I now prefer Qdrant.",
        "memory_type": "preference",
        "subject": "vector database",
        "value": "Qdrant",
        "source_type": "explicit_user",
    }
    values.update(changes)
    return AdmissionRequest(**values)  # type: ignore[arg-type]


def _fingerprint_input(
    request: AdmissionRequest,
    *,
    supersedes_memory_id: str | None = None,
) -> RequestFingerprintInput:
    return RequestFingerprintInput(
        conversation_id=request.conversation_id,
        turn_id=request.turn_id,
        content=request.content,
        memory_type=request.memory_type,
        subject=request.subject,
        value=request.value,
        source_type=request.source_type,
        source_event_at=request.source_event_at,
        valid_from=request.valid_from,
        valid_until=request.valid_until,
        supersedes_memory_id=supersedes_memory_id,
    )


def test_omitted_target_preserves_ordinary_admission_and_legacy_fingerprint() -> None:
    request = _admission_request()
    legacy_canonical = (
        '{"content":"I now prefer Qdrant.","conversation_id":"conversation-1",'
        '"memory_type":"preference","source_event_at":null,'
        '"source_type":"explicit_user","subject":"vector database",'
        '"turn_id":"turn-1","valid_from":null,"valid_until":null,'
        '"value":"Qdrant"}'
    )

    assert request.supersedes_memory_id is None
    assert canonical_request_json(_fingerprint_input(request)) == legacy_canonical


class _ExistingAdmission:
    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = fingerprint

    def find(self, *, user_id: str, idempotency_key: str) -> ExistingAdmission | None:
        assert user_id == "user-1"
        assert idempotency_key == "admission-1"
        return ExistingAdmission(
            request_fingerprint=self._fingerprint,
            result=AdmissionResult(
                decision=AdmissionDecision.ACCEPTED,
                reason="accepted_and_indexed",
                memory_id="replacement",
                indexing_state=IndexingState.INDEXED,
                retrievable=True,
            ),
        )


class _ForbiddenEffect:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"effect must not be used before conflict: {name}")


def test_changing_only_target_conflicts_before_any_mutation() -> None:
    original = _admission_request(supersedes_memory_id="old-1")
    original_fingerprint = request_fingerprint(
        _fingerprint_input(original, supersedes_memory_id=original.supersedes_memory_id)
    )
    forbidden = _ForbiddenEffect()
    service = MemoryService(  # type: ignore[arg-type]
        idempotency=_ExistingAdmission(original_fingerprint),
        embedder=forbidden,
        repository=forbidden,
        vector_index=forbidden,
        token_counter=forbidden,
        clock=forbidden,
        memory_ids=forbidden,
        relevance_threshold=0.50,
    )

    with pytest.raises(ValidationError, match="idempotency_key_conflict"):
        service.admit(
            RequestContext(user_id="user-1", request_id="request-2"),
            _admission_request(supersedes_memory_id="old-2"),
        )


@pytest.mark.parametrize("target", [" old-1", "old-1 ", "\told-1"])
def test_supersession_target_rejects_surrounding_whitespace(target: str) -> None:
    with pytest.raises(PydanticValidationError, match="must not contain surrounding whitespace"):
        _admission_request(supersedes_memory_id=target)

    request = _admission_request()
    with pytest.raises(ValueError, match="must not contain surrounding whitespace"):
        canonical_request_json(
            _fingerprint_input(request, supersedes_memory_id=target)
        )


def test_accepted_opaque_target_remains_exact_in_fingerprint_and_service_handling() -> None:
    target = "Memory-ID_A:01"
    request = _admission_request(supersedes_memory_id=target)
    fingerprint_input = _fingerprint_input(
        request,
        supersedes_memory_id=request.supersedes_memory_id,
    )
    canonical = canonical_request_json(fingerprint_input)
    original_fingerprint = request_fingerprint(fingerprint_input)
    forbidden = _ForbiddenEffect()
    service = MemoryService(  # type: ignore[arg-type]
        idempotency=_ExistingAdmission(original_fingerprint),
        embedder=forbidden,
        repository=forbidden,
        vector_index=forbidden,
        token_counter=forbidden,
        clock=forbidden,
        memory_ids=forbidden,
        relevance_threshold=0.50,
    )

    result = service.admit(
        RequestContext(user_id="user-1", request_id="request-3"),
        request,
    )

    assert '"supersedes_memory_id":"Memory-ID_A:01"' in canonical
    assert request.supersedes_memory_id == target
    assert result.memory_id == "replacement"
