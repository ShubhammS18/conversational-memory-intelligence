# Evaluation Matrix

# This file defines the fixed D3 regression workload and the
# acceptance criteria for evaluating the D4 architecture.
#
# It does NOT claim that the D4 system has passed these cases yet.
# It records what must be evaluated and separates hard invariants
# from quality/correctness checks.


EVALUATION_CASES = [
    {
        "case_id": "case1",
        "name": "Contradictory memories",
        "d3_failure": "contradictory_memories_retrieved",
        "d4_component": "conflict_resolution",
        "acceptance": (
            "Current-state retrieval must not use a superseded "
            "memory as the current state."
        ),
        "type": "correctness"
    },
    {
        "case_id": "case2",
        "name": "Changing state over time",
        "d3_failure": "old_and_new_decisions_retrieved",
        "d4_component": "lifecycle_and_conflict_resolution",
        "acceptance": (
            "Current-state queries must select the current memory; "
            "historical queries may recover superseded history."
        ),
        "type": "correctness"
    },
    {
        "case_id": "case3",
        "name": "Historical/context pollution",
        "d3_failure": "multiple_historical_architectural_decisions_retrieved",
        "d4_component": "context_construction",
        "acceptance": (
            "Selected memory context must remain within the hard "
            "token budget and avoid unnecessary historical pollution."
        ),
        "type": "hard_budget_and_quality"
    },
    {
        "case_id": "case4",
        "name": "Multiple users",
        "d3_failure": "different_users_memory_retrieved",
        "d4_component": "user_scoped_retrieval",
        "acceptance": (
            "Zero unauthorized user memories may enter the "
            "requesting user's retrieval results."
        ),
        "type": "hard_invariant"
    },
    {
        "case_id": "case5",
        "name": "Sensitive information",
        "d3_failure": "sensitive_memory_remains_retrievable",
        "d4_component": "admission_and_forgetting",
        "acceptance": (
            "Rejected sensitive information must not become durable "
            "memory, and forgotten memory must not be retrievable."
        ),
        "type": "hard_invariant"
    },
    {
        "case_id": "case6",
        "name": "Cold start / no relevant memory",
        "d3_failure": "unrelated_memories_retrieved_for_cold_start",
        "d4_component": "relevance_and_no_memory",
        "acceptance": (
            "When no stored memory is sufficiently relevant, the "
            "system must be able to return no memory."
        ),
        "type": "correctness"
    }
]


def print_evaluation_matrix():
    print("D4 Phase 9 — Fixed Evaluation Workload")
    print("=" * 60)

    for case in EVALUATION_CASES:
        print(f"\n{case['case_id']}: {case['name']}")
        print(f"D3 failure: {case['d3_failure']}")
        print(f"D4 component: {case['d4_component']}")
        print(f"Evaluation type: {case['type']}")
        print(f"Acceptance: {case['acceptance']}")


if __name__ == "__main__":
    print_evaluation_matrix()