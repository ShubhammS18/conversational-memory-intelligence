"""Fixed-workload contracts and bounded real primary-path evaluation."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import cast

from conversational_memory.application import (
    AdmissionRequest,
    AdmissionResult,
    CaptureEventSink,
    EventName,
    EventStage,
    ForgetRequest,
    MemoryService,
    RequestContext,
    RetrievalIntent,
    RetrievalRequest,
    RetrievalResult,
)


class CaseId(StrEnum):
    CASE1 = "case1"
    CASE2 = "case2"
    CASE3 = "case3"
    CASE4 = "case4"
    CASE5 = "case5"
    CASE6 = "case6"


class Verdict(StrEnum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"


class ReasonCode(StrEnum):
    EXPECTED_BEHAVIOR_CONFIRMED = "expected_behavior_confirmed"
    EXPECTED_MEMORY_NOT_SELECTED = "expected_memory_not_selected"
    EXTRA_MEMORY_SELECTED = "extra_memory_selected"
    UNEXPECTED_EMPTY_OUTCOME = "unexpected_empty_outcome"
    SAFETY_VIOLATION = "safety_violation"
    FIXTURE_MISMATCH = "fixture_mismatch"
    BASELINE_MISMATCH = "baseline_mismatch"
    PREREQUISITE_UNAVAILABLE = "prerequisite_unavailable"
    REQUIRED_STATE_FAILURE = "required_state_failure"
    UNEXPECTED_OPERATION_FAILURE = "unexpected_operation_failure"
    NON_REPRODUCIBLE_RESULT = "non_reproducible_result"


class CheckId(StrEnum):
    FIXTURE_BYTES = "fixture_bytes"
    BASELINE_BYTES = "baseline_bytes"
    CANDIDATE_ACCOUNTING = "candidate_accounting"
    ADMISSION_STATES = "admission_states"
    EXPLICIT_LINKS = "explicit_links"
    SENSITIVE_REJECTED = "sensitive_rejected"
    REJECTED_TARGET_NOT_FOUND = "rejected_target_not_found"
    RESTART_VERIFIED = "restart_verified"
    CURRENT_OWNER_SCOPE = "current_owner_scope"
    INDEXED_ONLY = "indexed_only"
    CURRENT_STATE_ONLY = "current_state_only"
    TOMBSTONES_ABSENT = "tombstones_absent"
    STALE_TARGETS_ABSENT = "stale_targets_absent"
    SENSITIVE_ABSENT = "sensitive_absent"
    APARTMENT_ABSENT = "apartment_absent"
    RELEVANCE_BOUNDARY = "relevance_boundary"
    COMPLETE_BLOCKS = "complete_blocks"
    EXACT_TOKENS = "exact_tokens"
    BUDGET_BOUND = "budget_bound"
    RESULT_CONSISTENCY = "result_consistency"
    EXPECTED_PRIMARY_IDS = "expected_primary_ids"
    EXPECTED_EMPTY_OUTCOME = "expected_empty_outcome"
    ZERO_BUDGET_EMPTY = "zero_budget_empty"
    ZERO_BUDGET_OUTCOME = "zero_budget_outcome"
    REPEATABLE = "repeatable"
    FORGETTING_COMPLETE = "forgetting_complete"
    DELETED_CURRENT_ABSENT = "deleted_current_absent"
    DELETED_HISTORICAL_ABSENT = "deleted_historical_absent"
    NO_REACTIVATION = "no_reactivation"
    REPORT_PRIVACY = "report_privacy"


_FILES = (
    "case1_irrelevant_contradictory.json", "case2_preference_change.json",
    "case3_long_context.json", "case4_multi_user.json",
    "case5_sensitive_memory.json", "case6_cold_start.json",
)
_HASHES = (
    "c674bdb684b1f6699954fb984f42d106b8c2c5c2be30a81c07c5463ac70a3b98",
    "1ae1b665c66381d7c49e3a8bf2004a4bf6b86be3fdb0d4f17aec3b9783ddae2d",
    "7d1a5beb79f7f8e7aa44a9376a016dc0e1bf00778fbace70270498977e427762",
    "51e2f14e49917da2dffd3afe835d4bedfed5178e3820fee108cc2d241aa37917",
    "24d03d0811f68c53221be7084ae6b6f77d2375cf79402538d807d45d62b6b768",
    "2dac46c31aadef03800903b328151aabc0193ff3be7245e2c165bd5863560a48",
)
_BASELINE_HASHES = (
    ("baseline_protocol.md", "8731f90878db8aa656f3cc872b9856fd3c63661cc6d638c71cfe46c6094cc6e3"),
    ("baseline_results.csv", "8fff27539660715a09b9f83f9e61f1a0bc9405630a1c708b8f854f5a9cb857f5"),
)
_BASELINE_COUNTS = (18, 3, 50, 17, 17, 17)
_BASELINE_FAILURES = (
    "contradictory_memories_retrieved", "old_and_new_decisions_retrieved",
    "multiple_historical_architectural_decisions_retrieved", "different_users_memory_retrieved",
    "sensitive_memory_remains_retrievable_after_forget_request",
    "unrelated_memories_retrieved_for_cold_start",
)
_USER_COUNTS = (3, 3, 50, 2, 1, 2)
_FILLER_COUNTS = (15, 0, 0, 15, 15, 15)
_CONVERSATION_INDICES = ((0, 2, 4), (0, 2, 4), tuple(range(50)), (0, 2), (0,), (0, 2))
_SETTINGS = (
    "sentence-transformers/all-mpnet-base-v2", "e8c3b32edf5434bc2275fc9bab85f82640a19130",
    768, "cpu", 0.50, 10, 128, "cl100k_base", "2026-01-02T00:00:00Z", 10,
)
_NOTES = (
    "Typed metadata, trusted identities, and explicit supersession targets are evaluation-only.",
    "Case2 preserves its original lack of padding.",
    "Case5 treats the forget instruction as a control action, not an admission.",
    "Case6 probes relevance using the populated same owner.",
    "Baseline failures are historical; latency is not compared.",
)


class EvaluationInputError(ValueError):
    """Closed, redacted input error; never includes a path or input bytes."""

    def __init__(self, reason: ReasonCode) -> None:
        if reason not in (ReasonCode.FIXTURE_MISMATCH, ReasonCode.BASELINE_MISMATCH):
            raise ValueError("invalid_evaluation_input_error")
        self.reason = reason
        super().__init__(reason.value)


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid_evaluation_report")


def _count(value: object) -> None:
    _require(type(value) is int and value >= 0)


def _index(case_id: CaseId) -> int:
    _require(type(case_id) is CaseId)
    return tuple(CaseId).index(case_id)


def _memory_case(memory_id: str) -> CaseId:
    _require(type(memory_id) is str)
    match = re.fullmatch(r"m10-(case[1-6])-([cf])([0-9]{4})", memory_id)
    _require(match is not None)
    assert match is not None
    case_id = CaseId(match[1])
    index = _index(case_id)
    number = int(match[3])
    _require(number in _CONVERSATION_INDICES[index] if match[2] == "c"
             else number < _FILLER_COUNTS[index])
    return case_id


@dataclass(frozen=True, slots=True)
class WorkloadTurn:
    role: str
    content: str = field(repr=False)
    user_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class WorkloadCase:
    case_id: CaseId
    sha256: str
    raw_bytes: bytes = field(repr=False)
    description: str = field(repr=False)
    expected_failure: str = field(repr=False)
    expected_improvement: str = field(repr=False)
    conversation: tuple[WorkloadTurn, ...]
    filler_memories: tuple[str, ...] = field(repr=False)
    query: str = field(repr=False)
    query_user_id: str | None = field(repr=False)
    expected_retrieval_issue: tuple[str, ...] = field(repr=False)
    signal_memory_positions: tuple[int, ...]
    random_seed: int | None
    total_memories: int | None


@dataclass(frozen=True, slots=True)
class BaselineEvidence:
    case_id: CaseId
    memory_count: int
    retrieved_count: int
    failures_observed: int
    failure_names: str

    def __post_init__(self) -> None:
        index = _index(self.case_id)
        for value in (self.memory_count, self.retrieved_count, self.failures_observed):
            _count(value)
        _require((self.memory_count, self.retrieved_count, self.failures_observed,
                  self.failure_names) == (_BASELINE_COUNTS[index],
                  min(10, _BASELINE_COUNTS[index]), 1, _BASELINE_FAILURES[index]))
        _require(type(self.failure_names) is str)


@dataclass(frozen=True, slots=True)
class FixedWorkload:
    cases: tuple[WorkloadCase, ...]
    baseline: tuple[BaselineEvidence, ...]


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


def _shape(value: object, required: set[str], optional: set[str] | None = None
           ) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError("invalid_input_shape")
    obj = cast(dict[str, object], value)
    if not required <= obj.keys() or not obj.keys() <= required | (optional or set()):
        raise ValueError("invalid_input_shape")
    return obj


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("invalid_input_shape")
    return value


def _texts(value: object) -> tuple[str, ...]:
    if type(value) is not list:
        raise ValueError("invalid_input_shape")
    return tuple(_text(item) for item in cast(list[object], value))


def _parse_case(case_id: CaseId, raw: bytes) -> WorkloadCase:
    obj = _shape(json.loads(raw, object_pairs_hook=_object), {
        "case_id", "description", "expected_failure", "expected_improvement",
        "conversation", "filler_memories", "workload_metadata", "evaluation_query",
        "expected_retrieval_issue",
    })
    if obj["case_id"] != case_id.value or type(obj["conversation"]) is not list:
        raise ValueError("invalid_input_shape")
    turns = []
    for value in cast(list[object], obj["conversation"]):
        turn = _shape(value, {"role", "content"}, {"user_id"})
        role = _text(turn["role"])
        if role not in ("user", "assistant"):
            raise ValueError("invalid_input_shape")
        owner = _text(turn["user_id"]) if "user_id" in turn else None
        turns.append(WorkloadTurn(role, _text(turn["content"]), owner))
    query = _shape(obj["evaluation_query"], {"query", "user_id"})
    metadata = obj["workload_metadata"]
    positions: tuple[int, ...] = ()
    seed = total = None
    if metadata is not None:
        meta = _shape(metadata, {"random_seed", "total_memories", "signal_memory_positions"})
        if (type(meta["random_seed"]) is not int or type(meta["total_memories"]) is not int
                or type(meta["signal_memory_positions"]) is not list):
            raise ValueError("invalid_input_shape")
        seed, total = meta["random_seed"], meta["total_memories"]
        values = cast(list[object], meta["signal_memory_positions"])
        if any(type(value) is not int for value in values):
            raise ValueError("invalid_input_shape")
        positions = cast(tuple[int, ...], tuple(values))
    return WorkloadCase(
        case_id, _HASHES[_index(case_id)], raw, _text(obj["description"]),
        _text(obj["expected_failure"]), _text(obj["expected_improvement"]), tuple(turns),
        _texts(obj["filler_memories"]), _text(query["query"]),
        None if query["user_id"] is None else _text(query["user_id"]),
        _texts(obj["expected_retrieval_issue"]), positions, seed, total,
    )


def _read_verified(path: Path, expected: str) -> bytes:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("hash_mismatch")
    return raw


def load_fixed_workload(repository: Path) -> FixedWorkload:
    """Read only the exact original fixture/baseline bytes, never execute them."""
    directory = repository / "experiments/naive_baseline/workload"
    try:
        if {path.name for path in directory.glob("*.json")} != set(_FILES):
            raise ValueError("fixture_set_mismatch")
        cases = tuple(_parse_case(case_id, _read_verified(directory / name, sha))
                      for case_id, name, sha in zip(CaseId, _FILES, _HASHES, strict=True))
    except (OSError, ValueError, TypeError, KeyError):
        raise EvaluationInputError(ReasonCode.FIXTURE_MISMATCH) from None
    try:
        protocol, result = tuple(_read_verified(repository / "experiments" / name, sha)
                                 for name, sha in _BASELINE_HASHES)
        protocol.decode("utf-8")
        reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
        if reader.fieldnames != ["case_id", "memory_count", "retrieved_count", "latency_ms",
                                 "failures_observed", "failure_names"]:
            raise ValueError("baseline_shape")
        baseline = tuple(BaselineEvidence(CaseId(row["case_id"]), int(row["memory_count"]),
                         int(row["retrieved_count"]), int(row["failures_observed"]),
                         row["failure_names"]) for row in reader)
        if tuple(row.case_id for row in baseline) != tuple(CaseId):
            raise ValueError("baseline_order")
    except (OSError, ValueError, TypeError, KeyError, csv.Error):
        raise EvaluationInputError(ReasonCode.BASELINE_MISMATCH) from None
    return FixedWorkload(cases, baseline)


@dataclass(frozen=True, slots=True)
class EvaluationSettings:
    model: str = _SETTINGS[0]
    revision: str = _SETTINGS[1]
    dimension: int = _SETTINGS[2]
    device: str = _SETTINGS[3]
    threshold: float = _SETTINGS[4]
    limit: int = _SETTINGS[5]
    token_budget: int = _SETTINGS[6]
    tokenizer: str = _SETTINGS[7]
    frozen_time: str = _SETTINGS[8]
    seed: int = _SETTINGS[9]

    def __post_init__(self) -> None:
        for descriptor, expected in zip(fields(self), _SETTINGS, strict=True):
            value = getattr(self, descriptor.name)
            _require(type(value) is type(expected) and value == expected)


@dataclass(frozen=True, slots=True)
class AdaptationEvidence:
    user_candidate_count: int
    filler_count: int
    admission_attempts: int
    accepted_count: int
    rejected_count: int
    control_actions: int

    def __post_init__(self) -> None:
        for descriptor in fields(self):
            _count(getattr(self, descriptor.name))
        _require(self.accepted_count + self.rejected_count <= self.admission_attempts)
        _require(self.admission_attempts <= self.user_candidate_count + self.filler_count)


@dataclass(frozen=True, slots=True)
class ReportExclusion:
    memory_id: str
    reason: str

    def __post_init__(self) -> None:
        _memory_case(self.memory_id)
        _require(type(self.reason) is str and self.reason in (
            "below_relevance_threshold", "budget_exceeded"))


_QUALITY_REASONS = frozenset((ReasonCode.EXPECTED_MEMORY_NOT_SELECTED,
                            ReasonCode.EXTRA_MEMORY_SELECTED, ReasonCode.UNEXPECTED_EMPTY_OUTCOME))
_QUALITY_CHECKS = frozenset((CheckId.EXPECTED_PRIMARY_IDS, CheckId.EXPECTED_EMPTY_OUTCOME))
_COMMON_CHECKS = frozenset(CheckId) - _QUALITY_CHECKS - {
    CheckId.EXPLICIT_LINKS, CheckId.SENSITIVE_REJECTED, CheckId.REJECTED_TARGET_NOT_FOUND,
    CheckId.APARTMENT_ABSENT, CheckId.FORGETTING_COMPLETE, CheckId.DELETED_CURRENT_ABSENT,
    CheckId.DELETED_HISTORICAL_ABSENT, CheckId.NO_REACTIVATION,
}
_OUTCOMES = frozenset(("memories_selected", "no_eligible_memory", "no_relevant_memory",
                      "budget_excluded"))


@dataclass(frozen=True, slots=True)
class CaseReport:
    case_id: CaseId
    baseline: BaselineEvidence
    adaptation: AdaptationEvidence
    status: Verdict
    reason_codes: tuple[ReasonCode, ...]
    checks: tuple[tuple[CheckId, bool], ...]
    executed: bool
    outcome: str | None
    selected_ids: tuple[str, ...]
    exclusions: tuple[ReportExclusion, ...]
    tokens_used: int | None
    token_budget: int = 128
    repeatable: bool = False
    restart_verified: bool = False

    def __post_init__(self) -> None:
        index = _index(self.case_id)
        _require(type(self.baseline) is BaselineEvidence)
        self.baseline.__post_init__()
        _require(self.baseline.case_id is self.case_id)
        _require(type(self.adaptation) is AdaptationEvidence)
        self.adaptation.__post_init__()
        _require(self.adaptation.user_candidate_count == _USER_COUNTS[index]
                 and self.adaptation.filler_count == _FILLER_COUNTS[index])
        _require(self.adaptation.control_actions <= int(self.case_id is CaseId.CASE5))
        _require(type(self.status) is Verdict)
        _require(type(self.reason_codes) is tuple and bool(self.reason_codes))
        _require(all(type(reason) is ReasonCode for reason in self.reason_codes))
        _require(len(set(self.reason_codes)) == len(self.reason_codes))
        allowed = ({ReasonCode.EXPECTED_BEHAVIOR_CONFIRMED} if self.status is Verdict.PASS
                   else _QUALITY_REASONS if self.status is Verdict.PARTIAL
                   else set(ReasonCode) - _QUALITY_REASONS - {ReasonCode.EXPECTED_BEHAVIOR_CONFIRMED})
        _require(set(self.reason_codes) <= allowed)
        _require(type(self.checks) is tuple)
        for check in self.checks:
            _require(type(check) is tuple and len(check) == 2)
            _require(type(check[0]) is CheckId and type(check[1]) is bool)
        _require(len({check[0] for check in self.checks}) == len(self.checks))
        _require(self.status is Verdict.FAIL or all(passed or key in _QUALITY_CHECKS
                                                  for key, passed in self.checks))
        for flag in (self.executed, self.repeatable, self.restart_verified):
            _require(type(flag) is bool)
        _count(self.token_budget)
        _require(self.token_budget == 128)
        _require(type(self.selected_ids) is tuple and type(self.exclusions) is tuple)
        for memory_id in self.selected_ids:
            _require(_memory_case(memory_id) is self.case_id)
        _require(len(set(self.selected_ids)) == len(self.selected_ids))
        for exclusion in self.exclusions:
            _require(type(exclusion) is ReportExclusion)
            exclusion.__post_init__()
            _require(_memory_case(exclusion.memory_id) is self.case_id)
        _require(len({item.memory_id for item in self.exclusions}) == len(self.exclusions))
        _require(set(self.selected_ids).isdisjoint(item.memory_id for item in self.exclusions))
        if not self.executed:
            _require(self.status is Verdict.FAIL and self.outcome is None
                     and self.tokens_used is None and not self.selected_ids and not self.exclusions
                     and not self.repeatable and not self.restart_verified)
        else:
            _require(type(self.outcome) is str and self.outcome in _OUTCOMES)
            _count(self.tokens_used)
            assert self.tokens_used is not None
            _require(self.tokens_used <= self.token_budget)
            _require(bool(self.selected_ids) == (self.outcome == "memories_selected"))
            _require(bool(self.selected_ids) or self.tokens_used == 0)
            if self.status is not Verdict.FAIL:
                _require(self.repeatable and self.restart_verified)
                required = set(_COMMON_CHECKS)
                required.add(CheckId.EXPECTED_PRIMARY_IDS if index < 4
                             else CheckId.EXPECTED_EMPTY_OUTCOME)
                if index < 3:
                    required.add(CheckId.EXPLICIT_LINKS)
                if self.case_id is CaseId.CASE1:
                    required.add(CheckId.APARTMENT_ABSENT)
                    _require("m10-case1-c0000" not in self.selected_ids)
                if self.case_id is CaseId.CASE5:
                    required.update((CheckId.SENSITIVE_REJECTED, CheckId.REJECTED_TARGET_NOT_FOUND,
                                     CheckId.FORGETTING_COMPLETE, CheckId.DELETED_CURRENT_ABSENT,
                                     CheckId.DELETED_HISTORICAL_ABSENT, CheckId.NO_REACTIVATION))
                _require(required <= {key for key, _ in self.checks})
                _require(self.status is not Verdict.PASS or all(passed for _, passed in self.checks))
                _require(self.adaptation.admission_attempts == _USER_COUNTS[index]
                         + _FILLER_COUNTS[index])
                _require(self.adaptation.rejected_count == int(self.case_id is CaseId.CASE5))
                _require(self.adaptation.accepted_count + self.adaptation.rejected_count
                         == self.adaptation.admission_attempts)
                _require(self.adaptation.control_actions == int(self.case_id is CaseId.CASE5))
                _require(self.case_id is not CaseId.CASE6 or (
                    not self.selected_ids and self.outcome == "no_relevant_memory"))


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    cases: tuple[CaseReport, ...]
    settings: EvaluationSettings = field(default_factory=EvaluationSettings)

    def __post_init__(self) -> None:
        _require(type(self.cases) is tuple and len(self.cases) == 6)
        _require(type(self.settings) is EvaluationSettings)
        self.settings.__post_init__()
        for case in self.cases:
            _require(type(case) is CaseReport)
            case.__post_init__()
        _require(tuple(case.case_id for case in self.cases) == tuple(CaseId))


def _record(value: EvaluationSettings | AdaptationEvidence) -> dict[str, object]:
    return {descriptor.name: getattr(value, descriptor.name) for descriptor in fields(value)}


def serialize_report(report: EvaluationReport) -> str:
    """Revalidate every nested field, then emit only closed privacy-safe metadata."""
    _require(type(report) is EvaluationReport)
    report.__post_init__()
    cases = [{
        "case_id": case.case_id.value, "fixture_sha256": _HASHES[_index(case.case_id)],
        "baseline": {"memory_count": case.baseline.memory_count,
                     "retrieved_count": case.baseline.retrieved_count,
                     "failures_observed": case.baseline.failures_observed,
                     "failure_names": case.baseline.failure_names},
        "adaptation": _record(case.adaptation), "status": case.status.value,
        "reason_codes": [reason.value for reason in ReasonCode if reason in case.reason_codes],
        "checks": {key.value: passed for key, passed in sorted(case.checks)},
        "executed": case.executed, "outcome": case.outcome,
        "selected_ids": list(case.selected_ids),
        "exclusions": [{"memory_id": item.memory_id, "reason": item.reason}
                       for item in case.exclusions],
        "tokens_used": case.tokens_used, "token_budget": case.token_budget,
        "repeatable": case.repeatable, "restart_verified": case.restart_verified,
    } for case in report.cases]
    payload = {
        "schema_version": 1, "implementation_stage": "pre-production reference implementation",
        "policy_id": "M10-fixed-workload-v1", "settings": _record(report.settings),
        "comparison_notes": list(_NOTES), "cases": cases,
        "totals": {status.value.lower(): sum(case.status is status for case in report.cases)
                   for status in Verdict},
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False) + "\n"


_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHAIN_INDICES = {CaseId.CASE1: (2, 4), CaseId.CASE2: (0, 2, 4),
                  CaseId.CASE3: (10, 25, 40)}
_EXPECTED_IDS = {CaseId.CASE1: "m10-case1-c0004", CaseId.CASE2: "m10-case2-c0004",
                 CaseId.CASE3: "m10-case3-c0040", CaseId.CASE4: "m10-case4-c0000"}


@dataclass(frozen=True, slots=True)
class TypedCandidate:
    memory_id: str
    at: datetime
    context: RequestContext = field(repr=False)
    request: AdmissionRequest = field(repr=False)


@dataclass(frozen=True, slots=True)
class AdaptedCase:
    case_id: CaseId
    candidates: tuple[TypedCandidate, ...]
    query_context: RequestContext = field(repr=False)
    query_request: RetrievalRequest = field(repr=False)
    query_at: datetime
    deferred_control_turns: tuple[int, ...]


def adapt_case(case: WorkloadCase) -> AdaptedCase:
    """Apply approved caller annotations; no inferred extraction or relationships."""
    index = _index(case.case_id)
    _require(hashlib.sha256(case.raw_bytes).hexdigest() == _HASHES[index])
    # Reparse verified bytes, so a forged in-memory fixture cannot alter execution.
    case = _parse_case(case.case_id, case.raw_bytes)
    case_name = case.case_id.value
    owner = "user_a" if case.case_id is CaseId.CASE4 else f"m10-{case_name}-user"
    chain = _CHAIN_INDICES.get(case.case_id, ())
    sources = [(f"c{i:04d}", i, turn.content, turn.user_id or owner)
               for i, turn in enumerate(case.conversation) if turn.role == "user"
               and not (case.case_id is CaseId.CASE5 and i == 2)]
    sources += [(f"f{j:04d}", len(case.conversation) + j, text, owner)
                for j, text in enumerate(case.filler_memories)]
    candidates = []
    for label, ordinal, text, candidate_owner in sources:
        memory_type, subject, target = "fact", f"m10.{case_name}.{label}", None
        if label.startswith("c") and ordinal in chain:
            memory_type, subject = "decision", "project.storage"
            position = chain.index(ordinal)
            if position:
                target = f"m10-{case_name}-c{chain[position - 1]:04d}"
        elif case.case_id is CaseId.CASE4 and label in ("c0000", "c0002"):
            memory_type, subject = "preference", "deployment.framework"
        key = f"m10-{case_name}-admit-{label}"
        at = _T0 + timedelta(seconds=ordinal)
        candidates.append(TypedCandidate(f"m10-{case_name}-{label}", at,
            RequestContext(user_id=candidate_owner, request_id=key), AdmissionRequest(
                idempotency_key=key, conversation_id=f"m10-{case_name}", turn_id=label,
                content=text, value=text, memory_type=memory_type, subject=subject,
                source_type="explicit_user", source_event_at=at, valid_from=at,
                valid_until=None, supersedes_memory_id=target)))
    return AdaptedCase(case.case_id, tuple(candidates),
        RequestContext(user_id=owner, request_id=f"m10-{case_name}-primary"),
        RetrievalRequest(query=case.query, intent=RetrievalIntent.CURRENT,
                         limit=10, token_budget=128), _T0 + timedelta(days=1),
        (2,) if case.case_id is CaseId.CASE5 else ())


class _CandidateClock:
    def __init__(self) -> None:
        self.at = _T0
        self.calls = 0
        self.sequence: tuple[datetime, ...] = ()

    def now(self) -> datetime:
        self.calls += 1
        if self.sequence:
            self.at, self.sequence = self.sequence[0], self.sequence[1:]
        return self.at


class _CandidateIds:
    def __init__(self) -> None:
        self.memory_id: str | None = None
        self.calls = 0

    def new_id(self) -> str:
        _require(self.memory_id is not None)
        self.calls += 1
        assert self.memory_id is not None
        return self.memory_id


class _EvaluationTelemetryClock:
    def __init__(self) -> None:
        self.ticks = 0

    def utc_now(self) -> datetime:
        return _T0

    def monotonic_ns(self) -> int:
        self.ticks += 1
        return self.ticks * 1_000_000


def _classify_primary(checks: tuple[tuple[CheckId, bool], ...],
                      quality: tuple[ReasonCode, ...]) -> tuple[Verdict, tuple[ReasonCode, ...]]:
    failed = {key for key, passed in checks if not passed} - _QUALITY_CHECKS
    if failed:
        state = {CheckId.CANDIDATE_ACCOUNTING, CheckId.ADMISSION_STATES,
                 CheckId.EXPLICIT_LINKS, CheckId.SENSITIVE_REJECTED}
        reasons = ({ReasonCode.REQUIRED_STATE_FAILURE} if failed & state else set())
        if failed - state:
            reasons.add(ReasonCode.SAFETY_VIOLATION)
        return Verdict.FAIL, tuple(reason for reason in ReasonCode if reason in reasons)
    if quality:
        return Verdict.PARTIAL, tuple(reason for reason in ReasonCode if reason in quality)
    return Verdict.PASS, (ReasonCode.EXPECTED_BEHAVIOR_CONFIRMED,)


@dataclass(frozen=True, slots=True)
class PrimaryCaseEvidence:
    """Intermediate primary evidence, not a final M10 acceptance report."""

    case_id: CaseId
    baseline: BaselineEvidence
    adaptation: AdaptationEvidence
    status: Verdict
    reason_codes: tuple[ReasonCode, ...]
    checks: tuple[tuple[CheckId, bool], ...]
    executed: bool
    outcome: str
    selected_ids: tuple[str, ...]
    exclusions: tuple[ReportExclusion, ...]
    tokens_used: int
    token_budget: int = 128

    def __post_init__(self) -> None:
        _index(self.case_id)
        _require(type(self.baseline) is BaselineEvidence and self.baseline.case_id is self.case_id)
        self.baseline.__post_init__()
        _require(type(self.adaptation) is AdaptationEvidence)
        self.adaptation.__post_init__()
        _require(type(self.status) is Verdict and type(self.reason_codes) is tuple)
        _require(bool(self.reason_codes) and all(type(reason) is ReasonCode
                                               for reason in self.reason_codes))
        _require(type(self.checks) is tuple)
        for pair in self.checks:
            _require(type(pair) is tuple and len(pair) == 2 and type(pair[0]) is CheckId
                     and type(pair[1]) is bool)
        _require(len({key for key, _ in self.checks}) == len(self.checks))
        deferred = {CheckId.RESTART_VERIFIED, CheckId.REPEATABLE, CheckId.ZERO_BUDGET_EMPTY,
                    CheckId.ZERO_BUDGET_OUTCOME, CheckId.FORGETTING_COMPLETE,
                    CheckId.REJECTED_TARGET_NOT_FOUND, CheckId.DELETED_CURRENT_ABSENT,
                    CheckId.DELETED_HISTORICAL_ABSENT, CheckId.NO_REACTIVATION}
        _require(not (deferred & {key for key, _ in self.checks}))
        required = set(_COMMON_CHECKS) - deferred
        required.add(CheckId.EXPECTED_PRIMARY_IDS if _index(self.case_id) < 4
                     else CheckId.EXPECTED_EMPTY_OUTCOME)
        if self.case_id in _CHAIN_INDICES:
            required.add(CheckId.EXPLICIT_LINKS)
        if self.case_id is CaseId.CASE1:
            required.add(CheckId.APARTMENT_ABSENT)
        if self.case_id is CaseId.CASE5:
            required.add(CheckId.SENSITIVE_REJECTED)
        _require(required <= {key for key, _ in self.checks})
        _require(self.adaptation.control_actions == 0)
        _require(type(self.executed) is bool and self.executed)
        _require(type(self.outcome) is str and self.outcome in _OUTCOMES)
        _count(self.tokens_used)
        _count(self.token_budget)
        _require(self.token_budget == 128 and type(self.selected_ids) is tuple)
        _require(type(self.exclusions) is tuple)
        for memory_id in self.selected_ids:
            _require(_memory_case(memory_id) is self.case_id)
        for exclusion in self.exclusions:
            _require(type(exclusion) is ReportExclusion)
            exclusion.__post_init__()
            _require(_memory_case(exclusion.memory_id) is self.case_id)
        quality = tuple(reason for reason in self.reason_codes if reason in _QUALITY_REASONS)
        _require((self.status, self.reason_codes) == _classify_primary(self.checks, quality))


def _primary_evidence(adapted: AdaptedCase, baseline: BaselineEvidence,
                      service: MemoryService, database: Path,
                      results: tuple[AdmissionResult, ...], sensitive_safe: bool,
                      result: RetrievalResult) -> PrimaryCaseEvidence:
    # Local-demo inspection only: read-only SQLite, no writes or pipeline dispatch.
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM memories").fetchall()
        mappings = connection.execute("SELECT memory_id FROM memory_vector_mappings").fetchall()
        embeddings = connection.execute("SELECT memory_id, embedding_model, embedding_dimension, "
                                        "length(embedding_blob) AS size FROM memory_embeddings").fetchall()
    accepted = [candidate for candidate, admitted in zip(adapted.candidates, results, strict=True)
                if admitted.retrievable]
    by_id = {row["memory_id"]: row for row in rows}
    accepted_ids = {candidate.memory_id for candidate in accepted}
    expected_ids = {candidate.memory_id for candidate in adapted.candidates
                    if candidate.memory_id != "m10-case5-c0000"}
    checks = {
        CheckId.FIXTURE_BYTES: True, CheckId.BASELINE_BYTES: True,
        CheckId.CANDIDATE_ACCOUNTING: len(rows) == len(accepted)
            and accepted_ids == expected_ids and set(by_id) == accepted_ids
            and {row["memory_id"] for row in mappings} == accepted_ids
            and {row["memory_id"] for row in embeddings} == accepted_ids,
        CheckId.ADMISSION_STATES: all(admitted.memory_id == candidate.memory_id
            and admitted.indexing_state is not None and admitted.indexing_state.value == "indexed"
            for candidate, admitted in zip(adapted.candidates, results, strict=True)
            if admitted.retrievable) and all(row["indexing_state"] == "indexed" for row in rows)
            and all(row["embedding_model"] == f"{_SETTINGS[0]}@{_SETTINGS[1]}"
                    and row["embedding_dimension"] == 768 and row["size"] == 3072
                    for row in embeddings),
        CheckId.CURRENT_OWNER_SCOPE: all(item.memory.user_id == adapted.query_context.user_id
            and by_id[item.memory.memory_id]["user_id"] == adapted.query_context.user_id
            for item in result.memories),
        CheckId.INDEXED_ONLY: all(item.memory.indexing_state.value == "indexed"
                                 for item in result.memories),
        CheckId.CURRENT_STATE_ONLY: all(item.memory.lifecycle_status.value == "active"
            and item.memory.superseded_by is None
            and (item.memory.valid_from is None or item.memory.valid_from <= adapted.query_at)
            and (item.memory.valid_until is None or adapted.query_at < item.memory.valid_until)
            for item in result.memories),
        CheckId.TOMBSTONES_ABSENT: all(item.memory.deleted_at is None
            and by_id[item.memory.memory_id]["deleted_at"] is None for item in result.memories),
        CheckId.STALE_TARGETS_ABSENT: all(by_id[item.memory.memory_id]["lifecycle_status"] == "active"
                                        for item in result.memories),
        CheckId.SENSITIVE_ABSENT: "sk-test-123456789" not in result.context
            and all("sk-test-123456789" not in row["content"] for row in rows),
        CheckId.RELEVANCE_BOUNDARY: all(math.isfinite(item.score) and item.score >= 0.50
                                       for item in result.memories),
        CheckId.COMPLETE_BLOCKS: result.context == "\n\n".join(
            f"Memory {item.memory.memory_id}:\n{by_id[item.memory.memory_id]['content']}"
            for item in result.memories) and all(item.memory.content
                == by_id[item.memory.memory_id]["content"] for item in result.memories),
        CheckId.EXACT_TOKENS: service._token_counter.count_tokens(result.context) == result.tokens_used
            and result.tokenizer == "cl100k_base",
        CheckId.BUDGET_BOUND: result.token_budget == 128 and result.tokens_used <= 128,
        CheckId.RESULT_CONSISTENCY: result.included_memory_ids == tuple(
            item.memory.memory_id for item in result.memories)
            and bool(result.memories) == (result.outcome.value == "memories_selected")
            and (bool(result.memories) or not result.context),
    }
    chain = _CHAIN_INDICES.get(adapted.case_id, ())
    if chain:
        checks[CheckId.EXPLICIT_LINKS] = all(
            by_id[f"m10-{adapted.case_id.value}-c{old:04d}"]["superseded_by"]
                == f"m10-{adapted.case_id.value}-c{new:04d}"
            and by_id[f"m10-{adapted.case_id.value}-c{old:04d}"]["lifecycle_status"] == "superseded"
            and json.loads(by_id[f"m10-{adapted.case_id.value}-c{new:04d}"]["supersedes_json"])
                == [f"m10-{adapted.case_id.value}-c{old:04d}"]
            for old, new in pairwise(chain))
    if adapted.case_id is CaseId.CASE1:
        checks[CheckId.APARTMENT_ABSENT] = "m10-case1-c0000" not in result.included_memory_ids
    if adapted.case_id is CaseId.CASE5:
        checks[CheckId.SENSITIVE_REJECTED] = sensitive_safe and len(results) == 16 \
            and results[0].reason == "sensitive_credential" and results[0].memory_id is None \
            and not results[0].retrievable and "m10-case5-c0000" not in by_id
    if adapted.case_id is CaseId.CASE6:
        checks[CheckId.RELEVANCE_BOUNDARY] &= not result.memories
        checks[CheckId.CANDIDATE_ACCOUNTING] &= len(rows) == 17 \
            and result.outcome.value != "no_eligible_memory"
    quality: tuple[ReasonCode, ...] = ()
    expected = _EXPECTED_IDS.get(adapted.case_id)
    if expected is not None:
        checks[CheckId.EXPECTED_PRIMARY_IDS] = result.included_memory_ids == (expected,)
        if expected not in result.included_memory_ids:
            quality += (ReasonCode.EXPECTED_MEMORY_NOT_SELECTED,)
        if set(result.included_memory_ids) - {expected}:
            quality += (ReasonCode.EXTRA_MEMORY_SELECTED,)
    else:
        checks[CheckId.EXPECTED_EMPTY_OUTCOME] = not result.memories \
            and result.outcome.value == "no_relevant_memory"
        if result.memories:
            quality = (ReasonCode.EXTRA_MEMORY_SELECTED,)
        elif result.outcome.value != "no_relevant_memory":
            quality = (ReasonCode.UNEXPECTED_EMPTY_OUTCOME,)
    index = _index(adapted.case_id)
    adaptation = AdaptationEvidence(_USER_COUNTS[index], _FILLER_COUNTS[index], len(results),
                                   len(accepted), len(results) - len(accepted), 0)
    # Every emitted string is closed or a planned ID; no raw results enter reports.
    checks[CheckId.REPORT_PRIVACY] = True
    pairs = tuple(sorted(checks.items()))
    status, reasons = _classify_primary(pairs, quality)
    return PrimaryCaseEvidence(adapted.case_id, baseline, adaptation, status, reasons, pairs, True,
        result.outcome.value, result.included_memory_ids,
        tuple(ReportExclusion(item.memory_id, item.reason.value) for item in result.exclusions),
        result.tokens_used)


def execute_primary_workloads(repository: Path, store_root: Path,
                              model_cache_directory: Path) -> tuple[PrimaryCaseEvidence, ...]:
    """Six sequential real primary paths; no restarts, forgetting, or extra queries."""
    return cast(tuple[PrimaryCaseEvidence, ...],
                _execute_workloads(repository, store_root, model_cache_directory, supplemental=False))


def execute_verified_workloads(repository: Path, store_root: Path,
                               model_cache_directory: Path, *,
                               progress: Callable[[PrimaryCaseEvidence | CaseReport], None] | None = None,
                               ) -> EvaluationReport:
    """Execute frozen primary paths and every approved supplemental probe."""
    return EvaluationReport(cast(tuple[CaseReport, ...],
        _execute_workloads(repository, store_root, model_cache_directory,
                           supplemental=True, progress=progress)))


def _execute_workloads(repository: Path, store_root: Path, model_cache_directory: Path,
                       *, supplemental: bool,
                       progress: Callable[[PrimaryCaseEvidence | CaseReport], None] | None = None,
                       ) -> tuple[PrimaryCaseEvidence | CaseReport, ...]:
    from conversational_memory.composition import compose_local_memory_service

    workload = load_fixed_workload(repository)
    _require(os.environ.get("HF_HUB_OFFLINE") == "1"
             and os.environ.get("TRANSFORMERS_OFFLINE") == "1"
             and os.environ.get("CUDA_VISIBLE_DEVICES") == "")
    cache = os.environ.get("TIKTOKEN_CACHE_DIR")
    _require(bool(cache) and model_cache_directory.is_dir())
    assert cache is not None
    tokenizer = Path(cache) / "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
    _read_verified(tokenizer, "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7")
    store_root.mkdir(parents=True, exist_ok=False)
    evidence: list[PrimaryCaseEvidence | CaseReport] = []
    for case, baseline in zip(workload.cases, workload.baseline, strict=True):
        adapted = adapt_case(case)
        directory = store_root / case.case_id.value
        directory.mkdir()
        database = directory / "memory.sqlite3"
        clock, ids, sink = _CandidateClock(), _CandidateIds(), CaptureEventSink()

        def fresh_service(database: Path = database, directory: Path = directory,
                          clock: _CandidateClock = clock, ids: _CandidateIds = ids,
                          sink: CaptureEventSink = sink) -> MemoryService:
            return compose_local_memory_service(database_path=database,
                index_directory=directory / "index", model_cache_directory=model_cache_directory,
                clock=clock, memory_ids=ids, relevance_threshold=0.50, event_sink=sink,
                telemetry_clock=_EvaluationTelemetryClock(), user_hmac_key=b"m10-local-evaluation-key-32-bytes!!")

        # Discard local library chatter; never serialize it or exception text.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            if progress is not None:
                progress(_incomplete_evidence(case.case_id, baseline, (), attempts=0))
            service = fresh_service()
            results: list[AdmissionResult] = []
            sensitive_safe = True
            rejected_control_safe = False
            control_actions = 0
            for candidate in adapted.candidates:
                clock.at, ids.memory_id = candidate.at, candidate.memory_id
                calls_before, events_before = ids.calls, len(sink.events)
                state_before = _stored_state(database) if supplemental else ()
                if progress is not None:
                    progress(_incomplete_evidence(case.case_id, baseline, tuple(results),
                                                  attempts=len(results) + 1, control_actions=control_actions))
                admitted = service.admit(candidate.context, candidate.request)
                results.append(admitted)
                if progress is not None:
                    progress(_incomplete_evidence(case.case_id, baseline, tuple(results),
                        attempts=len(results), control_actions=control_actions))
                if candidate.memory_id == "m10-case5-c0000":
                    events = sink.events[events_before:]
                    sensitive_safe = ids.calls == calls_before and not any(
                        event.event_name in (EventName.INDEXING_COMPLETED, EventName.INDEXING_FAILED)
                        or event.stage in (EventStage.PERSIST_PENDING, EventStage.MARK_INDEXED)
                        for event in events)
                    if supplemental:
                        sensitive_safe &= _stored_state(database) == state_before and not any(
                            event.stage is EventStage.EMBEDDING for event in events)
                        clock_calls, state = clock.calls, _stored_state(database)
                        events_before = len(sink.events)
                        control_actions = 1
                        if progress is not None:
                            progress(_incomplete_evidence(case.case_id, baseline, tuple(results),
                                attempts=len(results), control_actions=control_actions))
                        denied = service.forget(RequestContext(user_id=adapted.query_context.user_id,
                            request_id="m10-case5-forget-rejected"),
                            ForgetRequest(memory_id="m10-case5-c0000"))
                        rejected_control_safe = denied.outcome.value == "not_found" \
                            and denied.memory_id is None and clock.calls == clock_calls \
                            and _stored_state(database) == state and not any(
                                event.event_name in (EventName.INDEXING_COMPLETED, EventName.INDEXING_FAILED)
                                for event in sink.events[events_before:])
                        if progress is not None:
                            progress(_incomplete_evidence(case.case_id, baseline, tuple(results),
                                attempts=len(results), control_actions=1))
            clock.at, ids.memory_id = adapted.query_at, None
            persisted = _stored_state(database) if supplemental else ()
            if supplemental:
                service = fresh_service()
            result = service.retrieve(adapted.query_context, adapted.query_request)
            if progress is not None:
                progress(_incomplete_evidence(case.case_id, baseline, tuple(results),
                    attempts=len(results), control_actions=control_actions, result=result))
            primary = _primary_evidence(adapted, baseline, service, database,
                                        tuple(results), sensitive_safe, result)
            if progress is not None:
                progress(replace(_incomplete_evidence(case.case_id, baseline, tuple(results),
                    attempts=len(results), control_actions=control_actions, result=result), checks=primary.checks))
            if not supplemental:
                evidence.append(primary)
                continue
            checks = dict(primary.checks)
            checks[CheckId.RESTART_VERIFIED] = _stored_state(database) == persisted
            service = fresh_service()
            repeated = service.retrieve(replace(adapted.query_context,
                request_id=f"m10-{case.case_id.value}-post-restart"), adapted.query_request)
            repeat_evidence = _primary_evidence(adapted, baseline, service, database,
                                                tuple(results), sensitive_safe, repeated)
            for key, passed in repeat_evidence.checks:
                if key not in _QUALITY_CHECKS:
                    checks[key] &= passed
            checks[CheckId.RESTART_VERIFIED] &= _stored_state(database) == persisted
            checks[CheckId.REPEATABLE] = _retrieval_fingerprint(result) == _retrieval_fingerprint(repeated)
            zero = service.retrieve(replace(adapted.query_context,
                request_id=f"m10-{case.case_id.value}-zero-budget"),
                replace(adapted.query_request, token_budget=0))
            checks[CheckId.ZERO_BUDGET_EMPTY] = not zero.memories and not zero.context \
                and not zero.included_memory_ids and zero.tokens_used == 0 \
                and _probe_is_safe(service, database, adapted, zero, token_budget=0)
            expected_zero = "budget_excluded" if result.memories or result.outcome.value == "budget_excluded" \
                else result.outcome.value
            checks[CheckId.ZERO_BUDGET_OUTCOME] = zero.outcome.value == expected_zero
            adaptation = primary.adaptation
            if case.case_id is CaseId.CASE5:
                checks[CheckId.REJECTED_TARGET_NOT_FOUND] = rejected_control_safe
                adaptation = replace(adaptation, control_actions=1)
                clock.sequence = (_T0 + timedelta(days=2), _T0 + timedelta(days=2, seconds=1))
                forgotten = service.forget(RequestContext(user_id=adapted.query_context.user_id,
                    request_id="m10-case5-forget-f0000"), ForgetRequest(memory_id="m10-case5-f0000"))
                checks[CheckId.FORGETTING_COMPLETE] = forgotten.cleanup_complete \
                    and not forgotten.retrievable and not clock.sequence \
                    and _forgetting_is_complete(database)
                after_forgetting = _stored_state(database)
                service = fresh_service()
                filler_query = case.filler_memories[0]
                for intent, check in ((RetrievalIntent.CURRENT, CheckId.DELETED_CURRENT_ABSENT),
                                      (RetrievalIntent.HISTORICAL, CheckId.DELETED_HISTORICAL_ABSENT)):
                    supplemental_result = service.retrieve(replace(adapted.query_context,
                        request_id=f"m10-case5-forgotten-{intent.value}"),
                        RetrievalRequest(query=filler_query, intent=intent, limit=10, token_budget=128))
                    checks[check] = "m10-case5-f0000" not in supplemental_result.included_memory_ids \
                        and _probe_is_safe(service, database, adapted, supplemental_result, token_budget=128)
                checks[CheckId.NO_REACTIVATION] = _stored_state(database) == after_forgetting
            pairs = tuple(sorted(checks.items()))
            quality = tuple(reason for reason in primary.reason_codes if reason in _QUALITY_REASONS)
            status, reasons = _classify_completed(pairs, quality)
            completed = CaseReport(case_id=primary.case_id, baseline=baseline, adaptation=adaptation,
                status=status, reason_codes=reasons, checks=pairs, executed=True,
                outcome=primary.outcome, selected_ids=primary.selected_ids, exclusions=primary.exclusions,
                tokens_used=primary.tokens_used, repeatable=checks[CheckId.REPEATABLE],
                restart_verified=checks[CheckId.RESTART_VERIFIED])
            evidence.append(completed)
            if progress is not None:
                progress(completed)
    # Read-only second load proves hashes did not change during real execution.
    load_fixed_workload(repository)
    return tuple(evidence)


def _incomplete_evidence(case_id: CaseId, baseline: BaselineEvidence,
                         results: tuple[AdmissionResult, ...], *, attempts: int,
                         control_actions: int = 0, result: RetrievalResult | None = None) -> CaseReport:
    index = _index(case_id)
    return CaseReport(case_id=case_id, baseline=baseline,
        adaptation=AdaptationEvidence(_USER_COUNTS[index], _FILLER_COUNTS[index], attempts,
            sum(item.retrievable for item in results),
            sum(item.decision.value == "rejected" for item in results), control_actions),
        status=Verdict.FAIL, reason_codes=(ReasonCode.PREREQUISITE_UNAVAILABLE,),
        checks=((CheckId.FIXTURE_BYTES, True), (CheckId.BASELINE_BYTES, True)),
        executed=result is not None, outcome=result.outcome.value if result is not None else None,
        selected_ids=result.included_memory_ids if result is not None else (),
        exclusions=tuple(ReportExclusion(item.memory_id, item.reason.value) for item in result.exclusions)
            if result is not None else (), tokens_used=result.tokens_used if result is not None else None)


def failed_evaluation_report(reason: ReasonCode, recorded: tuple[PrimaryCaseEvidence | CaseReport, ...],
                             *, prerequisite: bool) -> EvaluationReport:
    """Closed failure output: retain observations, never invent retrieval results."""
    _require(type(reason) is ReasonCode and type(prerequisite) is bool)
    observed = {item.case_id: item for item in recorded}
    cases = []
    reasons = tuple(code for code in ReasonCode if code is reason or
                    (prerequisite and code is ReasonCode.PREREQUISITE_UNAVAILABLE))
    for index, case_id in enumerate(CaseId):
        item = observed.get(case_id)
        invalid_inputs = reason in (ReasonCode.FIXTURE_MISMATCH, ReasonCode.BASELINE_MISMATCH)
        if type(item) is CaseReport and item.reason_codes != (ReasonCode.PREREQUISITE_UNAVAILABLE,) \
            and not invalid_inputs:
            item.__post_init__()
            cases.append(item)
            continue
        baseline = BaselineEvidence(case_id, _BASELINE_COUNTS[index],
            min(10, _BASELINE_COUNTS[index]), 1, _BASELINE_FAILURES[index])
        if item is None:
            item = _incomplete_evidence(case_id, baseline, (), attempts=0)
            item = replace(item, checks=())  # No input verification evidence available.
        item.__post_init__()
        checks = dict(item.checks)
        if reason is ReasonCode.FIXTURE_MISMATCH:
            checks[CheckId.FIXTURE_BYTES] = False
        elif reason is ReasonCode.BASELINE_MISMATCH:
            checks[CheckId.BASELINE_BYTES] = False
        cases.append(CaseReport(case_id=case_id, baseline=item.baseline, adaptation=item.adaptation,
            status=Verdict.FAIL, reason_codes=reasons, checks=tuple(sorted(checks.items())), executed=item.executed,
            outcome=item.outcome, selected_ids=item.selected_ids, exclusions=item.exclusions,
            tokens_used=item.tokens_used))
    if not any(case.status is Verdict.FAIL for case in cases):
        # A later boundary failure must not turn into a successful aggregate.
        cases[-1] = replace(cases[-1], status=Verdict.FAIL, reason_codes=reasons)
    return EvaluationReport(tuple(cases))


def _stored_state(database: Path) -> tuple[tuple[tuple[object, ...], ...], ...]:
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        return tuple(tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall())
            for table in ("memories", "admission_idempotency", "memory_embeddings",
                          "memory_vector_mappings", "memory_forgetting"))


def _retrieval_fingerprint(result: RetrievalResult) -> tuple[object, ...]:
    return (result.outcome, result.included_memory_ids, result.exclusions, result.tokens_used, result.context)


def _classify_completed(checks: tuple[tuple[CheckId, bool], ...],
                        quality: tuple[ReasonCode, ...]) -> tuple[Verdict, tuple[ReasonCode, ...]]:
    failed = {key for key, passed in checks if not passed} - _QUALITY_CHECKS
    if not failed:
        return _classify_primary(checks, quality)
    reasons = set()
    if CheckId.REPEATABLE in failed:
        reasons.add(ReasonCode.NON_REPRODUCIBLE_RESULT)
    state = {CheckId.CANDIDATE_ACCOUNTING, CheckId.ADMISSION_STATES, CheckId.EXPLICIT_LINKS,
             CheckId.SENSITIVE_REJECTED, CheckId.REJECTED_TARGET_NOT_FOUND,
             CheckId.RESTART_VERIFIED, CheckId.FORGETTING_COMPLETE}
    if failed & state:
        reasons.add(ReasonCode.REQUIRED_STATE_FAILURE)
    if failed - state - {CheckId.REPEATABLE}:
        reasons.add(ReasonCode.SAFETY_VIOLATION)
    return Verdict.FAIL, tuple(reason for reason in ReasonCode if reason in reasons)


def _forgetting_is_complete(database: Path) -> bool:
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT f.cleanup_state,f.requested_at,f.completed_at,m.deleted_at "
            "FROM memory_forgetting f JOIN memories m ON m.memory_id=f.memory_id "
            "WHERE f.memory_id='m10-case5-f0000'").fetchone()
        return bool(row == ("complete", "2026-01-03T00:00:00.000000Z", "2026-01-03T00:00:01.000000Z",
                       "2026-01-03T00:00:00.000000Z") and connection.execute(
            "SELECT count(*) FROM memory_vector_mappings WHERE memory_id='m10-case5-f0000'").fetchone()[0] == 0)


def _probe_is_safe(service: MemoryService, database: Path, adapted: AdaptedCase,
                   result: RetrievalResult, *, token_budget: int) -> bool:
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = {row["memory_id"]: row for row in connection.execute("SELECT * FROM memories")}
    for item in result.memories:
        row = rows.get(item.memory.memory_id)
        if row is None or row["user_id"] != adapted.query_context.user_id \
            or item.memory.user_id != row["user_id"] \
            or row["indexing_state"] != "indexed" or row["deleted_at"] is not None \
            or item.memory.indexing_state.value != "indexed" or item.memory.deleted_at is not None \
            or row["lifecycle_status"] != "active" or row["superseded_by"] is not None \
            or item.memory.lifecycle_status.value != "active" or item.memory.superseded_by is not None \
            or not math.isfinite(item.score) or item.score < 0.50 \
            or item.memory.content != row["content"]:
            return False
    return result.token_budget == token_budget and result.tokenizer == "cl100k_base" \
        and result.tokens_used == service._token_counter.count_tokens(result.context) \
        and result.tokens_used <= token_budget \
        and result.included_memory_ids == tuple(item.memory.memory_id for item in result.memories) \
        and bool(result.memories) == (result.outcome.value == "memories_selected") \
        and result.context == "\n\n".join(f"Memory {item.memory.memory_id}:\n{item.memory.content}"
                                         for item in result.memories)


def serialize_primary_evidence(cases: tuple[PrimaryCaseEvidence, ...]) -> str:
    """Intermediate case-data array only; deliberately not the final M10 summary."""
    _require(type(cases) is tuple and len(cases) == 6)
    for case in cases:
        _require(type(case) is PrimaryCaseEvidence)
        case.__post_init__()
    _require(tuple(case.case_id for case in cases) == tuple(CaseId))
    payload = [{"case_id": case.case_id.value, "fixture_sha256": _HASHES[_index(case.case_id)],
        "baseline": {"memory_count": case.baseline.memory_count,
                     "retrieved_count": case.baseline.retrieved_count,
                     "failures_observed": case.baseline.failures_observed,
                     "failure_names": case.baseline.failure_names},
        "adaptation": _record(case.adaptation), "status": case.status.value,
        "reason_codes": [reason.value for reason in case.reason_codes],
        "checks": {key.value: passed for key, passed in case.checks},
        "executed": case.executed, "outcome": case.outcome,
        "selected_ids": list(case.selected_ids),
        "exclusions": [{"memory_id": item.memory_id, "reason": item.reason}
                       for item in case.exclusions],
        "tokens_used": case.tokens_used, "token_budget": case.token_budget} for case in cases]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False) + "\n"
