from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError
from pydantic.dataclasses import is_pydantic_dataclass

from conversational_memory.application import (
    AdmissionRequest,
    AdmissionResult,
    Embedding,
    ExistingAdmission,
    IndexingError,
    MemoryService,
    PersistedPendingMemory,
    RequestContext,
    RetrievalRequest,
    RetrievalResult,
    RetrievedMemory,
    StorageError,
    ValidationError,
)
from conversational_memory.domain.idempotency import RequestFingerprintInput, request_fingerprint
from conversational_memory.domain.models import (
    AdmissionDecision,
    EvidenceAuthority,
    IndexingState,
    LifecycleStatus,
    MemoryRecord,
    MemoryType,
    Provenance,
)

NOW = datetime(2026, 8, 31, 6, 30, tzinfo=UTC)


def _request(**changes: object) -> AdmissionRequest:
    values: dict[str, object] = {
        "idempotency_key": " turn-1 ",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "content": "I prefer FAISS.",
        "memory_type": "preference",
        "subject": "vector database",
        "value": "FAISS",
        "source_type": "explicit_user",
        "source_event_at": None,
        "valid_from": None,
        "valid_until": None,
    }
    values.update(changes)
    return AdmissionRequest(**values)  # type: ignore[arg-type]


def _fingerprint(request: AdmissionRequest) -> str:
    return request_fingerprint(
        RequestFingerprintInput(
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
        )
    )


def _pending_memory() -> MemoryRecord:
    return MemoryRecord(
        memory_id="memory-contract",
        user_id="user-1",
        content="A pending memory.",
        memory_type=MemoryType.FACT,
        provenance=Provenance(
            authority=EvidenceAuthority.EXPLICIT_USER,
            source_type="explicit_user",
            conversation_id="conversation-1",
            turn_id="turn-1",
        ),
        created_at=NOW,
        lifecycle_status=LifecycleStatus.ACTIVE,
        indexing_state=IndexingState.PENDING,
    )


class SpyIdempotency:
    def __init__(
        self,
        events: list[str],
        existing: ExistingAdmission | None = None,
    ) -> None:
        self.events = events
        self.existing = existing

    def find(self, *, user_id: str, idempotency_key: str) -> ExistingAdmission | None:
        self.events.append(f"idempotency.find:{user_id}:{idempotency_key}")
        return self.existing


class SpyEmbedder:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def embed(self, content: str) -> Embedding:
        self.events.append(f"embedder.embed:{content}")
        return Embedding(values=(0.1, 0.2), model_id="test-model", dimension=2)


class SpyClock:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def now(self) -> datetime:
        self.events.append("clock.now")
        return NOW


class SpyMemoryIds:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def new_id(self) -> str:
        self.events.append("memory_ids.new_id")
        return "memory-1"


class SpyTokenCounter:
    tokenizer_id = "cl100k_base"

    def count_tokens(self, text: str) -> int:
        return len(text)


