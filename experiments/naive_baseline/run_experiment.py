import csv
import json
from pathlib import Path

from pipeline import run_case


BASE_DIR = Path(__file__).resolve().parent
WORKLOAD_DIR = BASE_DIR / "workload"

PROJECT_ROOT = BASE_DIR.parent.parent

RESULTS_FILE = PROJECT_ROOT / "experiments" / "baseline_results.csv"
ERRORS_FILE = PROJECT_ROOT / "experiments" / "error_examples.jsonl"


WORKLOAD_FILES = [
    "case1_irrelevant_contradictory.json",
    "case2_preference_change.json",
    "case3_long_context.json",
    "case4_multi_user.json",
    "case5_sensitive_memory.json",
    "case6_cold_start.json"]


def run_all_cases():
    """Run the naive baseline against all six workload cases."""

    results = []
    error_examples = []

    for filename in WORKLOAD_FILES:
        case_path = WORKLOAD_DIR / filename

        print(f"\nRunning {filename}...")

        result = run_case(case_path)

        evaluation = result["evaluation"]

        observed_failures = [
            check["failure"]
            for check in evaluation["checks"]
            if check["observed"]]

        results.append(
               {"case_id": result["case_id"],
                "memory_count": result["memory_count"],
                "retrieved_count": result["retrieved_count"],
                "latency_ms": round(result["latency_ms"], 3),
                "failures_observed": len(observed_failures),
                "failure_names": "; ".join(observed_failures)})

        if observed_failures:
            error_examples.append(
                   {"case_id": result["case_id"],
                    "retrieved_memories": result["retrieved_memories"],
                    "evaluation": evaluation,
                    "context": result["context"]})

        print(f" Memories: {result['memory_count']}")
        print(f" Retrieved: {result['retrieved_count']}")
        print(f" Latency: {result['latency_ms']:.3f} ms")
        print(f" Failures observed: {len(observed_failures)}")

    write_results(results)
    write_error_examples(error_examples)

    print("\nExperiment complete.")
    print(f"Results written to: {RESULTS_FILE}")
    print(f"Error examples written to: {ERRORS_FILE}")


def write_results(results):
    """Write summary results to CSV."""

    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "case_id",
        "memory_count",
        "retrieved_count",
        "latency_ms",
        "failures_observed",
        "failure_names"]

    with open(
        RESULTS_FILE,
        "w",
        newline="",
        encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        writer.writeheader()
        writer.writerows(results)


def write_error_examples(error_examples):
    """Write detailed failure examples as JSONL."""

    ERRORS_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(
        ERRORS_FILE,
        "w",
        encoding="utf-8") as f:
        for example in error_examples:
            f.write(
                json.dumps(
                    example,
                    ensure_ascii=False) + "\n")


if __name__ == "__main__":
    run_all_cases()