# D4 Phase 10 — Cost Analysis

## What I can establish

The current D4 implementation is local and uses FAISS for vector retrieval. The scale experiments therefore give me storage and compute measurements, but they do not give me a production monetary cost.

I do not have enough evidence to claim a cost-per-query or monthly production cost.

## Main cost drivers

For this design, the main resource drivers are:

- embedding generation;
- vector storage in memory;
- FAISS search CPU/memory usage;
- memory metadata/content storage;
- LLM calls when retrieved memory is passed into the model.

The relative cost of these components will depend on the deployment environment and workload.

## Evidence from the scale experiment

At 1M memories, the raw vector storage requirement was approximately 2.861 GB for 768-dimensional float32 vectors.

At 5M memories, the raw vector requirement was approximately 14.305 GB, and the current FAISS index failed to build in the development environment with `std::bad_alloc`.

These numbers describe resource requirements, not monetary cost.

## What I am not claiming

I am not assigning:

- a cost per query;
- a monthly infrastructure cost;
- a maximum number of users;
- a production memory cost;
- an LLM cost per request.

Those values would require deployment-specific assumptions or measurements that we do not currently have.

## Current conclusion

Cost remains a deployment-dependent engineering concern.

For the current D4 scope, the useful evidence is the resource growth observed during the scale experiment rather than a fabricated monetary estimate.

If this system is later deployed as a production service, the cost model should be derived from the actual embedding model, model provider or hosting setup, hardware, request volume, memory volume, and concurrency.