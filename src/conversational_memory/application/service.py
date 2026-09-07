"""Synchronous application service coordinating admission through ports."""

from __future__ import annotations

from datetime import datetime
from threading import Lock

from conversational_memory.domain.admission import evaluate_credential_admission
from conversational_memory.domain.context import (
    ContextExclusion,
    ContextExclusionReason,
    select_context,
)
from conversational_memory.domain.eligibility import (
    is_current_state_eligible,
    is_historical_eligible,
)
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
from conversational_memory.domain.ranking import RetrievalCandidate, rank_candidates
from conversational_memory.domain.relevance import (
    is_relevant,
    validate_relevance_threshold,
)
from conversational_memory.domain.supersession import validate_supersession_target

from .contracts import (
    AdmissionRequest,
    AdmissionResult,
    Embedding,
    ExistingAdmission,
    RequestContext,
    RetrievalIntent,
    RetrievalOutcome,
    RetrievalRequest,
    RetrievalResult,
    RetrievedMemory,
)
from .errors import (
    AuthorizationError,
    ConfigurationError,
    IndexingError,
    StorageError,
    ValidationError,
)
from .ports import (
    ClockPort,
    EmbeddingPort,
    IdempotencyPort,
    MemoryIdPort,
    MemoryRepositoryPort,
    TokenCounterPort,
    VectorIndexPort,
)

_M1_TOKENIZER = "cl100k_base"
_PROCESS_WRITE_LOCK = Lock()
_MISSING_RELEVANCE_THRESHOLD = object()


