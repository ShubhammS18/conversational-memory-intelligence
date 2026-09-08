"""Command-line boundary for the local memory-layer reference implementation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser without initializing any memory infrastructure."""
    parser = argparse.ArgumentParser(
        prog="conversational-memory",
        description="Local conversational memory layer",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser(
        "demo-first-slice",
        help="Run the real persisted M1 admission, restart, and retrieval demonstration",
    )
    subcommands.add_parser(
        "demo-current-state",
        help="Run the real M2 current-state eligibility demonstration",
    )
    subcommands.add_parser(
        "demo-no-memory",
        help="Run the real M3 explicit no-relevant-memory demonstration",
    )
    subcommands.add_parser(
        "demo-supersession",
        help="Run the real M4 explicit supersession and restart demonstration",
    )
    subcommands.add_parser(
        "demo-history",
        help="Run the real M5 current-versus-historical retrieval demonstration",
    )
    subcommands.add_parser(
        "demo-expiration",
        help="Run the real M6 trusted-clock expiration demonstration",
    )
    return parser


class _UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _UuidMemoryIds:
    def new_id(self) -> str:
        return str(uuid.uuid4())


class _SequenceMemoryIds:
    def __init__(self, *memory_ids: str) -> None:
        self._memory_ids = iter(memory_ids)

    def new_id(self) -> str:
        return next(self._memory_ids)


def _model_cache_directory() -> Path:
    configured = os.environ.get("CONVERSATIONAL_MEMORY_MODEL_CACHE")
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "huggingface" / "hub"


def _demo_first_slice() -> int:
    from conversational_memory.application import (
        AdmissionRequest,
        RequestContext,
        RetrievalRequest,
    )
    from conversational_memory.composition import compose_local_memory_service

    model_cache_directory = _model_cache_directory()
    request = AdmissionRequest(
        idempotency_key="demo-turn-1",
        conversation_id="demo-conversation-1",
        turn_id="demo-turn-1",
        content="I prefer SQLite as the authoritative local memory store.",
        memory_type="preference",
        subject="authoritative memory store",
        value="SQLite",
        source_type="explicit_user",
    )

    with tempfile.TemporaryDirectory(prefix="conversational-memory-m1-") as temporary:
        root = Path(temporary)
        database_path = root / "memory.sqlite3"
        index_directory = root / "index"
        service = compose_local_memory_service(
            database_path=database_path,
            index_directory=index_directory,
            model_cache_directory=model_cache_directory,
            clock=_UtcClock(),
            memory_ids=_UuidMemoryIds(),
            create_index_if_missing=True,
            relevance_threshold=0.50,
        )
        owner = RequestContext(user_id="demo-user", request_id="demo-admit")
        admitted = service.admit(owner, request)
        replayed = service.admit(
            RequestContext(user_id="demo-user", request_id="demo-replay"),
            request,
        )

        restarted = compose_local_memory_service(
            database_path=database_path,
            index_directory=index_directory,
            model_cache_directory=model_cache_directory,
            clock=_UtcClock(),
            memory_ids=_UuidMemoryIds(),
            relevance_threshold=0.50,
        )
        retrieval = restarted.retrieve(
            RequestContext(user_id="demo-user", request_id="demo-retrieve"),
            RetrievalRequest(
                query="Which local memory store do I prefer?",
                limit=5,
                token_budget=128,
            ),
        )
        other_user = restarted.retrieve(
            RequestContext(user_id="other-user", request_id="demo-isolation"),
            RetrievalRequest(
                query="Which local memory store does demo-user prefer?",
                limit=5,
                token_budget=128,
            ),
        )

        if (
            not admitted.retrievable
            or admitted.memory_id is None
            or admitted.indexing_state is None
        ):
            raise RuntimeError("M1 demo admission did not become retrievable")
        if replayed != admitted:
            raise RuntimeError("M1 demo idempotent replay changed the admission result")
        if retrieval.included_memory_ids != (admitted.memory_id,):
            raise RuntimeError("M1 demo restart did not retrieve the admitted memory")
        if retrieval.tokens_used > retrieval.token_budget:
            raise RuntimeError("M1 demo context exceeded its memory token allowance")
        if other_user.memories or other_user.context:
            raise RuntimeError("M1 demo owner isolation failed")

        output = {
            "admission": {
                "decision": admitted.decision.value,
                "reason": admitted.reason,
                "memory_id": admitted.memory_id,
                "indexing_state": admitted.indexing_state.value,
                "retrievable": admitted.retrievable,
            },
            "idempotent_replay": {
                "same_result": replayed == admitted,
                "memory_id": replayed.memory_id,
            },
            "restart_retrieval": {
                "selected_memory_ids": list(retrieval.included_memory_ids),
                "context": retrieval.context,
                "tokenizer": retrieval.tokenizer,
                "token_budget": retrieval.token_budget,
                "tokens_used": retrieval.tokens_used,
                "exclusions": [
                    {
                        "memory_id": exclusion.memory_id,
                        "reason": exclusion.reason.value,
                    }
                    for exclusion in retrieval.exclusions
                ],
            },
            "owner_isolation": {
                "other_user_memory_count": len(other_user.memories),
                "other_user_context": other_user.context,
            },
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _demo_current_state() -> int:
    from datetime import timedelta

    from conversational_memory.application import (
        AdmissionRequest,
        RequestContext,
        RetrievalRequest,
    )
    from conversational_memory.composition import compose_local_memory_service

    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    scenarios = (
        ("current", now - timedelta(days=1), now + timedelta(days=1)),
        ("ended", now - timedelta(days=1), now),
        ("future", now + timedelta(days=1), None),
    )
    with tempfile.TemporaryDirectory(prefix="conversational-memory-m2-") as temporary:
        root = Path(temporary)
        service = compose_local_memory_service(
            database_path=root / "memory.sqlite3",
            index_directory=root / "index",
            model_cache_directory=_model_cache_directory(),
            clock=_FixedClock(now),
            memory_ids=_UuidMemoryIds(),
            create_index_if_missing=True,
            relevance_threshold=0.50,
        )
        admissions = {}
        for name, valid_from, valid_until in scenarios:
            admissions[name] = service.admit(
                RequestContext(user_id="demo-user", request_id=f"admit-{name}"),
                AdmissionRequest(
                    idempotency_key=f"m2-{name}",
                    conversation_id="m2-demo-conversation",
                    turn_id=f"m2-{name}",
                    content=f"M2 {name} memory.",
                    memory_type="fact",
                    subject="current-state-demo",
                    value=name,
                    source_type="explicit_user",
                    valid_from=valid_from,
                    valid_until=valid_until,
                ),
            )
        result = service.retrieve(
            RequestContext(user_id="demo-user", request_id="retrieve-current-state"),
            RetrievalRequest(query="M2 memory", limit=10, token_budget=128),
        )
        current_id = admissions["current"].memory_id
        if current_id is None or result.included_memory_ids != (current_id,):
            raise RuntimeError("M2 demo current-state filtering failed")

        output = {
            "trusted_now": now.isoformat(),
            "selected_memory_ids": list(result.included_memory_ids),
            "current_memory_id": current_id,
            "excluded_seed_ids": {
                name: admission.memory_id
                for name, admission in admissions.items()
                if name != "current"
            },
            "token_budget": result.token_budget,
            "tokens_used": result.tokens_used,
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _demo_no_memory() -> int:
    from conversational_memory.application import (
        AdmissionRequest,
        RequestContext,
        RetrievalOutcome,
        RetrievalRequest,
    )
    from conversational_memory.composition import compose_local_memory_service

    relevance_threshold = 0.50
    query = "What is my favorite programming language?"
    with tempfile.TemporaryDirectory(prefix="conversational-memory-m3-") as temporary:
        root = Path(temporary)
        service = compose_local_memory_service(
            database_path=root / "memory.sqlite3",
            index_directory=root / "index",
            model_cache_directory=_model_cache_directory(),
            clock=_UtcClock(),
            memory_ids=_UuidMemoryIds(),
            create_index_if_missing=True,
            relevance_threshold=relevance_threshold,
        )
        admission = service.admit(
            RequestContext(user_id="demo-user", request_id="m3-admit"),
            AdmissionRequest(
                idempotency_key="m3-unrelated-memory",
                conversation_id="machine-learning-project",
                turn_id="m3-unrelated-memory",
                content="I prefer FAISS for vector search in machine learning projects.",
                memory_type="preference",
                subject="vector search",
                value="FAISS",
                source_type="explicit_user",
            ),
        )
        result = service.retrieve(
            RequestContext(user_id="demo-user", request_id="m3-retrieve"),
            RetrievalRequest(query=query, limit=5, token_budget=128),
        )
        if admission.memory_id is None or not admission.retrievable:
            raise RuntimeError("M3 demo memory did not become retrievable")
        if result.outcome is not RetrievalOutcome.NO_RELEVANT_MEMORY:
            raise RuntimeError("M3 demo did not return no_relevant_memory")
        if result.memories or result.context or result.included_memory_ids:
            raise RuntimeError("M3 demo forced an unrelated memory into context")
        if len(result.exclusions) != 1 or (
            result.exclusions[0].memory_id != admission.memory_id
            or result.exclusions[0].reason.value != "below_relevance_threshold"
        ):
            raise RuntimeError("M3 demo relevance exclusion evidence is invalid")

        output = {
            "query": query,
            "relevance_threshold": relevance_threshold,
            "outcome": result.outcome.value,
            "selected_memory_ids": list(result.included_memory_ids),
            "context": result.context,
            "token_budget": result.token_budget,
            "tokens_used": result.tokens_used,
            "exclusions": [
                {
                    "memory_id": exclusion.memory_id,
                    "reason": exclusion.reason.value,
                }
                for exclusion in result.exclusions
            ],
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _demo_supersession() -> int:
    from conversational_memory.application import (
        AdmissionRequest,
        RequestContext,
        RetrievalRequest,
        ValidationError,
    )
    from conversational_memory.composition import compose_local_memory_service

    now = datetime(2026, 9, 6, 12, tzinfo=UTC)
    original_request = AdmissionRequest(
        idempotency_key="m4-original",
        conversation_id="m4-demo-conversation",
        turn_id="m4-original",
        content="I prefer FAISS for vector database search.",
        memory_type="preference",
        subject="vector database",
        value="FAISS",
        source_type="explicit_user",
    )
    replacement_request = AdmissionRequest(
        idempotency_key="m4-replacement",
        conversation_id="m4-demo-conversation",
        turn_id="m4-replacement",
        content="I prefer PostgreSQL for vector database search.",
        memory_type="preference",
        subject="vector database",
        value="PostgreSQL",
        source_type="explicit_user",
        supersedes_memory_id="m4-original-memory",
    )
    ambiguous_request = AdmissionRequest(
        idempotency_key="m4-ambiguous",
        conversation_id="m4-demo-conversation",
        turn_id="m4-ambiguous",
        content="Perhaps another vector database might be preferable.",
        memory_type="preference",
        subject="vector database",
        value="uncertain",
        source_type="inferred",
        supersedes_memory_id="m4-replacement-memory",
    )

    with tempfile.TemporaryDirectory(prefix="conversational-memory-m4-") as temporary:
        root = Path(temporary)
        database_path = root / "memory.sqlite3"
        index_directory = root / "index"
        service = compose_local_memory_service(
            database_path=database_path,
            index_directory=index_directory,
            model_cache_directory=_model_cache_directory(),
            clock=_FixedClock(now),
            memory_ids=_SequenceMemoryIds(
                "m4-original-memory",
                "m4-replacement-memory",
            ),
            create_index_if_missing=True,
            relevance_threshold=0.50,
        )
        owner = RequestContext(user_id="demo-user", request_id="m4-original")
        original = service.admit(owner, original_request)
        replacement = service.admit(
            RequestContext(user_id="demo-user", request_id="m4-replacement"),
            replacement_request,
        )

        restarted = compose_local_memory_service(
            database_path=database_path,
            index_directory=index_directory,
            model_cache_directory=_model_cache_directory(),
            clock=_FixedClock(now),
            memory_ids=_SequenceMemoryIds("m4-ambiguous-memory"),
            relevance_threshold=0.50,
        )
        replayed_original = restarted.admit(
            RequestContext(user_id="demo-user", request_id="m4-original-replay"),
            original_request,
        )
        replayed_replacement = restarted.admit(
            RequestContext(user_id="demo-user", request_id="m4-replacement-replay"),
            replacement_request,
        )
        before_ambiguous = restarted.retrieve(
            RequestContext(user_id="demo-user", request_id="m4-before-ambiguous"),
            RetrievalRequest(
                query=replacement_request.content,
                limit=5,
                token_budget=128,
            ),
        )
        ambiguous_reason = ""
        try:
            restarted.admit(
                RequestContext(user_id="demo-user", request_id="m4-ambiguous"),
                ambiguous_request,
            )
        except ValidationError as error:
            ambiguous_reason = error.reason
        after_ambiguous = restarted.retrieve(
            RequestContext(user_id="demo-user", request_id="m4-after-ambiguous"),
            RetrievalRequest(
                query=replacement_request.content,
                limit=5,
                token_budget=128,
            ),
        )

        if not original.retrievable or original.memory_id != "m4-original-memory":
            raise RuntimeError("M4 demo original admission failed")
        if not replacement.retrievable or replacement.memory_id != "m4-replacement-memory":
            raise RuntimeError("M4 demo replacement admission failed")
        if replayed_original.memory_id != original.memory_id:
            raise RuntimeError("M4 demo restart lost the original record")
        if replayed_replacement != replacement:
            raise RuntimeError("M4 demo restart lost the replacement record")
        if before_ambiguous.included_memory_ids != (replacement.memory_id,):
            raise RuntimeError("M4 demo did not select only the replacement")
        if replayed_replacement.supersedes_memory_ids != (original.memory_id,):
            raise RuntimeError("M4 demo replacement relationship was not durable")
        if replayed_original.superseded_by_memory_id != replacement.memory_id:
            raise RuntimeError("M4 demo original relationship was not durable")
        atomic_bidirectional_commit = (
            replayed_replacement.supersedes_memory_ids == (original.memory_id,)
            and replayed_original.superseded_by_memory_id == replacement.memory_id
        )
        if ambiguous_reason != "invalid_supersession_target":
            raise RuntimeError("M4 demo ambiguous target was not rejected")
        if after_ambiguous != before_ambiguous:
            raise RuntimeError("M4 demo ambiguous input mutated current retrieval")

        output = {
            "original_admission": {
                "memory_id": original.memory_id,
                "indexed": original.retrievable,
            },
            "replacement_admission": {
                "memory_id": replacement.memory_id,
                "indexed": replacement.retrievable,
            },
            "relationships": {
                "replacement_supersedes": list(
                    replayed_replacement.supersedes_memory_ids
                ),
                "original_superseded_by": replayed_original.superseded_by_memory_id,
                "atomic_bidirectional_commit": atomic_bidirectional_commit,
            },
            "restart": {
                "original_record_preserved": replayed_original.memory_id
                == original.memory_id,
                "replacement_record_preserved": replayed_replacement == replacement,
            },
            "current_state_retrieval": {
                "selected_memory_ids": list(before_ambiguous.included_memory_ids),
                "context": before_ambiguous.context,
            },
            "ambiguous_target": {
                "rejection_reason": ambiguous_reason,
                "current_state_unchanged": after_ambiguous == before_ambiguous,
            },
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _demo_history() -> int:
    from conversational_memory.application import (
        AdmissionRequest,
        RequestContext,
        RetrievalIntent,
        RetrievalRequest,
    )
    from conversational_memory.composition import compose_local_memory_service

    now = datetime(2026, 9, 7, 12, tzinfo=UTC)
    original_request = AdmissionRequest(
        idempotency_key="m5-original",
        conversation_id="m5-demo-conversation",
        turn_id="m5-original",
        content="I prefer FAISS for vector database search.",
        memory_type="preference",
        subject="vector database",
        value="FAISS",
        source_type="explicit_user",
    )
    replacement_request = AdmissionRequest(
        idempotency_key="m5-replacement",
        conversation_id="m5-demo-conversation",
        turn_id="m5-replacement",
        content="I prefer PostgreSQL for vector database search.",
        memory_type="preference",
        subject="vector database",
        value="PostgreSQL",
        source_type="explicit_user",
        supersedes_memory_id="m5-original-memory",
    )
    deleted_request = AdmissionRequest(
        idempotency_key="m5-deleted",
        conversation_id="m5-demo-conversation",
        turn_id="m5-deleted",
        content="I prefer FAISS for vector database search.",
        memory_type="preference",
        subject="deleted vector database",
        value="FAISS",
        source_type="explicit_user",
    )
    other_owner_request = AdmissionRequest(
        idempotency_key="m5-other-owner",
        conversation_id="m5-demo-conversation",
        turn_id="m5-other-owner",
        content="I prefer FAISS for vector database search.",
        memory_type="preference",
        subject="vector database",
        value="FAISS",
        source_type="explicit_user",
    )

    with tempfile.TemporaryDirectory(prefix="conversational-memory-m5-") as temporary:
        root = Path(temporary)
        database_path = root / "memory.sqlite3"
        index_directory = root / "index"
        service = compose_local_memory_service(
            database_path=database_path,
            index_directory=index_directory,
            model_cache_directory=_model_cache_directory(),
            clock=_FixedClock(now),
            memory_ids=_SequenceMemoryIds(
                "m5-original-memory",
                "m5-replacement-memory",
                "m5-deleted-memory",
                "m5-other-owner-memory",
            ),
            create_index_if_missing=True,
            relevance_threshold=0.50,
        )
        owner = RequestContext(user_id="demo-user", request_id="m5-original")
        original = service.admit(owner, original_request)
        replacement = service.admit(
            RequestContext(user_id="demo-user", request_id="m5-replacement"),
            replacement_request,
        )
        deleted = service.admit(
            RequestContext(user_id="demo-user", request_id="m5-deleted"),
            deleted_request,
        )
        other_owner = service.admit(
            RequestContext(user_id="other-user", request_id="m5-other-owner"),
            other_owner_request,
        )
        if deleted.memory_id is None:
            raise RuntimeError("M5 demo deleted seed admission failed")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE memories SET deleted_at = ? WHERE memory_id = ?",
                (now.isoformat(), deleted.memory_id),
            )

        restarted = compose_local_memory_service(
            database_path=database_path,
            index_directory=index_directory,
            model_cache_directory=_model_cache_directory(),
            clock=_FixedClock(now),
            memory_ids=_SequenceMemoryIds(),
            relevance_threshold=0.50,
        )
        state_query = """
            SELECT memory_id, lifecycle_status, supersedes_json, superseded_by,
                   deleted_at
            FROM memories ORDER BY memory_id
        """
        with sqlite3.connect(database_path) as connection:
            before = connection.execute(state_query).fetchall()

        current = restarted.retrieve(
            RequestContext(user_id="demo-user", request_id="m5-current"),
            RetrievalRequest(
                query=replacement_request.content,
                limit=10,
                token_budget=256,
            ),
        )
        historical = restarted.retrieve(
            RequestContext(user_id="demo-user", request_id="m5-historical"),
            RetrievalRequest(
                query=original_request.content,
                limit=10,
                token_budget=256,
                intent=RetrievalIntent.HISTORICAL,
            ),
        )
        with sqlite3.connect(database_path) as connection:
            after = connection.execute(state_query).fetchall()

        if original.memory_id is None or replacement.memory_id is None:
            raise RuntimeError("M5 demo supersession admissions failed")
        if current.included_memory_ids != (replacement.memory_id,):
            raise RuntimeError("M5 demo current retrieval did not select only replacement")
        historical_by_id = {
            item.memory.memory_id: item.memory for item in historical.memories
        }
        historical_original = historical_by_id.get(original.memory_id)
        if historical_original is None:
            raise RuntimeError("M5 demo historical retrieval lost superseded original")
        if historical_original.lifecycle_status.value != "superseded":
            raise RuntimeError("M5 demo historical lifecycle metadata is inaccurate")
        excluded_ids = {deleted.memory_id, other_owner.memory_id}
        if excluded_ids.intersection(historical.included_memory_ids):
            raise RuntimeError("M5 demo historical isolation or deletion failed")
        if before != after:
            raise RuntimeError("M5 demo retrieval mutated lifecycle relationships")
        if current.tokens_used > current.token_budget:
            raise RuntimeError("M5 demo current context exceeded its token budget")
        if historical.tokens_used > historical.token_budget:
            raise RuntimeError("M5 demo historical context exceeded its token budget")

        output = {
            "restart": {
                "durable_store_reopened": True,
            },
            "current_retrieval": {
                "selected_memory_ids": list(current.included_memory_ids),
                "token_budget": current.token_budget,
                "tokens_used": current.tokens_used,
            },
            "historical_retrieval": {
                "selected_memory_ids": list(historical.included_memory_ids),
                "memories": [
                    {
                        "memory_id": item.memory.memory_id,
                        "lifecycle_status": item.memory.lifecycle_status.value,
                        "superseded_by": item.memory.superseded_by,
                    }
                    for item in historical.memories
                ],
                "token_budget": historical.token_budget,
                "tokens_used": historical.tokens_used,
            },
            "exclusions": {
                "deleted_memory_absent": deleted.memory_id
                not in historical.included_memory_ids,
                "other_owner_memory_absent": other_owner.memory_id
                not in historical.included_memory_ids,
            },
            "read_only": {
                "lifecycle_and_relationship_state_unchanged": before == after,
                "original_remains_superseded": historical_original.superseded_by
                == replacement.memory_id,
            },
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _demo_expiration() -> int:
    from datetime import timedelta

    from conversational_memory.application import (
        AdmissionRequest,
        RequestContext,
        RetrievalIntent,
        RetrievalRequest,
    )
    from conversational_memory.composition import compose_local_memory_service

    boundary = datetime(2026, 9, 8, 12, tzinfo=UTC)
    before = boundary - timedelta(microseconds=1)
    after = boundary + timedelta(microseconds=1)
    token_budget = 256
    original_request = AdmissionRequest(
        idempotency_key="m6-original",
        conversation_id="m6-demo-conversation",
        turn_id="m6-original",
        content="The deployment window remains open.",
        memory_type="fact",
        subject="deployment window",
        value="open",
        source_type="explicit_user",
    )
    replacement_request = AdmissionRequest(
        idempotency_key="m6-expiring",
        conversation_id="m6-demo-conversation",
        turn_id="m6-expiring",
        content="The deployment window closes at noon UTC.",
        memory_type="fact",
        subject="deployment window",
        value="closed at noon UTC",
        source_type="explicit_user",
        valid_from=before,
        valid_until=boundary,
        supersedes_memory_id="m6-original-memory",
    )

    class _UnreadClock:
        def __init__(self) -> None:
            self.read = False

        def now(self) -> datetime:
            self.read = True
            raise RuntimeError("historical retrieval read the trusted clock")

    def index_hashes(directory: Path) -> dict[str, str]:
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.iterdir())
            if path.is_file()
        }

    with tempfile.TemporaryDirectory(prefix="conversational-memory-m6-") as temporary:
        root = Path(temporary)
        database_path = root / "memory.sqlite3"
        index_directory = root / "index"
        service_before = compose_local_memory_service(
            database_path=database_path,
            index_directory=index_directory,
            model_cache_directory=_model_cache_directory(),
            clock=_FixedClock(before),
            memory_ids=_SequenceMemoryIds(
                "m6-original-memory",
                "m6-expiring-memory",
            ),
            create_index_if_missing=True,
            relevance_threshold=0.50,
        )
        original = service_before.admit(
            RequestContext(user_id="demo-user", request_id="m6-original"),
            original_request,
        )
        replacement = service_before.admit(
            RequestContext(user_id="demo-user", request_id="m6-expiring"),
            replacement_request,
        )
        query = replacement_request.content
        before_result = service_before.retrieve(
            RequestContext(user_id="demo-user", request_id="m6-before"),
            RetrievalRequest(query=query, limit=10, token_budget=token_budget),
        )
        faiss_before = index_hashes(index_directory)

        service_at = compose_local_memory_service(
            database_path=database_path,
            index_directory=index_directory,
            model_cache_directory=_model_cache_directory(),
            clock=_FixedClock(boundary),
            memory_ids=_SequenceMemoryIds(),
            relevance_threshold=0.50,
        )
        at_result = service_at.retrieve(
            RequestContext(user_id="demo-user", request_id="m6-at"),
            RetrievalRequest(query=query, limit=10, token_budget=token_budget),
        )

        service_after_restart = compose_local_memory_service(
            database_path=database_path,
            index_directory=index_directory,
            model_cache_directory=_model_cache_directory(),
            clock=_FixedClock(after),
            memory_ids=_SequenceMemoryIds(),
            relevance_threshold=0.50,
        )
        after_result = service_after_restart.retrieve(
            RequestContext(user_id="demo-user", request_id="m6-after"),
            RetrievalRequest(query=query, limit=10, token_budget=token_budget),
        )
        faiss_after = index_hashes(index_directory)

        state_query = """
            SELECT memory_id, lifecycle_status, supersedes_json, superseded_by,
                   deleted_at
            FROM memories ORDER BY memory_id
        """
        with sqlite3.connect(database_path) as connection:
            before_history = connection.execute(state_query).fetchall()
        states = {str(row[0]): row for row in before_history}
        original_state = states["m6-original-memory"]
        replacement_state = states["m6-expiring-memory"]

        unread_clock = _UnreadClock()
        historical_service = compose_local_memory_service(
            database_path=database_path,
            index_directory=index_directory,
            model_cache_directory=_model_cache_directory(),
            clock=unread_clock,
            memory_ids=_SequenceMemoryIds(),
            relevance_threshold=0.50,
        )
        historical = historical_service.retrieve(
            RequestContext(user_id="demo-user", request_id="m6-history"),
            RetrievalRequest(
                query=query,
                limit=10,
                token_budget=token_budget,
                intent=RetrievalIntent.HISTORICAL,
            ),
        )
        with sqlite3.connect(database_path) as connection:
            after_history = connection.execute(state_query).fetchall()

        if original.memory_id is None or replacement.memory_id is None:
            raise RuntimeError("M6 demo admissions failed")
        if before_result.included_memory_ids != (replacement.memory_id,):
            raise RuntimeError("M6 demo memory was not current before its end")
        if at_result.included_memory_ids or after_result.included_memory_ids:
            raise RuntimeError("M6 demo expired memory remained current")
        if replacement_state[1] != "expired":
            raise RuntimeError("M6 demo did not persist expiration")
        relationships_unchanged = (
            json.loads(str(replacement_state[2])) == [original.memory_id]
            and original_state[3] == replacement.memory_id
        )
        if not relationships_unchanged or faiss_before != faiss_after:
            raise RuntimeError("M6 demo expiration changed relationships or FAISS")
        if replacement.memory_id not in historical.included_memory_ids:
            raise RuntimeError("M6 demo history did not return the expired memory")
        if unread_clock.read or before_history != after_history:
            raise RuntimeError("M6 demo historical retrieval was not read-only")
        if before_result.tokens_used > before_result.token_budget:
            raise RuntimeError("M6 demo current context exceeded its token budget")
        if historical.tokens_used > historical.token_budget:
            raise RuntimeError("M6 demo historical context exceeded its token budget")

        output = {
            "trusted_times": {
                "before": before.isoformat(),
                "boundary": boundary.isoformat(),
                "after": after.isoformat(),
            },
            "boundary": {
                "before_current": list(before_result.included_memory_ids),
                "at_current": list(at_result.included_memory_ids),
                "after_restart_current": list(after_result.included_memory_ids),
                "persisted_lifecycle_status": replacement_state[1],
            },
            "preservation": {
                "faiss_generation_unchanged": faiss_before == faiss_after,
                "replacement_supersedes": json.loads(str(replacement_state[2])),
                "original_superseded_by": original_state[3],
                "relationships_unchanged": relationships_unchanged,
            },
            "historical": {
                "selected_memory_ids": list(historical.included_memory_ids),
                "expired_memory_returned": replacement.memory_id
                in historical.included_memory_ids,
                "clock_read": unread_clock.read,
                "state_unchanged": before_history == after_history,
                "remained_expired": replacement_state[1] == "expired",
                "token_budget": historical.token_budget,
                "tokens_used": historical.tokens_used,
            },
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and return a process exit code."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "demo-first-slice":
        return _demo_first_slice()
    if arguments.command == "demo-current-state":
        return _demo_current_state()
    if arguments.command == "demo-no-memory":
        return _demo_no_memory()
    if arguments.command == "demo-supersession":
        return _demo_supersession()
    if arguments.command == "demo-history":
        return _demo_history()
    if arguments.command == "demo-expiration":
        return _demo_expiration()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
