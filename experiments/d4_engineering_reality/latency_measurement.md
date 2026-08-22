# D4 Phase 10 — Latency Measurement

## What I am measuring

The current D4 implementation does not provide a complete integrated request pipeline, so I cannot honestly report an end-to-end memory-system latency.

I therefore separate the latency question into measurable and currently unmeasurable parts.

## Measured

### FAISS retrieval

The scale experiment measured 100 scoped FAISS searches after 10 warm-up queries.

At 1M memories:

| Authorized fraction | Authorized IDs | p50 | p95 |
|---:|---:|---:|---:|
| 0.1% | 1,000 | 7.038 ms | 9.062 ms |
| 1% | 10,000 | 10.197 ms | 11.619 ms |
| 10% | 100,000 | 45.527 ms | 56.708 ms |

These measurements include the FAISS search with the user-scoped `IDSelector`.

## Not yet measured

The following stages do not currently have an integrated benchmark:

- query embedding;
- memory extraction;
- admission processing;
- authorization lookup outside the synthetic experiment;
- lifecycle/conflict processing as part of a complete request;
- context construction as part of a complete request;
- final model generation.

Therefore I am not assigning a total end-to-end latency number.

## Current conclusion

The FAISS retrieval component is measurable and shows that latency increases with both total index size and the size of the authorized candidate set.

The current evidence is not sufficient to define an end-to-end latency SLO.

For the current D4 scope, I will treat the measured FAISS numbers as component benchmarks rather than production latency guarantees.

## Limitation

These measurements were collected on the development machine using synthetic 768-dimensional float32 vectors and a single-query-at-a-time workload.

They do not represent production hardware, concurrent traffic, network latency, embedding-service latency, or LLM generation latency.