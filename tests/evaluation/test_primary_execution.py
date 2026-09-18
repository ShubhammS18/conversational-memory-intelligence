"""Real primary paths only; deferred M10 acceptance probes are not executed."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conversational_memory.entrypoints import evaluation

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("case_id", tuple(evaluation.CaseId))
def test_typed_mapping_is_exact_and_preserves_source_content(case_id: evaluation.CaseId) -> None:
    case = evaluation.load_fixed_workload(ROOT).cases[list(evaluation.CaseId).index(case_id)]
    adapted = evaluation.adapt_case(case)
    assert adapted.query_request.query == case.query
    assert adapted.query_request.intent.value == "current"
    assert (adapted.query_request.limit, adapted.query_request.token_budget) == (10, 128)
    assert adapted.query_at == datetime(2026, 1, 2, tzinfo=UTC)
    assert adapted.query_context.user_id == ("user_a" if case_id is evaluation.CaseId.CASE4
                                            else f"m10-{case_id.value}-user")
    for candidate in adapted.candidates:
        label = candidate.request.turn_id
        position = int(label[1:])
        source = (case.conversation[position].content if label[0] == "c"
                  else case.filler_memories[position])
        ordinal = position if label[0] == "c" else len(case.conversation) + position
        assert candidate.request.content == candidate.request.value == source
        assert candidate.memory_id == f"m10-{case_id.value}-{label}"
        assert candidate.request.idempotency_key == candidate.context.request_id
        assert candidate.at == datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=ordinal)
        assert candidate.request.valid_from == candidate.request.source_event_at == candidate.at
        assert candidate.request.valid_until is None
        assert candidate.request.source_type == "explicit_user"
    expected_targets = {
        "case1": {"c0004": "c0002"},
        "case2": {"c0002": "c0000", "c0004": "c0002"},
        "case3": {"c0025": "c0010", "c0040": "c0025"},
    }.get(case_id.value, {})
    assert {item.request.turn_id: item.request.supersedes_memory_id
            for item in adapted.candidates if item.request.supersedes_memory_id} == {
                label: f"m10-{case_id.value}-{target}" for label, target in expected_targets.items()}
    assert adapted.deferred_control_turns == ((2,) if case_id is evaluation.CaseId.CASE5 else ())
    assert len(adapted.candidates) == (18, 3, 50, 17, 16, 17)[list(evaluation.CaseId).index(case_id)]


@pytest.fixture(scope="module")
def real_primary(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, tuple]:
    cache = os.environ.get("CONVERSATIONAL_MEMORY_MODEL_CACHE")
    assert cache, "recorded model cache configuration is required; no skips"
    parent = tmp_path_factory.mktemp("m10-primary")
    inputs = [ROOT / "experiments/naive_baseline/workload" / name
              for name in evaluation._FILES]
    inputs += [ROOT / "experiments/baseline_protocol.md", ROOT / "experiments/baseline_results.csv"]
    before = tuple(path.read_bytes() for path in inputs)
    # Fail immediately if iteration 2 attempts any forgetting or historical path.
    from unittest.mock import patch

    from conversational_memory.application import MemoryService
    original_retrieve = MemoryService.retrieve

    def current_only(service: MemoryService, context: object, request: object) -> object:
        assert request.intent.value == "current"
        assert request.limit == 10 and request.token_budget == 128
        return original_retrieve(service, context, request)

    with patch.object(MemoryService, "forget", side_effect=AssertionError("forgetting forbidden")), \
         patch.object(MemoryService, "retrieve", current_only):
        reports = evaluation.execute_primary_workloads(ROOT, parent / "stores", Path(cache))
    assert before == tuple(path.read_bytes() for path in inputs)
    assert [hashlib.sha256(raw).hexdigest() for raw in before[:6]] == list(evaluation._HASHES)
    assert tuple(report.case_id for report in reports) == tuple(evaluation.CaseId)
    safe = json.loads(evaluation.serialize_primary_evidence(reports))
    print(json.dumps([{key: item[key] for key in (
        "case_id", "status", "reason_codes", "outcome", "selected_ids", "tokens_used")}
        for item in safe], sort_keys=True, separators=(",", ":")))
    return parent / "stores", reports


@pytest.mark.real_model
@pytest.mark.parametrize("index", range(6), ids=[case.value for case in evaluation.CaseId])
def test_real_primary_case_hard_gates_and_truthful_verdict(real_primary: tuple, index: int) -> None:
    _, reports = real_primary
    evidence = reports[index]
    assert evidence.executed
    assert evidence.status is not evaluation.Verdict.FAIL, evidence.reason_codes
    checks = dict(evidence.checks)
    assert all(passed for key, passed in evidence.checks if key not in (
        evaluation.CheckId.EXPECTED_PRIMARY_IDS, evaluation.CheckId.EXPECTED_EMPTY_OUTCOME))
    assert 0 <= evidence.tokens_used <= 128
    assert not ({evaluation.CheckId.RESTART_VERIFIED, evaluation.CheckId.REPEATABLE,
                 evaluation.CheckId.ZERO_BUDGET_EMPTY, evaluation.CheckId.FORGETTING_COMPLETE}
                & checks.keys())
    assert evidence.adaptation.control_actions == 0
    if index >= 4:
        assert evidence.selected_ids == ()
        assert evidence.outcome == "no_relevant_memory"
    else:
        expected = ("c0004", "c0004", "c0040", "c0000")[index]
        if evidence.status is evaluation.Verdict.PASS:
            assert evidence.selected_ids == (f"m10-case{index + 1}-{expected}",)


@pytest.mark.real_model
def test_persisted_owners_links_and_sensitive_rejection(real_primary: tuple) -> None:
    root, reports = real_primary
    for index, report in enumerate(reports):
        with sqlite3.connect(root / report.case_id.value / "memory.sqlite3") as connection:
            rows = connection.execute("SELECT memory_id, user_id, lifecycle_status, indexing_state, "
                "supersedes_json, superseded_by FROM memories ORDER BY memory_id").fetchall()
            assert len(rows) == (18, 3, 50, 17, 15, 17)[index]
            assert all(row[3] == "indexed" for row in rows)
            assert connection.execute("SELECT count(*) FROM memory_vector_mappings").fetchone()[0] == len(rows)
            assert connection.execute("SELECT count(*) FROM memory_embeddings").fetchone()[0] == len(rows)
            if index < 3:
                old = [row for row in rows if row[2] == "superseded"]
                assert len(old) == (1, 2, 2)[index]
                by_id = {row[0]: row for row in rows}
                for row in old:
                    assert row[0] in json.loads(by_id[row[5]][4])
                    assert row[0] not in report.selected_ids
            if index == 3:
                assert {row[1] for row in rows} == {"user_a", "user_b"}
                assert "m10-case4-c0002" not in report.selected_ids
            if index == 4:
                assert not any(row[0] == "m10-case5-c0000" for row in rows)
                assert connection.execute("SELECT count(*) FROM memory_forgetting").fetchone()[0] == 0
                assert report.adaptation.rejected_count == 1
                assert dict(report.checks)[evaluation.CheckId.SENSITIVE_REJECTED]


@pytest.mark.real_model
def test_primary_serialization_is_deterministic_safe_and_not_final_acceptance(real_primary: tuple) -> None:
    _, reports = real_primary
    output = evaluation.serialize_primary_evidence(reports)
    assert output == evaluation.serialize_primary_evidence(reports)
    payload = json.loads(output)
    assert output == json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False) + "\n"
    assert len(payload) == 6
    assert all("repeatable" not in item and "restart_verified" not in item for item in payload)
    for case in evaluation.load_fixed_workload(ROOT).cases:
        for text in (case.query, *(turn.content for turn in case.conversation), *case.filler_memories):
            assert text not in output and text.encode() not in output.encode()
    for sentinel in ("user_a", "user_b", "sk-test-123456789", "m10-case1-user"):
        assert sentinel not in output and sentinel.encode() not in output.encode()
    forbidden = {"query", "content", "user_id", "subject", "value", "exception", "credentials"}

    def scan(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for item in value.values():
                scan(item)
        elif isinstance(value, list):
            for item in value:
                scan(item)
    scan(payload)
    with pytest.raises(ValueError, match="invalid_evaluation_report"):
        evaluation.serialize_report(reports)


@pytest.mark.parametrize("check", (
    evaluation.CheckId.CANDIDATE_ACCOUNTING, evaluation.CheckId.ADMISSION_STATES,
    evaluation.CheckId.EXPLICIT_LINKS, evaluation.CheckId.SENSITIVE_REJECTED,
    evaluation.CheckId.CURRENT_OWNER_SCOPE, evaluation.CheckId.INDEXED_ONLY,
    evaluation.CheckId.CURRENT_STATE_ONLY, evaluation.CheckId.TOMBSTONES_ABSENT,
    evaluation.CheckId.STALE_TARGETS_ABSENT, evaluation.CheckId.SENSITIVE_ABSENT,
    evaluation.CheckId.APARTMENT_ABSENT, evaluation.CheckId.RELEVANCE_BOUNDARY,
    evaluation.CheckId.COMPLETE_BLOCKS, evaluation.CheckId.EXACT_TOKENS,
    evaluation.CheckId.BUDGET_BOUND, evaluation.CheckId.RESULT_CONSISTENCY,
))
def test_hard_failures_cannot_be_averaged_into_partial(check: evaluation.CheckId) -> None:
    status, reasons = evaluation._classify_primary(
        ((check, False), (evaluation.CheckId.EXPECTED_PRIMARY_IDS, False)),
        (evaluation.ReasonCode.EXPECTED_MEMORY_NOT_SELECTED,))
    assert status is evaluation.Verdict.FAIL
    assert evaluation.ReasonCode.EXPECTED_MEMORY_NOT_SELECTED not in reasons


@pytest.mark.real_model
def test_primary_serializer_revalidates_and_rejects_unapproved_fields(real_primary: tuple) -> None:
    _, reports = real_primary
    with pytest.raises(TypeError):
        replace(reports[0], query="PRIVATE-SENTINEL")
    with pytest.raises(ValueError):
        replace(reports[0], checks=(("private-query", True),))
    forged = replace(reports[0])
    object.__setattr__(forged, "selected_ids", ("user_a",))
    with pytest.raises(ValueError, match="^invalid_evaluation_report$"):
        evaluation.serialize_primary_evidence((forged, *reports[1:]))