class MemoryService:
    """Coordinate the M1 admission state transition without concrete infrastructure."""

    def __init__(
        self,
        *,
        idempotency: IdempotencyPort,
        embedder: EmbeddingPort,
        repository: MemoryRepositoryPort,
        vector_index: VectorIndexPort,
        token_counter: TokenCounterPort,
        clock: ClockPort,
        memory_ids: MemoryIdPort,
        relevance_threshold: object = _MISSING_RELEVANCE_THRESHOLD,
    ) -> None:
        self._idempotency = idempotency
        self._embedder = embedder
        self._repository = repository
        self._vector_index = vector_index
        self._token_counter = token_counter
        self._clock = clock
        self._memory_ids = memory_ids
        self._known_published_supersessions: set[tuple[str, str, int, str]] = set()
        try:
            self._relevance_threshold = validate_relevance_threshold(
                relevance_threshold
            )
        except ValueError as error:
            raise ConfigurationError("invalid_relevance_threshold") from error

    def admit(self, context: RequestContext, request: AdmissionRequest) -> AdmissionResult:
        """Admit one memory using trusted identity and the approved M1 ordering."""
        self._validate_context(context)
        fingerprint_input, idempotency_key, fingerprint = self._canonicalize(request)

        with _PROCESS_WRITE_LOCK:
            existing = self._idempotency.find(
                user_id=context.user_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise ValidationError("idempotency_key_conflict")
                if existing.result.indexing_state is IndexingState.INDEXED:
                    return existing.result
                return self._retry_indexing(
                    context,
                    existing,
                    supersedes_memory_id=request.supersedes_memory_id,
                )

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
            target_memory_id = request.supersedes_memory_id
            if target_memory_id is not None:
                created_at = self._clock.now()
                memory = self._new_memory(context, request, content, created_at)
                target = self._repository.find_supersession_target(
                    user_id=context.user_id,
                    memory_id=target_memory_id,
                )
                if target is None:
                    raise ValidationError("invalid_supersession_target")
                try:
                    validate_supersession_target(
                        target=target,
                        user_id=context.user_id,
                        replacement_memory_id=memory.memory_id,
                        replacement_authority=memory.provenance.authority,
                        replacement_subject=memory.subject,
                        replacement_memory_type=memory.memory_type,
                    )
                except ValueError as error:
                    raise ValidationError("invalid_supersession_target") from error
                embedding = self._embedder.embed(content)
            else:
                embedding = self._embedder.embed(content)
                created_at = self._clock.now()
                memory = self._new_memory(context, request, content, created_at)
            persisted = self._repository.persist_pending(
                memory=memory,
                embedding=embedding,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
            )

            return self._index_and_acknowledge(
                context=context,
                memory_id=persisted.memory.memory_id,
                vector_id=persisted.vector_id,
                embedding=embedding,
                supersedes_memory_id=target_memory_id,
            )

    def retrieve(
        self,
        context: RequestContext,
        request: RetrievalRequest,
    ) -> RetrievalResult:
        """Retrieve only owner-authorized indexed memories through a scoped FAISS search."""
        self._validate_context(context)
        query, limit, token_budget = self._validate_retrieval_request(request)
        if self._token_counter.tokenizer_id != _M1_TOKENIZER:
            raise ValidationError("invalid_tokenizer_configuration")
        now: datetime | None = None
        if request.intent is RetrievalIntent.CURRENT:
            now = self._clock.now()
            allowed_vector_ids = self._repository.current_state_vector_ids(
                user_id=context.user_id,
                now=now,
            )
        else:
            allowed_vector_ids = self._repository.historical_vector_ids(
                user_id=context.user_id,
            )
        if not allowed_vector_ids:
            return RetrievalResult(
                memories=(),
                context="",
                tokenizer=_M1_TOKENIZER,
                token_budget=token_budget,
                tokens_used=0,
                included_memory_ids=(),
                exclusions=(),
                outcome=RetrievalOutcome.NO_ELIGIBLE_MEMORY,
            )

        query_embedding = self._embedder.embed(query)
        hits = self._vector_index.search(
            embedding=query_embedding,
            allowed_vector_ids=allowed_vector_ids,
            limit=limit,
        )
        allowed = set(allowed_vector_ids)
        if any(hit.vector_id not in allowed for hit in hits):
            raise AuthorizationError("unauthorized_retrieval_result")

        hit_vector_ids = tuple(hit.vector_id for hit in hits)
        if request.intent is RetrievalIntent.CURRENT:
            if now is None:
                raise AuthorizationError("unauthorized_retrieval_result")
            hydrated = self._repository.hydrate_current_state(
                user_id=context.user_id,
                vector_ids=hit_vector_ids,
                now=now,
            )
        else:
            hydrated = self._repository.hydrate_historical(
                user_id=context.user_id,
                vector_ids=hit_vector_ids,
            )
        memories_by_vector_id = {item.vector_id: item.memory for item in hydrated}
        if len(memories_by_vector_id) != len(hits):
            raise AuthorizationError("unauthorized_retrieval_result")

        candidates: list[RetrievalCandidate] = []
        relevance_exclusions: list[ContextExclusion] = []
        scores_by_memory_id: dict[str, float] = {}
        for hit in hits:
            memory = memories_by_vector_id.get(hit.vector_id)
            eligible = memory is not None and (
                is_historical_eligible(memory, user_id=context.user_id)
                if request.intent is RetrievalIntent.HISTORICAL
                else now is not None
                and is_current_state_eligible(
                    memory, user_id=context.user_id, now=now
                )
            )
            if not eligible or memory is None:
                raise AuthorizationError("unauthorized_retrieval_result")
            scores_by_memory_id[memory.memory_id] = hit.score
            if is_relevant(
                eligible=True,
                score=hit.score,
                threshold=self._relevance_threshold,
            ):
                candidates.append(
                    RetrievalCandidate(memory=memory, relevance=hit.score, eligible=True)
                )
            else:
                relevance_exclusions.append(
                    ContextExclusion(
                        memory_id=memory.memory_id,
                        reason=ContextExclusionReason.BELOW_RELEVANCE_THRESHOLD,
                    )
                )

        if not candidates:
            return RetrievalResult(
                memories=(),
                context="",
                tokenizer=_M1_TOKENIZER,
                token_budget=token_budget,
                tokens_used=0,
                included_memory_ids=(),
                exclusions=tuple(relevance_exclusions),
                outcome=RetrievalOutcome.NO_RELEVANT_MEMORY,
            )

        ranked = rank_candidates(candidates)
        try:
            selection = select_context(
                [candidate.memory for candidate in ranked],
                token_budget,
                self._token_counter.count_tokens,
            )
        except ValueError as error:
            raise ValidationError("invalid_tokenizer_result") from error
        selected = tuple(
            RetrievedMemory(
                memory=memory,
                score=scores_by_memory_id[memory.memory_id],
            )
            for memory in selection.selected_memories
        )
        outcome = (
            RetrievalOutcome.MEMORIES_SELECTED
            if selected
            else RetrievalOutcome.BUDGET_EXCLUDED
        )
        return RetrievalResult(
            memories=selected,
            context=selection.context,
            tokenizer=_M1_TOKENIZER,
            token_budget=token_budget,
            tokens_used=selection.tokens_used,
            included_memory_ids=tuple(memory.memory_id for memory in selection.selected_memories),
            exclusions=tuple(relevance_exclusions) + selection.exclusions,
            outcome=outcome,
        )

    def _retry_indexing(
        self,
        context: RequestContext,
        existing: ExistingAdmission,
        *,
        supersedes_memory_id: str | None,
    ) -> AdmissionResult:
        work = existing.indexing_work
        if work is None or existing.result.memory_id != work.memory_id:
            raise StorageError("Stored indexing work is unavailable")
        if existing.result.indexing_state is IndexingState.FAILED:
            self._repository.mark_pending(
                user_id=context.user_id,
                memory_id=work.memory_id,
            )
        elif existing.result.indexing_state is not IndexingState.PENDING:
            raise StorageError("Stored indexing state cannot be retried")
        if supersedes_memory_id is not None:
            publication_key = (
                context.user_id,
                work.memory_id,
                work.vector_id,
                supersedes_memory_id,
            )
            if publication_key in self._known_published_supersessions:
                return self._acknowledge_published_supersession(
                    context=context,
                    memory_id=work.memory_id,
                    vector_id=work.vector_id,
                    supersedes_memory_id=supersedes_memory_id,
                )
        return self._index_and_acknowledge(
            context=context,
            memory_id=work.memory_id,
            vector_id=work.vector_id,
            embedding=work.embedding,
            supersedes_memory_id=supersedes_memory_id,
        )

    def _index_and_acknowledge(
        self,
        *,
        context: RequestContext,
        memory_id: str,
        vector_id: int,
        embedding: Embedding,
        supersedes_memory_id: str | None = None,
    ) -> AdmissionResult:
        try:
            self._vector_index.add(vector_id=vector_id, embedding=embedding)
        except IndexingError as error:
            indexing_state = self._record_indexing_failure(context, memory_id, error)
            return AdmissionResult(
                decision=AdmissionDecision.ACCEPTED,
                reason="indexing_failed",
                memory_id=memory_id,
                indexing_state=indexing_state,
                retrievable=False,
                retryable_error=str(error),
            )

        if supersedes_memory_id is not None:
            publication_key = (
                context.user_id,
                memory_id,
                vector_id,
                supersedes_memory_id,
            )
            self._known_published_supersessions.add(publication_key)
            return self._acknowledge_published_supersession(
                context=context,
                memory_id=memory_id,
                vector_id=vector_id,
                supersedes_memory_id=supersedes_memory_id,
            )

        try:
            self._repository.mark_indexed(
                user_id=context.user_id,
                memory_id=memory_id,
            )
        except StorageError as error:
            return AdmissionResult(
                decision=AdmissionDecision.ACCEPTED,
                reason="indexing_acknowledgement_failed",
                memory_id=memory_id,
                indexing_state=IndexingState.PENDING,
                retrievable=False,
                retryable_error=str(error),
            )

        return AdmissionResult(
            decision=AdmissionDecision.ACCEPTED,
            reason="accepted_and_indexed",
            memory_id=memory_id,
            indexing_state=IndexingState.INDEXED,
            retrievable=True,
        )

    def _acknowledge_published_supersession(
        self,
        *,
        context: RequestContext,
        memory_id: str,
        vector_id: int,
        supersedes_memory_id: str,
    ) -> AdmissionResult:
        publication_key = (
            context.user_id,
            memory_id,
            vector_id,
            supersedes_memory_id,
        )
        try:
            self._repository.acknowledge_supersession(
                user_id=context.user_id,
                replacement_memory_id=memory_id,
                target_memory_id=supersedes_memory_id,
            )
        except StorageError as error:
            return AdmissionResult(
                decision=AdmissionDecision.ACCEPTED,
                reason="supersession_acknowledgement_failed",
                memory_id=memory_id,
                indexing_state=IndexingState.PENDING,
                retrievable=False,
                retryable_error=str(error),
            )

        self._known_published_supersessions.discard(publication_key)
        return AdmissionResult(
            decision=AdmissionDecision.ACCEPTED,
            reason="accepted_and_indexed",
            memory_id=memory_id,
            indexing_state=IndexingState.INDEXED,
            retrievable=True,
            supersedes_memory_ids=(supersedes_memory_id,),
        )

    @staticmethod
    def _validate_context(context: RequestContext) -> None:
        if not context.user_id.strip():
            raise ValidationError("invalid_user_id")
        if not context.request_id.strip():
            raise ValidationError("invalid_request_id")

    @staticmethod
    def _validate_retrieval_request(request: RetrievalRequest) -> tuple[str, int, int]:
        if not isinstance(request.query, str):
            raise ValidationError("invalid_retrieval_query")
        query = normalize_text(request.query)
        if not query:
            raise ValidationError("invalid_retrieval_query")
        if (
            isinstance(request.limit, bool)
            or not isinstance(request.limit, int)
            or request.limit <= 0
        ):
            raise ValidationError("invalid_retrieval_limit")
        if (
            isinstance(request.token_budget, bool)
            or not isinstance(request.token_budget, int)
            or request.token_budget < 0
        ):
            raise ValidationError("invalid_token_budget")
        return query, request.limit, request.token_budget

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
            supersedes_memory_id=request.supersedes_memory_id,
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
        memory_id: str,
        error: IndexingError,
    ) -> IndexingState:
        try:
            self._repository.mark_failed(
                user_id=context.user_id,
                memory_id=memory_id,
                reason=str(error),
            )
        except StorageError:
            return IndexingState.PENDING
        return IndexingState.FAILED
