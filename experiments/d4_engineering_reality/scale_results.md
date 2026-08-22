# D4 Phase 10 — Scale Experiment Results

## Purpose

Test the practical scale of the existing shared FAISS design using 768-dimensional float32 vectors and user-scoped retrieval with FAISS `IDSelector`.

This is an engineering reality test, not a production capacity claim.

## Results

| Total memories | Raw vector storage | Authorized fraction | Authorized IDs | Search p95 | Result |
|---:|---:|---:|---:|---:|---|
| 100,000 | 0.286 GB | 0.1% | 100 | 2.528 ms | PASS |
| 100,000 | 0.286 GB | 1% | 1,000 | 3.135 ms | PASS |
| 100,000 | 0.286 GB | 10% | 10,000 | 8.727 ms | PASS |
| 1,000,000 | 2.861 GB | 0.1% | 1,000 | 9.062 ms | PASS |
| 1,000,000 | 2.861 GB | 1% | 10,000 | 11.619 ms | PASS |
| 1,000,000 | 2.861 GB | 10% | 100,000 | 56.708 ms | PASS |
| 5,000,000 | 14.305 GB | — | — | — | BUILD FAILED |

## 5M failure

The 5M-vector experiment failed while adding vectors to the FAISS `IndexIDMap2` index:

`MemoryError: std::bad_alloc`

The failure occurred during `index.add_with_ids()`.

Therefore, no 5M retrieval latency result was collected.

## What this establishes

1. The current shared FAISS + `IDSelector` design preserved user isolation at all tested scales where the index could be built.

2. Retrieval latency increased as the total index size and authorized ID set increased.

3. At 1M memories, p95 retrieval latency ranged from 9.062 ms with 0.1% authorized memories to 56.708 ms with 10% authorized memories.

4. The current implementation could not build a 5M-memory index in the available development environment.

5. The 14.305 GB figure for 5M memories represents raw vector storage only.
   It is not the total RAM requirement.

## Limitation

These measurements were taken on the project's development machine using synthetic vectors and a single-query-at-a-time workload. They should not be treated as production latency or capacity guarantees.