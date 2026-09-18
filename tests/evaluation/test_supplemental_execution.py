"""Frozen real workload probes; no threshold tuning or credential seeding."""

from __future__ import annotations

import json
import os
import sqlite3
import weakref
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from conversational_memory.application import MemoryService
from conversational_memory.entrypoints import evaluation

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def completed(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    cache = os.environ.get("CONVERSATIONAL_MEMORY_MODEL_CACHE")
    assert cache, "verified offline model cache required; no skips"
    directory = tmp_path_factory.mktemp("m10-supplemental") / "stores"
    paths = [ROOT / "experiments/naive_baseline/workload" / name for name in evaluation._FILES]
    paths += [ROOT / "experiments" / name for name in ("baseline_protocol.md", "baseline_results.csv")]
    before = tuple(path.read_bytes() for path in paths)
    calls = []
    forgetting = []
    original_retrieve, original_forget = MemoryService.retrieve, MemoryService.forget

    def retrieve(service, context, request):
        clock_calls = service._clock.calls
        result = original_retrieve(service, context, request)
        calls.append((weakref.ref(service), context, request, result, service._clock.calls - clock_calls))
        return result

    def forget(service, context, request):
        result = original_forget(service, context, request)
        forgetting.append((context, request, result))
        return result

    with patch.object(MemoryService, "retrieve", retrieve), patch.object(MemoryService, "forget", forget):
        report = evaluation.execute_verified_workloads(ROOT, directory, Path(cache))
    assert before == tuple(path.read_bytes() for path in paths)
    print(json.dumps([{"case_id": case.case_id.value, "status": case.status.value,
                       "repeatable": case.repeatable, "restart_verified": case.restart_verified,
                       "outcome": case.outcome, "tokens_used": case.tokens_used}
                      for case in report.cases], sort_keys=True, separators=(",", ":")))
    return directory, report, calls, forgetting


@pytest.mark.real_model
@pytest.mark.parametrize("index", range(6))
def test_restart_repeat_zero_budget_and_hard_safety(completed: tuple, index: int) -> None:
    directory, report, calls, _ = completed
    case = report.cases[index]
    assert case.status is (evaluation.Verdict.PARTIAL if index < 3 else evaluation.Verdict.PASS)
    assert case.restart_verified and case.repeatable and case.executed
    checks = dict(case.checks)
    assert all(value for key, value in checks.items() if key not in evaluation._QUALITY_CHECKS)
    owner = "user_a" if index == 3 else f"m10-case{index + 1}-user"
    own_calls = [call for call in calls if call[1].user_id == owner]
    primary, repeated, zero = own_calls[:3]
    assert primary[0] is not repeated[0]
    assert primary[1].request_id == f"m10-case{index + 1}-primary"
    assert repeated[1].request_id == f"m10-case{index + 1}-post-restart"
    for invocation in (primary, repeated):
        assert invocation[2].intent.value == "current"
        assert (invocation[2].limit, invocation[2].token_budget) == (10, 128)
    assert evaluation._retrieval_fingerprint(primary[3]) == evaluation._retrieval_fingerprint(repeated[3])
    result = zero[3]
    assert zero[2].token_budget == 0 and zero[2].limit == 10
    assert not result.memories and not result.context and not result.included_memory_ids and result.tokens_used == 0
    assert result.outcome.value == ("budget_excluded" if primary[3].memories else primary[3].outcome.value)
    assert {item.reason.value for item in result.exclusions} <= {"budget_exceeded", "below_relevance_threshold"}
    if primary[3].memories:
        assert set(primary[3].included_memory_ids) <= {
            item.memory_id for item in result.exclusions if item.reason.value == "budget_exceeded"}
    else:
        assert evaluation._retrieval_fingerprint(result) == evaluation._retrieval_fingerprint(primary[3])
    assert primary[4] == repeated[4] == zero[4] == 1
    assert (directory / case.case_id.value / "index/memory.faiss").is_file()


@pytest.mark.real_model
def test_sensitive_control_and_persisted_forgetting(completed: tuple) -> None:
    directory, report, calls, forgetting = completed
    assert len(forgetting) == 2
    rejected_context, rejected_request, rejected_result = forgetting[0]
    assert rejected_context.request_id == "m10-case5-forget-rejected"
    assert rejected_request.memory_id == "m10-case5-c0000"
    assert rejected_result.outcome.value == "not_found" and rejected_result.memory_id is None
    _, request, result = forgetting[1]
    assert request.memory_id == "m10-case5-f0000" and result.cleanup_complete
    assert report.cases[4].adaptation.control_actions == 1
    assert report.cases[4].adaptation.admission_attempts == 16
    connection = sqlite3.connect(directory / "case5/memory.sqlite3")
    try:
        assert connection.execute("SELECT count(*) FROM memories").fetchone()[0] == 15
        assert connection.execute("SELECT count(*) FROM memories WHERE memory_id='m10-case5-c0000'").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM memories WHERE content LIKE '%sk-test-123456789%'").fetchone()[0] == 0
        row = connection.execute("SELECT requested_at,completed_at,cleanup_state FROM memory_forgetting").fetchone()
        assert row == ("2026-01-03T00:00:00.000000Z", "2026-01-03T00:00:01.000000Z", "complete")
        assert connection.execute("SELECT count(*) FROM memory_vector_mappings").fetchone()[0] == 14
    finally:
        connection.close()
    supplemental = [call for call in calls if call[1].user_id == "m10-case5-user"][3:]
    assert [call[2].intent.value for call in supplemental] == ["current", "historical"]
    assert all("m10-case5-f0000" not in call[3].included_memory_ids for call in supplemental)
    assert supplemental[0][4] == 1 and supplemental[1][4] == 0


@pytest.mark.real_model
def test_report_determinism_and_recursive_object_json_byte_privacy(completed: tuple) -> None:
    _, report, _, _ = completed
    output = evaluation.serialize_report(report)
    assert output == evaluation.serialize_report(report)
    payload = json.loads(output)
    assert payload["totals"] == {"pass": 3, "partial": 3, "fail": 0}
    forbidden = {"content", "query", "user_id", "subject", "value", "exception", "credentials", "key", "path"}

    def scan(value):
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for item in value.values():
                scan(item)
        elif isinstance(value, list):
            for item in value:
                scan(item)
    scan(payload)
    for case in evaluation.load_fixed_workload(ROOT).cases:
        for text in (case.query, *(turn.content for turn in case.conversation), *case.filler_memories):
            assert text not in output and text.encode() not in output.encode()
    for sentinel in ("sk-test-123456789", "user_a", "user_b", "m10-case1-user", "m10-local-evaluation-key-32-bytes!!"):
        assert sentinel not in output and sentinel.encode() not in output.encode()


@pytest.mark.real_model
def test_repeatability_compares_context_and_ordered_exclusions_not_only_ids(completed: tuple) -> None:
    _, _, calls, _ = completed
    empty = calls[0][3]
    assert evaluation._retrieval_fingerprint(empty) != evaluation._retrieval_fingerprint(replace(empty, context="DIFFERENT"))
    assert len(empty.exclusions) > 1
    assert evaluation._retrieval_fingerprint(empty) != evaluation._retrieval_fingerprint(
        replace(empty, exclusions=tuple(reversed(empty.exclusions))))


@pytest.mark.parametrize("check", (evaluation.CheckId.REPEATABLE, evaluation.CheckId.RESTART_VERIFIED,
                                 evaluation.CheckId.ZERO_BUDGET_EMPTY, evaluation.CheckId.ZERO_BUDGET_OUTCOME,
                                 evaluation.CheckId.FORGETTING_COMPLETE, evaluation.CheckId.NO_REACTIVATION))
def test_supplemental_failure_never_becomes_quality_partial(check: evaluation.CheckId) -> None:
    status, reasons = evaluation._classify_completed(((check, False),),
        (evaluation.ReasonCode.EXPECTED_MEMORY_NOT_SELECTED,))
    assert status is evaluation.Verdict.FAIL
    assert evaluation.ReasonCode.EXPECTED_MEMORY_NOT_SELECTED not in reasons
