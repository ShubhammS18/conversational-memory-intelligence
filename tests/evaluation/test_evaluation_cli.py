"""CLI exit policy, safe failure reports, and real offline command coverage."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conversational_memory.application.errors import AuthorizationError, ServiceUnavailableError
from conversational_memory.entrypoints import cli, evaluation

ROOT = Path(__file__).resolve().parents[2]


def _report(*, hard_failure: bool = False) -> evaluation.EvaluationReport:
    workload = evaluation.load_fixed_workload(ROOT)
    cases = []
    for index, (case, baseline) in enumerate(zip(workload.cases, workload.baseline, strict=True)):
        checks = {key: True for key in evaluation._COMMON_CHECKS}
        checks[evaluation.CheckId.EXPECTED_PRIMARY_IDS if index < 4
               else evaluation.CheckId.EXPECTED_EMPTY_OUTCOME] = index >= 3
        if index < 3:
            checks[evaluation.CheckId.EXPLICIT_LINKS] = True
        if index == 0:
            checks[evaluation.CheckId.APARTMENT_ABSENT] = not hard_failure
        if index == 4:
            checks.update(dict.fromkeys((evaluation.CheckId.SENSITIVE_REJECTED,
                evaluation.CheckId.REJECTED_TARGET_NOT_FOUND, evaluation.CheckId.FORGETTING_COMPLETE,
                evaluation.CheckId.DELETED_CURRENT_ABSENT, evaluation.CheckId.DELETED_HISTORICAL_ABSENT,
                evaluation.CheckId.NO_REACTIVATION), True))
        status = evaluation.Verdict.PARTIAL if index < 3 else evaluation.Verdict.PASS
        reasons = (evaluation.ReasonCode.EXPECTED_MEMORY_NOT_SELECTED,) if index < 3 \
            else (evaluation.ReasonCode.EXPECTED_BEHAVIOR_CONFIRMED,)
        if index == 0 and hard_failure:
            status, reasons = evaluation.Verdict.FAIL, (evaluation.ReasonCode.SAFETY_VIOLATION,)
        cases.append(evaluation.CaseReport(case_id=case.case_id, baseline=baseline,
            adaptation=evaluation.AdaptationEvidence(evaluation._USER_COUNTS[index],
                evaluation._FILLER_COUNTS[index], evaluation._USER_COUNTS[index] + evaluation._FILLER_COUNTS[index],
                (18, 3, 50, 17, 15, 17)[index], int(index == 4), int(index == 4)),
            status=status, reason_codes=reasons, checks=tuple(sorted(checks.items())), executed=True,
            outcome="memories_selected" if index == 3 else "no_relevant_memory",
            selected_ids=("m10-case4-c0000",) if index == 3 else (), exclusions=(),
            tokens_used=20 if index == 3 else 0, repeatable=True, restart_verified=True))
    return evaluation.EvaluationReport(tuple(cases))


def test_parser_documents_exit_codes_and_has_no_tuning_options(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.build_parser().parse_args(["evaluate-fixed-workload"]).command == "evaluate-fixed-workload"
    with pytest.raises(SystemExit) as error:
        cli.main(["evaluate-fixed-workload", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    for phrase in ("pre-production", "PARTIAL", "0", "1", "2", "offline"):
        assert phrase in output
    assert "--threshold" not in output and "--token-budget" not in output


@pytest.mark.parametrize("hard_failure,expected_exit", ((False, 0), (True, 1)))
def test_cli_reuses_runner_and_preserves_case_verdicts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    hard_failure: bool, expected_exit: int, tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONVERSATIONAL_MEMORY_MODEL_CACHE", str(tmp_path))
    expected = _report(hard_failure=hard_failure)
    calls = []

    def run(repository, stores, cache, *, progress):
        calls.append((repository, stores, cache))
        print("PRIVATE-CONTENT-QUERY-AUTH-KEY-ERROR")
        for case in expected.cases:
            progress(case)
        return expected
    monkeypatch.setattr(evaluation, "execute_verified_workloads", run)
    assert cli.main(["evaluate-fixed-workload"]) == expected_exit
    streams = capsys.readouterr()
    assert streams.err == ""
    assert streams.out == evaluation.serialize_report(expected)
    assert len(calls) == 1 and calls[0][0] == ROOT and calls[0][2] == tmp_path
    assert not calls[0][1].exists(), "temporary stores must be cleaned"


@pytest.mark.parametrize("error,expected_exit,reason", (
    (evaluation.EvaluationInputError(evaluation.ReasonCode.FIXTURE_MISMATCH), 2, "fixture_mismatch"),
    (evaluation.EvaluationInputError(evaluation.ReasonCode.BASELINE_MISMATCH), 2, "baseline_mismatch"),
    (ServiceUnavailableError("PRIVATE-MODEL-PATH-ERROR"), 2, "prerequisite_unavailable"),
    (AuthorizationError("PRIVATE-OWNER-CONTENT"), 1, "safety_violation"),
    (RuntimeError("PRIVATE-QUERY-SECRET-ERROR"), 1, "unexpected_operation_failure"),
))
def test_safe_failure_reports_never_invent_retrieval_evidence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path,
    error: Exception, expected_exit: int, reason: str,
) -> None:
    monkeypatch.setenv("CONVERSATIONAL_MEMORY_MODEL_CACHE", str(tmp_path))

    def fail(*args, **kwargs):
        print("PRIVATE-LOG-CONTENT")
        raise error
    monkeypatch.setattr(evaluation, "execute_verified_workloads", fail)
    assert cli.main(["evaluate-fixed-workload"]) == expected_exit
    streams = capsys.readouterr()
    assert streams.err == "" and "PRIVATE" not in streams.out
    cases = json.loads(streams.out)["cases"]
    assert len(cases) == 6
    assert all(case["status"] == "FAIL" and reason in case["reason_codes"] for case in cases)
    assert all(not case["executed"] and case["outcome"] is None and case["tokens_used"] is None for case in cases)


def test_partial_execution_failure_retains_only_actual_evidence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONVERSATIONAL_MEMORY_MODEL_CACHE", str(tmp_path))
    completed = _report().cases[0]

    def fail(*args, progress):
        progress(completed)
        raise RuntimeError("PRIVATE-PARTIAL-ERROR")
    monkeypatch.setattr(evaluation, "execute_verified_workloads", fail)
    assert cli.main(["evaluate-fixed-workload"]) == 1
    cases = json.loads(capsys.readouterr().out)["cases"]
    assert cases[0]["status"] == "PARTIAL" and cases[0]["executed"]
    assert all(not case["executed"] for case in cases[1:])


def test_missing_explicit_cache_fails_before_runner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CONVERSATIONAL_MEMORY_MODEL_CACHE", raising=False)
    monkeypatch.setattr(evaluation, "execute_verified_workloads", lambda *a, **k: pytest.fail("runner forbidden"))
    assert cli.main(["evaluate-fixed-workload"]) == 2
    assert json.loads(capsys.readouterr().out)["totals"] == {"pass": 0, "partial": 0, "fail": 6}


def test_unknown_cli_arguments_are_rejected_without_echoing_private_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["evaluate-fixed-workload", "--threshold=PRIVATE-ARGUMENT-SECRET"]) == 2
    streams = capsys.readouterr()
    assert streams.err == "" and "PRIVATE" not in streams.out
    assert all(not case["executed"] for case in json.loads(streams.out)["cases"])


def test_failure_after_primary_retains_actual_result_counts_and_control_evidence() -> None:
    source = _report().cases[4]
    unfinished = evaluation.CaseReport(case_id=source.case_id, baseline=source.baseline,
        adaptation=source.adaptation, status=evaluation.Verdict.FAIL,
        reason_codes=(evaluation.ReasonCode.PREREQUISITE_UNAVAILABLE,), checks=source.checks,
        executed=True, outcome=source.outcome, selected_ids=source.selected_ids,
        exclusions=source.exclusions, tokens_used=source.tokens_used)
    report = evaluation.failed_evaluation_report(evaluation.ReasonCode.UNEXPECTED_OPERATION_FAILURE,
                                                 (unfinished,), prerequisite=False)
    failed = report.cases[4]
    assert failed.status is evaluation.Verdict.FAIL and failed.executed
    assert failed.adaptation == source.adaptation and failed.outcome == source.outcome
    assert not failed.repeatable and not failed.restart_verified


@pytest.mark.parametrize("reason,check", ((evaluation.ReasonCode.FIXTURE_MISMATCH,
                                         evaluation.CheckId.FIXTURE_BYTES),
                                        (evaluation.ReasonCode.BASELINE_MISMATCH,
                                         evaluation.CheckId.BASELINE_BYTES)))
def test_late_input_integrity_failure_is_hard_failure_even_after_completed_cases(
    reason: evaluation.ReasonCode, check: evaluation.CheckId,
) -> None:
    recorded = _report().cases[:2]
    report = evaluation.failed_evaluation_report(reason, recorded, prerequisite=True)
    assert all(case.status is evaluation.Verdict.FAIL and dict(case.checks)[check] is False
               for case in report.cases)
    assert all(case.executed for case in report.cases[:2])
    assert all(not case.executed for case in report.cases[2:])


@pytest.mark.real_model
def test_real_offline_cli_is_case_specific_safe_and_preserves_original_bytes() -> None:
    paths = [ROOT / "experiments/naive_baseline/workload" / name for name in evaluation._FILES]
    paths += [ROOT / "experiments" / name for name in ("baseline_protocol.md", "baseline_results.csv")]
    before = tuple(path.read_bytes() for path in paths)
    assert os.environ.get("HF_HUB_OFFLINE") == "1" and os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    assert os.environ.get("CUDA_VISIBLE_DEVICES") == ""
    process = subprocess.run([sys.executable, "-m", "conversational_memory.entrypoints.cli",
        "evaluate-fixed-workload"], cwd=ROOT, capture_output=True, timeout=180, check=False)
    assert process.returncode == 0
    assert process.stderr == b""
    output = process.stdout.decode("utf-8")
    data = json.loads(output)
    assert data["implementation_stage"] == "pre-production reference implementation"
    assert data["totals"] == {"pass": 3, "partial": 3, "fail": 0}
    assert [case["status"] for case in data["cases"]] == ["PARTIAL"] * 3 + ["PASS"] * 3
    assert all(case["repeatable"] and case["restart_verified"] for case in data["cases"])
    assert data["settings"] == json.loads(evaluation.serialize_report(_report()))["settings"]
    assert output == json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    forbidden = {"content", "query", "user_id", "subject", "value", "exception", "credentials", "key", "path"}

    def scan(item):
        if isinstance(item, dict):
            assert forbidden.isdisjoint(item)
            for value in item.values():
                scan(value)
        elif isinstance(item, list):
            for value in item:
                scan(value)
    scan(data)
    for case in evaluation.load_fixed_workload(ROOT).cases:
        for text in (case.query, *(turn.content for turn in case.conversation), *case.filler_memories):
            assert text not in output and text.encode() not in process.stdout
    for sentinel in ("sk-test-123456789", "user_a", "user_b", "m10-case1-user", "m10-local-evaluation-key-32-bytes!!"):
        assert sentinel not in output and sentinel.encode() not in process.stdout
    assert before == tuple(path.read_bytes() for path in paths)
