# D4 -- Sprint Plan

## Goal

The goal of the next sprint is to turn the D4 design into a small integrated memory pipeline without changing the core architecture unless the implementation gives me a clear reason to.

The main focus is to connect the pieces I already tested separately:

- memory admission;
- storage;
- user-scoped retrieval;
- lifecycle handling;
- conflict resolution;
- no-memory behavior;
- context construction;
- forgetting.

---

## 1. Build the integrated memory pipeline

### Work

- Create one end-to-end flow from conversation input to memory admission.
- Store admitted durable memories using the D4 memory schema.
- Generate and store embeddings with the memory ID mapping.
- Keep metadata and FAISS IDs consistent.

### Done when

A memory can move from input -> admission -> storage -> retrieval without using isolated test-only data structures.

---

## 2. Integrate current-state retrieval

### Work

- Resolve memory IDs owned by the requesting user.
- Apply FAISS scoped retrieval using authorized IDs.
- Exclude forgotten memories.
- Exclude superseded memories for current-state queries.
- Apply the existing conflict-resolution rules.

### Done when

A current query returns only authorized, relevant, current memories.

---

## 3. Integrate historical retrieval

### Work

- Support historical query intent.
- Allow superseded memories when the query asks about past state.
- Keep forgotten memories excluded.
- Make sure historical retrieval never reactivates an old memory.

### Done when

The FAISS -> Qdrant history case works through the integrated pipeline instead of only through the isolated lifecycle test.

---

## 4. Integrate no-memory behavior

### Work

- Apply the relevance threshold inside the real retrieval flow.
- Return an empty memory result when no candidate is good enough.
- Re-test the D3 cold-start failure case.

### Done when

Case 6 from the D4 evaluation can be upgraded from PARTIAL only if the integrated behavior actually passes.

---

## 5. Integrate context construction

### Work

- Pass ranked memories into the token-budgeting layer.
- Reserve response tokens.
- Keep the selected memory context inside the hard budget.
- Allow an empty context when no memory should be used.

### Done when

The integrated path produces a bounded memory context instead of returning an unrestricted list of retrieved memories.

---

## 6. Integrate forgetting

### Work

- Authorize deletion by `user_id`.
- Make the memory unavailable to retrieval immediately.
- Remove its vector from FAISS using its memory ID.
- Keep the operation idempotent.
- Preserve the existing rule that deleting a newer memory does not reactivate an older superseded memory.

### Done when

The forgetting guarantees tested in D4 still hold in the integrated pipeline.

---

## 7. Add basic observability

### Work

Add lightweight logging/metrics for:

- retrieval latency;
- number of authorized candidates;
- number of returned memories;
- no-memory results;
- admission outcomes;
- forgetting success/failure;
- FAISS cleanup failures.

Do not log raw sensitive memory content unless it is genuinely required for debugging.

### Done when

The integrated pipeline gives enough information to understand why a memory was or was not used.

---

## 8. Re-run the fixed D3 evaluation cases

Use the same six cases from D3:

1. contradictory memories;
2. old + new decisions;
3. long historical context;
4. cross-user retrieval;
5. sensitive memory retention;
6. cold-start / unrelated retrieval.

Do not replace these cases with easier examples.

### Done when

Each case has an integrated result with PASS / PARTIAL / FAIL and a short explanation.

---

## 9. Re-check engineering limits

After integration:

- measure end-to-end retrieval latency;
- separate vector-search latency from the rest of the pipeline;
- record memory/index growth;
- document any new failure boundary;
- do not claim production-scale reliability from local tests.

---

## Sprint exit criteria

The sprint is complete when:

1. the major D4 components work together in one pipeline;
2. user isolation still holds;
3. forgetting still holds;
4. current and historical retrieval behave differently where required;
5. no-memory behavior is tested inside the real flow;
6. context stays within the hard token budget;
7. the six D3 failure cases are re-run;
8. any remaining gaps are documented instead of hidden.

---

## Things I am not planning to add unless the evidence requires them

- a new vector database;
- distributed retrieval;
- automatic memory consolidation/reflection;
- generic age-based decay;
- a complex weighted ranking formula;
- production infrastructure that is not needed for the current project scope.

The point of the next sprint is integration and validation, not adding architecture for its own sake.
