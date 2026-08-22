# D4 Phase 10 — Scale / Capacity Decision

## Decision

The current shared FAISS design is acceptable for the scale demonstrated in this experiment, but we will not treat it as an unlimited production-scale solution.

For the current D4 scope, we will keep:

- 768-dimensional float32 embeddings
- shared FAISS index
- `IndexFlatIP`
- `IndexIDMap2`
- user-scoped retrieval using `FAISS IDSelector`

No vector-database migration is being introduced as part of this decision.

## Evidence

The design successfully handled:

- 100K memories
- 1M memories

with user isolation preserved in every tested retrieval.

At 1M memories:

- 0.1% authorized set → p95 9.062 ms
- 1% authorized set → p95 11.619 ms
- 10% authorized set → p95 56.708 ms

The 5M-memory index could not be built in the development environment and failed with `std::bad_alloc`.

## Engineering boundary

The experiment therefore gives us an observed boundary, not a universal capacity limit.

The current implementation should not be presented as proven for multi-million memory deployments.

If the system eventually needs substantially larger memory collections, the index/storage strategy will need to be revisited and benchmarked rather than assumed to scale indefinitely.

## Why we are not changing the architecture now

The current D4 work is focused on validating the memory-system design and its core invariants.

The scale experiment has identified the limitation of the current approach without establishing that a different storage system is required for the current project scope.

Therefore, changing the vector-storage architecture now would introduce a new design change without evidence that it is necessary for the target scope.

## Limitation

These results came from the development environment using synthetic vectors and a single-query-at-a-time workload.

They are engineering evidence for this project, not production capacity guarantees.