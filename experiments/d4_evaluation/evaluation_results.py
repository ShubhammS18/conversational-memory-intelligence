# D4 Evaluation Results
#
# This records actual evaluation outcomes separately from
# the fixed evaluation workload.

RESULT_STATUSES = {
    "PASS",
    "IMPROVED",
    "PARTIAL",
    "FAIL",
    "NOT_EVALUATED"}

D3_RESULTS = {
    "case1": "Contradictory memories retrieved",
    "case2": "Old + new decisions retrieved",
    "case3": "Multiple historical decisions retrieved",
    "case4": "Cross-user retrieval",
    "case5": "Sensitive memory retrievable",
    "case6": "Unrelated cold-start retrieval"}


def record_result(
    case_id,
    d3_result,
    d4_result,
    status,
    evidence):
    if status not in RESULT_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    return {
        "case_id": case_id,
        "d3_result": d3_result,
        "d4_result": d4_result,
        "status": status,
        "evidence": evidence}


results = [
    record_result(
        case_id="case1",
        d3_result=D3_RESULTS["case1"],
        d4_result=("Superseded memory excluded from current active memories; "
                   "explicit conflict resolved by recency and authority"),
        status="PASS",
        evidence=("test_supersession.py: Case 1 and Case 2 passed; "
                  "explicit and authority-first conflict resolution were validated.")
    ),

    record_result(
        case_id="case2",
        d3_result=D3_RESULTS["case2"],
        d4_result=("Newer memory supersedes the older memory while the "
               "older memory remains available as historical state"),
        status="PASS",
        evidence=("test_supersession.py: supersession lifecycle and current-memory "
                  "selection passed; older memory was marked superseded and excluded "
                  "from current active memories.")
    ),

    record_result(
        case_id="case3",
        d3_result=D3_RESULTS["case3"],
        d4_result=("Selected memory context stays within the token budget "
                   "while preserving the current relevant preference"),
        status="PASS",
        evidence=("test_context_selection.py: memory-token budget, response-token "
                  "reservation, and preservation of the current preference all passed.")
    ),

    record_result(
        case_id="case4",
        d3_result=D3_RESULTS["case4"],
        d4_result=("Retrieval is restricted to the requesting user's "
                   "authorized memory IDs"),
        status="PASS",
        evidence=("test_scoped_faiss.py: FAISS IDSelector restricted retrieval "
                  "to authorized IDs and the isolation assertion passed."),
    ),

    record_result(
        case_id="case5",
        d3_result=D3_RESULTS["case5"],
        d4_result=("Sensitive information is rejected before durable storage, "
                   "and forgotten memories are excluded from retrieval"),
        status="PASS",
        evidence=("test_sensitive_admission.py: sensitive information was rejected "
                  "and not stored. test_forgetting.py: forgotten memories remained "
                  "excluded from retrieval, including when FAISS cleanup failed."),
    ),

    record_result(
        case_id="case6",
        d3_result=D3_RESULTS["case6"],
        d4_result=("No-memory behavior was validated when no candidate "
                   "reaches the relevance threshold"),
        status="PARTIAL",
        evidence=("test_no_memory.py: the cold-start query returned no relevant "
                  "memory when all candidate scores were below the threshold. "
                  "This is a controlled decision-rule test, not an integrated "
                  "end-to-end D4 retrieval evaluation.")
    )]


for result in results:
    print(
        f"{result['case_id']} | "
        f"D3={result['d3_result']} | "
        f"D4={result['d4_result']} | "
        f"status={result['status']}")