class SpyRepository:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.persisted_memory: MemoryRecord | None = None
        self.persisted_key: str | None = None
        self.persisted_fingerprint: str | None = None
        self.fail_mark_indexed = False
        self.fail_mark_failed = False

    def persist_pending(
        self,
        *,
        memory: MemoryRecord,
        embedding: Embedding,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> PersistedPendingMemory:
        self.events.append("repository.persist_pending")
        self.persisted_memory = memory
        self.persisted_key = idempotency_key
        self.persisted_fingerprint = request_fingerprint
        assert embedding.model_id == "test-model"
        return PersistedPendingMemory(memory=memory, vector_id=41)

    def mark_indexed(self, *, user_id: str, memory_id: str) -> None:
        self.events.append(f"repository.mark_indexed:{user_id}:{memory_id}")
        if self.fail_mark_indexed:
            raise StorageError("acknowledgement failed")

    def mark_failed(self, *, user_id: str, memory_id: str, reason: str) -> None:
        self.events.append(f"repository.mark_failed:{user_id}:{memory_id}:{reason}")
        if self.fail_mark_failed:
            raise StorageError("failure state unavailable")


class SpyVectorIndex:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.failure: IndexingError | None = None

    def add(self, *, vector_id: int, embedding: Embedding) -> None:
        self.events.append(f"vector_index.add:{vector_id}:{embedding.model_id}")
        if self.failure is not None:
            raise self.failure


class Harness:
    def __init__(self, existing: ExistingAdmission | None = None) -> None:
        self.events: list[str] = []
        self.idempotency = SpyIdempotency(self.events, existing)
        self.embedder = SpyEmbedder(self.events)
        self.repository = SpyRepository(self.events)
        self.vector_index = SpyVectorIndex(self.events)
        self.service = MemoryService(
            idempotency=self.idempotency,
            embedder=self.embedder,
            repository=self.repository,
            vector_index=self.vector_index,
            token_counter=SpyTokenCounter(),
            clock=SpyClock(self.events),
            memory_ids=SpyMemoryIds(self.events),
        )


def test_admission_request_has_no_authoritative_user_id() -> None:
    assert "user_id" not in {field.name for field in fields(AdmissionRequest)}


def test_public_boundary_records_use_pydantic_validation() -> None:
    assert is_pydantic_dataclass(RequestContext)
    assert is_pydantic_dataclass(AdmissionRequest)
    assert is_pydantic_dataclass(AdmissionResult)
    assert is_pydantic_dataclass(RetrievalRequest)
    assert is_pydantic_dataclass(RetrievedMemory)
    assert is_pydantic_dataclass(RetrievalResult)


@pytest.mark.parametrize(
    ("field", "value"),
    [("user_id", 7), ("request_id", True), ("user_id", "   ")],
)
def test_request_context_rejects_invalid_trusted_identity(field: str, value: object) -> None:
    values: dict[str, object] = {"user_id": "user-1", "request_id": "request-1"}
    values[field] = value

    with pytest.raises(PydanticValidationError):
        RequestContext(**values)  # type: ignore[arg-type]


def test_admission_request_rejects_string_coercion_at_the_public_boundary() -> None:
    with pytest.raises(PydanticValidationError):
        _request(content=7)


@pytest.mark.parametrize("invalid_dimension", [True, 1.0, "1", 0, -1])
def test_embedding_dimension_requires_an_actual_positive_integer(
    invalid_dimension: object,
) -> None:
    with pytest.raises(ValueError, match="dimension must be a positive integer"):
        Embedding(
            values=(0.1,),
            model_id="test-model",
            dimension=invalid_dimension,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("invalid_vector_id", [True, 1.0, "1", 0, -1, 2**63])
def test_pending_vector_id_rejects_non_int64_values(invalid_vector_id: object) -> None:
    with pytest.raises(ValueError, match="positive signed-int64 integer"):
        PersistedPendingMemory(
            memory=_pending_memory(),
            vector_id=invalid_vector_id,  # type: ignore[arg-type]
        )


def test_pending_vector_id_accepts_signed_int64_maximum() -> None:
    persisted = PersistedPendingMemory(memory=_pending_memory(), vector_id=2**63 - 1)

    assert persisted.vector_id == 2**63 - 1


def test_accepted_admission_uses_ports_in_binding_order_and_trusted_owner_scope() -> None:
    harness = Harness()
    request = _request()

    result = harness.service.admit(RequestContext("user-1", "request-1"), request)

    assert harness.events == [
        "idempotency.find:user-1:turn-1",
        "embedder.embed:I prefer FAISS.",
        "clock.now",
        "memory_ids.new_id",
        "repository.persist_pending",
        "vector_index.add:41:test-model",
        "repository.mark_indexed:user-1:memory-1",
    ]
    assert result == AdmissionResult(
        decision=AdmissionDecision.ACCEPTED,
        reason="accepted_and_indexed",
        memory_id="memory-1",
        indexing_state=IndexingState.INDEXED,
        retrievable=True,
    )
    assert harness.repository.persisted_memory is not None
    assert harness.repository.persisted_memory.user_id == "user-1"
    assert harness.repository.persisted_memory.valid_from == NOW
    assert harness.repository.persisted_key == "turn-1"
    assert harness.repository.persisted_fingerprint == _fingerprint(request)


def test_identical_idempotent_replay_returns_original_result_without_mutation() -> None:
    request = _request()
    original = AdmissionResult(
        decision=AdmissionDecision.ACCEPTED,
        reason="accepted_and_indexed",
        memory_id="memory-existing",
        indexing_state=IndexingState.INDEXED,
        retrievable=True,
    )
    harness = Harness(ExistingAdmission(_fingerprint(request), original))

    result = harness.service.admit(RequestContext("user-1", "request-2"), request)

    assert result is original
    assert harness.events == ["idempotency.find:user-1:turn-1"]


def test_conflicting_idempotency_reuse_is_explicit_and_has_no_mutation() -> None:
    original_request = _request()
    existing = ExistingAdmission(
        _fingerprint(original_request),
        AdmissionResult(
            decision=AdmissionDecision.ACCEPTED,
            reason="accepted_and_indexed",
            memory_id="memory-existing",
            indexing_state=IndexingState.INDEXED,
            retrievable=True,
        ),
    )
    harness = Harness(existing)

    with pytest.raises(ValidationError, match="idempotency_key_conflict") as captured:
        harness.service.admit(
            RequestContext("user-1", "request-3"),
            _request(content="I prefer Qdrant."),
        )

    assert captured.value.reason == "idempotency_key_conflict"
    assert harness.events == ["idempotency.find:user-1:turn-1"]


def test_credential_rejection_stops_before_repository_embedding_or_indexing() -> None:
    harness = Harness()

    result = harness.service.admit(
        RequestContext("user-1", "request-4"),
        _request(content="password=hunter2"),
    )

    assert result == AdmissionResult(
        decision=AdmissionDecision.REJECTED,
        reason="sensitive_credential",
        memory_id=None,
        indexing_state=None,
        retrievable=False,
    )
    assert harness.events == ["idempotency.find:user-1:turn-1"]


def test_indexing_failure_records_failed_and_returns_nonretrievable_result() -> None:
    harness = Harness()
    harness.vector_index.failure = IndexingError("durable index failed")

    result = harness.service.admit(RequestContext("user-1", "request-5"), _request())

    assert result.indexing_state is IndexingState.FAILED
    assert result.retrievable is False
    assert result.retryable_error == "durable index failed"
    assert harness.events[-2:] == [
        "vector_index.add:41:test-model",
        "repository.mark_failed:user-1:memory-1:durable index failed",
    ]


def test_failed_failure_recording_leaves_memory_pending() -> None:
    harness = Harness()
    harness.vector_index.failure = IndexingError("durable index failed")
    harness.repository.fail_mark_failed = True

    result = harness.service.admit(RequestContext("user-1", "request-6"), _request())

    assert result.indexing_state is IndexingState.PENDING
    assert result.retrievable is False


def test_index_success_without_sqlite_acknowledgement_remains_pending() -> None:
    harness = Harness()
    harness.repository.fail_mark_indexed = True

    result = harness.service.admit(RequestContext("user-1", "request-7"), _request())

    assert result.reason == "indexing_acknowledgement_failed"
    assert result.indexing_state is IndexingState.PENDING
    assert result.retrievable is False
