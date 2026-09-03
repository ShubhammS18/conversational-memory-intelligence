"""Command-line boundary for the local memory-layer reference implementation."""

from __future__ import annotations

import argparse
import json
import os
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


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and return a process exit code."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "demo-first-slice":
        return _demo_first_slice()
    if arguments.command == "demo-current-state":
        return _demo_current_state()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
