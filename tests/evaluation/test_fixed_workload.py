"""Contract tests only: no admissions, embeddings, retrieval, or CLI execution."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from conversational_memory.entrypoints.evaluation import (
    AdaptationEvidence,
    CaseId,
    CaseReport,
    CheckId,
    EvaluationInputError,
    EvaluationReport,
    EvaluationSettings,
    ReasonCode,
    ReportExclusion,
    Verdict,
    load_fixed_workload,
    serialize_report,
)

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    "case1_irrelevant_contradictory.json", "case2_preference_change.json",
    "case3_long_context.json", "case4_multi_user.json",
    "case5_sensitive_memory.json", "case6_cold_start.json",
)


def _copy_inputs(destination: Path) -> Path:
    experiments = destination / "experiments"
    shutil.copytree(ROOT / "experiments/naive_baseline/workload",
                    experiments / "naive_baseline/workload")
    for name in ("baseline_protocol.md", "baseline_results.csv"):
        shutil.copyfile(ROOT / "experiments" / name, experiments / name)
    return destination


def _report() -> EvaluationReport:
    workload = load_fixed_workload(ROOT)
    cases = tuple(CaseReport(
        case_id=case.case_id, baseline=baseline,
        adaptation=AdaptationEvidence(
            user_candidate_count=sum(turn.role == "user" for turn in case.conversation)
            - (case.case_id is CaseId.CASE5),
            filler_count=len(case.filler_memories), admission_attempts=0,
            accepted_count=0, rejected_count=0,
            control_actions=0,
        ),
        status=Verdict.FAIL, reason_codes=(ReasonCode.PREREQUISITE_UNAVAILABLE,),
        checks=((CheckId.FIXTURE_BYTES, True),), executed=False,
        outcome=None, selected_ids=(), exclusions=(), tokens_used=None,
    ) for case, baseline in zip(workload.cases, workload.baseline, strict=True))
    return EvaluationReport(cases=cases)


def test_exact_discovery_order_preserves_all_original_bytes() -> None:
    paths = [ROOT / "experiments/naive_baseline/workload" / name for name in FILES]
    before = tuple(path.read_bytes() for path in paths)
    workload = load_fixed_workload(ROOT)
    assert tuple(case.case_id for case in workload.cases) == tuple(CaseId)
    assert tuple(case.raw_bytes for case in workload.cases) == before
    assert before == tuple(path.read_bytes() for path in paths)
    assert tuple(case.sha256 for case in workload.cases) == tuple(
        hashlib.sha256(raw).hexdigest() for raw in before)
    assert tuple(len(case.conversation) for case in workload.cases) == (6, 6, 50, 4, 4, 4)
    assert workload.cases[1].filler_memories == ()
    assert workload.cases[2].signal_memory_positions == (10, 25, 40)
    assert workload.cases[2].conversation[0].content == workload.cases[2].conversation[9].content
    assert workload.cases[3].conversation[2].user_id == "user_b"
    assert all(row.failures_observed == 1 for row in workload.baseline)


@pytest.mark.parametrize("artifact", (*FILES, "baseline_protocol.md", "baseline_results.csv"))
def test_hash_mismatch_fails_closed_without_disclosing_bytes(
    tmp_path: Path, artifact: str,
) -> None:
    root = _copy_inputs(tmp_path)
    parent = root / "experiments"
    if artifact in FILES:
        parent = parent / "naive_baseline/workload"
    path = parent / artifact
    path.write_bytes(path.read_bytes() + b"PRIVATE-CONTENT-SENTINEL")
    with pytest.raises(EvaluationInputError) as error:
        load_fixed_workload(root)
    expected = "fixture_mismatch" if artifact in FILES else "baseline_mismatch"
    assert str(error.value) == expected
    assert "PRIVATE" not in repr(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize("change", ("missing", "extra", "renamed", "unknown_field"))
def test_discovery_and_unknown_fixture_fields_fail_closed(tmp_path: Path, change: str) -> None:
    root = _copy_inputs(tmp_path)
    directory = root / "experiments/naive_baseline/workload"
    path = directory / FILES[0]
    if change == "missing":
        path.unlink()
    elif change == "extra":
        (directory / "case7.json").write_bytes(b"{}")
    elif change == "renamed":
        path.rename(directory / "replacement.json")
    else:
        payload = json.loads(path.read_bytes())
        payload["unknown"] = "PRIVATE-SENTINEL"
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="^fixture_mismatch$"):
        load_fixed_workload(root)


def test_missing_baseline_is_safe_and_sensitive_fixture_repr_is_redacted(tmp_path: Path) -> None:
    root = _copy_inputs(tmp_path)
    (root / "experiments/baseline_results.csv").unlink()
    with pytest.raises(EvaluationInputError, match="^baseline_mismatch$"):
        load_fixed_workload(root)
    case = load_fixed_workload(ROOT).cases[4]
    assert "sk-test-" not in repr(case)
    assert "sk-test-" not in repr(case.conversation)


def test_contracts_are_deeply_immutable() -> None:
    report = _report()
    with pytest.raises(FrozenInstanceError):
        report.cases = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        report.cases[0].adaptation.accepted_count = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="^invalid_evaluation_report$"):
        replace(report, cases=list(report.cases))
    with pytest.raises(ValueError):
        replace(report.cases[0], checks=[(CheckId.FIXTURE_BYTES, True)])


@pytest.mark.parametrize("enum_type,unknown", (
    (CaseId, "case7"), (Verdict, "IMPROVED"), (ReasonCode, "raw-exception-secret"),
    (CheckId, "unknown_check"),
))
def test_closed_vocabularies(enum_type: type, unknown: str) -> None:
    with pytest.raises(ValueError):
        enum_type(unknown)
    assert {item.value for item in Verdict} == {"PASS", "PARTIAL", "FAIL"}
    assert len(ReasonCode) == 11
    assert len(CheckId) == 30


@pytest.mark.parametrize("field,value", (
    ("status", "FAIL"), ("reason_codes", ("prerequisite_unavailable",)),
    ("checks", (("unknown_check", True),)),
    ("checks", ((CheckId.FIXTURE_BYTES, 1),)),
    ("checks", ((CheckId.FIXTURE_BYTES, True), (CheckId.FIXTURE_BYTES, False))),
    ("executed", 1), ("token_budget", True), ("token_budget", 256),
    ("tokens_used", 0), ("selected_ids", ("user_a",)),
    ("outcome", "raw-secret"), ("repeatable", "yes"),
))
def test_strict_fields_reject_coercion_and_invalid_profiles(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="^invalid_evaluation_report$"):
        replace(_report().cases[0], **{field: value})


@pytest.mark.parametrize("field", (
    "unknown", "content", "query", "user_id", "subject", "value", "credentials",
    "exception", "hmac_key", "vectors", "path", "event_dump",
))
def test_unknown_and_forbidden_output_fields_are_rejected(field: str) -> None:
    with pytest.raises(TypeError):
        replace(_report().cases[0], **{field: "PRIVATE-SENTINEL"})


@pytest.mark.parametrize("field,value", (
    ("threshold", True), ("threshold", float("nan")), ("threshold", 0.49),
    ("limit", 11), ("device", "cuda"), ("revision", "main"),
    ("frozen_time", "caller-time"),
))
def test_settings_are_exact_and_strict(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="^invalid_evaluation_report$"):
        replace(EvaluationSettings(), **{field: value})


def test_deterministic_json_and_recursive_object_byte_privacy() -> None:
    report = _report()
    encoded = serialize_report(report)
    assert encoded == serialize_report(report) == serialize_report(_report())
    payload = json.loads(encoded)
    assert encoded == json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False, allow_nan=False) + "\n"
    assert payload["implementation_stage"] == "pre-production reference implementation"
    assert payload["totals"] == {"pass": 0, "partial": 0, "fail": 6}
    assert [case["case_id"] for case in payload["cases"]] == [case.value for case in CaseId]
    assert all(not case["executed"] and case["tokens_used"] is None for case in payload["cases"])
    forbidden = {"content", "query", "user_id", "subject", "value", "credentials", "exception"}

    def scan(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for nested in value.values():
                scan(nested)
        elif isinstance(value, list):
            for nested in value:
                scan(nested)
    scan(payload)
    sentinels = ["sk-test-123456789", "user_a", "user_b", "m10-case1-user"]
    for case in load_fixed_workload(ROOT).cases:
        sentinels.append(case.query)
        sentinels.extend(turn.content for turn in case.conversation)
        sentinels.extend(case.filler_memories)
    for sentinel in sentinels:
        assert sentinel not in encoded
        assert sentinel.encode() not in encoded.encode("utf-8")


@pytest.mark.parametrize("field,value", (
    ("status", "PRIVATE-SENTINEL"), ("reason_codes", ("sk-test-123456789",)),
    ("checks", (("query", "PRIVATE-SENTINEL"),)),
    ("selected_ids", ("user_a",)),
))
def test_serializer_revalidates_frozen_instance_bypass(field: str, value: object) -> None:
    report = _report()
    object.__setattr__(report.cases[0], field, value)
    with pytest.raises(ValueError, match="^invalid_evaluation_report$") as error:
        serialize_report(report)
    assert "PRIVATE" not in str(error.value)


def test_report_order_baseline_and_exclusions_are_not_replaceable() -> None:
    report = _report()
    with pytest.raises(ValueError):
        replace(report, cases=tuple(reversed(report.cases)))
    with pytest.raises(ValueError):
        replace(report.cases[0].baseline, failure_names="PRIVATE-SENTINEL")
    with pytest.raises(ValueError):
        ReportExclusion(memory_id="user_a", reason="budget_exceeded")
    with pytest.raises(ValueError):
        ReportExclusion(memory_id="m10-case1-c0004", reason="PRIVATE-SENTINEL")


def _executed_case(status: Verdict, reason: ReasonCode) -> CaseReport:
    case = _report().cases[0]
    return replace(case, executed=True, outcome="memories_selected",
                   selected_ids=("m10-case1-c0004",), tokens_used=32,
                   repeatable=True, restart_verified=True, status=status,
                   reason_codes=(reason,), checks=tuple((check, True) for check in CheckId),
                   adaptation=replace(case.adaptation, admission_attempts=18, accepted_count=18))


@pytest.mark.parametrize("status,reason", (
    (Verdict.PASS, ReasonCode.EXPECTED_BEHAVIOR_CONFIRMED),
    (Verdict.PARTIAL, ReasonCode.EXPECTED_MEMORY_NOT_SELECTED),
))
def test_nonfailure_reports_cannot_omit_mandatory_checks(
    status: Verdict, reason: ReasonCode,
) -> None:
    case = _executed_case(status, reason)
    with pytest.raises(ValueError, match="^invalid_evaluation_report$"):
        replace(case, checks=((CheckId.FIXTURE_BYTES, True),))
    with pytest.raises(ValueError, match="^invalid_evaluation_report$"):
        replace(case, checks=tuple((key, key is not CheckId.BUDGET_BOUND)
                                  for key, _ in case.checks))


@pytest.mark.parametrize("reason", tuple(ReasonCode))
def test_every_approved_reason_round_trips_without_free_text(reason: ReasonCode) -> None:
    status = (Verdict.PASS if reason is ReasonCode.EXPECTED_BEHAVIOR_CONFIRMED
              else Verdict.PARTIAL if reason in (
                  ReasonCode.EXPECTED_MEMORY_NOT_SELECTED, ReasonCode.EXTRA_MEMORY_SELECTED,
                  ReasonCode.UNEXPECTED_EMPTY_OUTCOME) else Verdict.FAIL)
    report = _report()
    case = _executed_case(status, reason)
    payload = json.loads(serialize_report(replace(report, cases=(case, *report.cases[1:]))))
    assert payload["cases"][0]["reason_codes"] == [reason.value]
    assert payload["cases"][0]["status"] == status.value


@pytest.mark.parametrize("check", tuple(CheckId))
def test_every_approved_check_round_trips(check: CheckId) -> None:
    report = _report()
    case = replace(report.cases[0], checks=((check, False),))
    payload = json.loads(serialize_report(replace(report, cases=(case, *report.cases[1:]))))
    assert payload["cases"][0]["checks"] == {check.value: False}


@pytest.mark.parametrize("field,value", (
    ("memory_count", True), ("failure_names", "PRIVATE-SENTINEL"),
))
def test_serializer_revalidates_baseline_bypass(field: str, value: object) -> None:
    report = _report()
    object.__setattr__(report.cases[0].baseline, field, value)
    with pytest.raises(ValueError, match="^invalid_evaluation_report$"):
        serialize_report(report)


def test_serializer_revalidates_nested_settings_and_adaptation() -> None:
    report = _report()
    object.__setattr__(report.settings, "threshold", float("nan"))
    with pytest.raises(ValueError):
        serialize_report(report)
    report = _report()
    object.__setattr__(report.cases[0].adaptation, "accepted_count", True)
    with pytest.raises(ValueError):
        serialize_report(report)
