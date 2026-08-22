# ADR-003: User-Scoped Retrieval

- **Status:** Proposed
- **Date:** 2026-08-13

## Context

The baseline stored memories from different users in one shared collection and did not use `user_id` during retrieval. In Case 4, a query for one user retrieved another user's memory.

This is not just a ranking problem. If an unauthorized memory is allowed into the candidate retrieval set, semantic similarity can cause it to be selected even when it does not belong to the requesting user.

D4 therefore needs retrieval to enforce user scope as part of the retrieval contract.

I tested a shared FAISS index with explicit memory IDs and a search-time ID selector. The first controlled test restricted a query for user A to A's memory IDs. A second adversarial test deliberately made user B's memories more similar to the query than user A's memories. In both tests, only user A's memories were returned.

## Decision drivers

- Prevent cross-user or cross-tenant memories from entering retrieval results.
- Make identity isolation an architectural constraint rather than a post-processing step.
- Preserve FAISS as the vector-search substrate selected for this project.
- Avoid creating a separate FAISS index for every user unless later scale evidence shows that this is necessary.
- Keep memory metadata and vector indexing as separate concerns so lifecycle and deletion operations can be handled explicitly.
- Make the isolation rule simple enough to test with adversarial cases.

## Options considered

### Option A — Shared FAISS index with global similarity search, then filter by `user_id`

The system would search all memories and remove unauthorized results afterward.

**Advantages**

- Simple implementation.
- One shared index.
- Minimal changes to the D3 retrieval approach.

**Problems**

- Unauthorized memories participate in candidate generation.
- A highly similar memory belonging to another user can occupy a top-k result before filtering.
- It weakens the architectural isolation boundary and can leave too few useful authorized results.

**Rejected.**

### Option B — Separate FAISS index for every user

Each user would have an independent vector index.

**Advantages**

- Very clear isolation boundary.
- A query physically searches only the user's index.
- Simple retrieval semantics.

**Problems**

- Index count grows with the number of users.
- Index creation, loading, updating, deletion, and operational management become more complicated.
- The approach may be unnecessarily expensive for a system with many users.

**Not selected as the default.** It remains a possible future design if scale or operational evidence justifies it.

### Option C — Shared FAISS index with authorized memory-ID filtering

Keep a shared FAISS index, maintain the mapping between memory IDs and user ownership separately, and restrict each search to the memory IDs authorized for the requesting user.

**Advantages**

- Preserves one shared vector index.
- Makes `user_id` an explicit retrieval constraint.
- Prevents unauthorized vectors from becoming search results even when they are more semantically similar.
- Keeps ownership metadata separate from the embedding representation.
- Avoids one-index-per-user management overhead.

**Problems**

- Requires an authorization/scope lookup before search.
- Requires the chosen FAISS index/search configuration to support the required ID-selection mechanism.
- Large authorized ID sets may introduce performance considerations that need to be measured.

**Selected.**

## Decision

Retrieval will require an explicit user/tenant scope.

The retrieval flow will be:

```text
query + user_id
        ↓
resolve authorized memory IDs
        ↓
restrict FAISS search to those IDs
        ↓
generate semantic candidates
        ↓
apply validity / conflict resolution
        ↓
rank candidates
```

The system will not use global FAISS retrieval followed by an authorization filter as its primary isolation mechanism.

For the current design, a shared FAISS index with ID-based search restriction is the selected mechanism.

`user_id` therefore forms part of the retrieval contract. A retrieval operation without a valid authorization scope must not perform an unrestricted memory search.

## Consequences and trade-offs

### Benefits

- Cross-user isolation is enforced before candidate selection.
- Semantic similarity cannot override authorization scope.
- The design preserves the project's FAISS preference without requiring an index per user.
- The vector index can remain focused on search while metadata handles ownership and other memory attributes.
- The same boundary can support future tenant-level isolation if the project expands beyond individual users.

### Costs and risks

- The system needs a reliable mapping from `memory_id` to `user_id`.
- Authorization/scope resolution becomes part of the retrieval path.
- The performance of ID-restricted search needs to be evaluated as the number of memories and users grows.
- FAISS index types and search configurations may differ in their filtering capabilities.
- Correctness depends on the authorization mapping being accurate and available.

## Validation plan

The isolation invariant is:

> **A retrieval request must never return a memory whose `user_id` is outside the authorized retrieval scope.**

Validation will include:

1. A normal scoped retrieval where user A has relevant memories and user B has unrelated memories.
2. An adversarial retrieval where user B's memories are deliberately more similar to the query than user A's memories.
3. Multiple-user cases to verify that the restriction is consistently applied.
4. Empty or invalid authorization scopes to verify that unrestricted retrieval is not performed.

The prototype already passed the first two tests:

- User A's authorized IDs were `[101, 102, 105]`.
- In the adversarial test, User B had similarity scores of `0.99` and `0.95`, higher than User A's `0.80`, `0.70`, and `0.60`.
- Only User A's memories were returned in both tests.

Further scale and lifecycle testing will be performed when the complete D4 retrieval/storage design is validated.

## Revisit conditions

I will revisit this decision if:

- the ID-restricted FAISS approach cannot meet the required latency at the intended scale;
- the selected FAISS index configuration cannot provide the required isolation guarantee;
- the authorization-ID lookup becomes an unacceptable retrieval bottleneck;
- deletion or lifecycle operations become too difficult to maintain consistently;
- controlled tests show that a partitioned or per-user index provides materially better isolation, reliability, or operational performance for the intended workload.

A performance problem alone should not justify moving isolation to post-retrieval filtering, because the zero-cross-user-retrieval invariant remains a hard correctness requirement.
