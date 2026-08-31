"""Synchronous application service coordinating admission through ports."""

from __future__ import annotations

from datetime import datetime

from conversational_memory.domain.admission import evaluate_credential_admission
from conversational_memory.domain.idempotency import (
    RequestFingerprintInput,
    normalize_idempotency_key,
    normalize_text,
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

from .contracts import AdmissionRequest, AdmissionResult, RequestContext
from .errors import IndexingError, StorageError, ValidationError
from .ports import (
    ClockPort,
    EmbeddingPort,
    IdempotencyPort,
    MemoryIdPort,
    MemoryRepositoryPort,
    VectorIndexPort,
)


class MemoryService:
    """Coordinate the M1 admission state transition without concrete infrastructure."""

    def __init__(
        self,
        *,
        idempotency: IdempotencyPort,
        embedder: EmbeddingPort,
        repository: MemoryRepositoryPort,
        vector_index: VectorIndexPort,
        clock: ClockPort,
        memory_ids: MemoryIdPort,
    ) -> None:
        self._idempotency = idempotency
        self._embedder = embedder
        self._repository = repository
        self._vector_index = vector_index
        self._clock = clock
        self._memory_ids = memory_ids

    def admit(self, context: RequestContext, request: AdmissionRequest) -> AdmissionResult:
        """Admit one memory using trusted identity and the approved M1 ordering."""
        self._validate_context(context)
        fingerprint_input, idempotency_key, fingerprint = self._canonicalize(request)

        existing = self._idempotency.find(
            user_id=context.user_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise ValidationError("idempotency_key_conflict")
            return existing.result

        policy_result = evaluate_credential_admission(fingerprint_input)
        if policy_result.decision is not AdmissionDecision.ACCEPTED:
            return AdmissionResult(
                decision=policy_result.decision,
                reason=policy_result.reason,
                memory_id=None,
                indexing_state=None,
                retrievable=False,
            )

        content = normalize_text(request.content)
        embedding = self._embedder.embed(content)
        created_at = self._clock.now()
        memory = self._new_memory(context, request, content, created_at)
        persisted = self._repository.persist_pending(
            memory=memory,
            embedding=embedding,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )

        try:
            self._vector_index.add(
                vector_id=persisted.vector_id,
                embedding=embedding,
            )
        except IndexingError as error:
            indexing_state = self._record_indexing_failure(context, persisted.memory, error)
            return AdmissionResult(
                decision=AdmissionDecision.ACCEPTED,
                reason="indexing_failed",
                memory_id=persisted.memory.memory_id,
                indexing_state=indexing_state,
                retrievable=False,
                retryable_error=str(error),
            )

        try:
            self._repository.mark_indexed(
                user_id=context.user_id,
                memory_id=persisted.memory.memory_id,
            )
        except StorageError as error:
            return AdmissionResult(
                decision=AdmissionDecision.ACCEPTED,
                reason="indexing_acknowledgement_failed",
                memory_id=persisted.memory.memory_id,
                indexing_state=IndexingState.PENDING,
                retrievable=False,
                retryable_error=str(error),
            )

        return AdmissionResult(
            decision=AdmissionDecision.ACCEPTED,
            reason="accepted_and_indexed",
            memory_id=persisted.memory.memory_id,
            indexing_state=IndexingState.INDEXED,
            retrievable=True,
        )

    @staticmethod
    def _validate_context(context: RequestContext) -> None:
        if not context.user_id.strip():
            raise ValidationError("invalid_user_id")
        if not context.request_id.strip():
            raise ValidationError("invalid_request_id")

    @staticmethod
    def _canonicalize(
        request: AdmissionRequest,
    ) -> tuple[RequestFingerprintInput, str, str]:
        fingerprint_input = RequestFingerprintInput(
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
        try:
            idempotency_key = normalize_idempotency_key(request.idempotency_key)
            fingerprint = request_fingerprint(fingerprint_input)
        except (TypeError, ValueError) as error:
            raise ValidationError("invalid_admission_request") from error
        return fingerprint_input, idempotency_key, fingerprint

    def _new_memory(
        self,
        context: RequestContext,
        request: AdmissionRequest,
        content: str,
        created_at: datetime,
    ) -> MemoryRecord:
        try:
            memory_type = MemoryType(normalize_text(request.memory_type))
            authority = EvidenceAuthority(normalize_text(request.source_type))
            subject = None if request.subject is None else normalize_text(request.subject)
            return MemoryRecord(
                memory_id=self._memory_ids.new_id(),
                user_id=context.user_id,
                content=content,
                memory_type=memory_type,
                provenance=Provenance(
                    authority=authority,
                    source_type=normalize_text(request.source_type),
                    conversation_id=normalize_text(request.conversation_id),
                    turn_id=normalize_text(request.turn_id),
                    source_event_at=request.source_event_at,
                ),
                created_at=created_at,
                lifecycle_status=LifecycleStatus.ACTIVE,
                indexing_state=IndexingState.PENDING,
                subject=subject,
                value=request.value,
                valid_from=request.valid_from or created_at,
                valid_until=request.valid_until,
            )
        except (TypeError, ValueError) as error:
            raise ValidationError("invalid_admission_request") from error

    def _record_indexing_failure(
        self,
        context: RequestContext,
        memory: MemoryRecord,
        error: IndexingError,
    ) -> IndexingState:
        try:
            self._repository.mark_failed(
                user_id=context.user_id,
                memory_id=memory.memory_id,
                reason=str(error),
            )
        except StorageError:
            return IndexingState.PENDING
        return IndexingState.FAILED
