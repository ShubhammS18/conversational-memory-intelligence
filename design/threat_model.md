# D4 — Threat Model

## Purpose

This document defines the main security and privacy threats identified for
the conversational memory system and the controls established during D4.

The focus is on memory-specific risks rather than general application
security.

---

## 1. Trust Boundaries

The main trust boundaries are:

```text
User / application
        |
        v
Memory system
        |
        +--> Memory admission
        |
        +--> Metadata / lifecycle state
        |
        +--> Vector index
        |
        +--> Context construction
        |
        v
Downstream model
```

The most important boundary is between a requesting user and stored memories
belonging to other users.

---

## 2. Threat: Cross-User Memory Retrieval

### Risk

A query from one user could retrieve a semantically similar memory belonging
to another user.

### Control

`user_id` is treated as a hard authorization boundary.

The system first resolves the memory IDs authorized for the requesting user
and restricts FAISS retrieval to those IDs.

### Acceptance

Zero unauthorized user memories may enter the requesting user's retrieval
results.

### D4 evidence

Cross-user retrieval testing passed, including adversarial retrieval tests.

---

## 3. Threat: Unauthorized Deletion

### Risk

A user could attempt to forget another user's memory.

### Control

Deletion requires authorization against the memory's `user_id`.

An unauthorized deletion request must not modify the target memory.

### Acceptance

Unauthorized deletion is rejected and the other user's memory remains
unchanged.

### D4 evidence

Unauthorized deletion testing passed.

---

## 4. Threat: Forgotten Memory Remaining Retrievable

### Risk

A memory that the user explicitly forgot could remain available through the
vector index.

### Control

Forgetting makes the memory ineligible for retrieval and removes it from
the retrieval set.

Physical FAISS deletion is also attempted, but retrieval correctness does
not depend solely on successful physical deletion.

### Acceptance

A forgotten memory must not be retrievable.

### D4 evidence

Deletion and FAISS deletion-failure tests passed.

---

## 5. Threat: Sensitive Information Becoming Durable Memory

### Risk

Sensitive information could be extracted from conversation and retained as
durable memory.

### Control

Sensitive information is rejected during the admission process before
durable storage.

Rejected information must not enter the memory store or retrieval index.

### Acceptance

Rejected sensitive information cannot become retrievable durable memory.

### D4 evidence

Sensitive-information admission and retrieval tests passed.

---

## 6. Threat: Incorrect Current State from Conflicting Memories

### Risk

A superseded memory could be selected as the user's current preference or
state.

### Control

Conflict resolution uses explicit supersession together with recency and
provenance authority.

Superseded memories are excluded from current-state retrieval.

Historical retrieval can still recover superseded memories when appropriate.

### Acceptance

Current-state retrieval must not use a superseded memory as the current
state.

### D4 evidence

The contradictory-memory and changing-state evaluation cases passed.

---

## 7. Threat: Historical Memory Polluting Current Context

### Risk

Too many historical memories could enter the model context and reduce the
relevance of the current memory or exceed the available token budget.

### Control

Context construction applies a hard token budget and selects the relevant
memory context rather than passing all retrieved history to the model.

### Acceptance

Selected memory context remains within the hard token budget while
preserving the relevant current memory.

### D4 evidence

The historical/context-pollution evaluation case passed.

---

## 8. Threat: Unrelated Memory Returned for Cold Start

### Risk

When no stored memory is sufficiently relevant, semantic retrieval could
still return an unrelated memory and make it appear relevant.

### Control

A relevance threshold allows the system to return no memory.

### Acceptance

When no stored memory is sufficiently relevant, the system must be able to
return an empty memory result.

### D4 evidence

The no-memory case validated this behavior, but was recorded as PARTIAL
because the broader evaluation does not establish that this behavior is
sufficient under all possible workloads.

---

## 9. Threat: Memory Reactivation After Forgetting

### Risk

Forgetting a newer memory could incorrectly cause an older superseded memory
to become current.

### Control

Forgetting is separate from supersession.

A forgotten memory is excluded from retrieval, and superseded memories are
not automatically reactivated.

### Acceptance

Forgetting a newer memory must not reactivate an older superseded memory.

### D4 evidence

The deletion lifecycle test passed.

---

## 10. Threat: Inferred Information Treated as Stronger Than Explicit Evidence

### Risk

An inferred memory could incorrectly override an explicit user statement.

### Control

Memory provenance distinguishes explicit from inferred information.

Explicit user evidence has higher authority during conflict resolution.

### Acceptance

Conflict resolution must consider provenance authority rather than relying
only on semantic similarity or recency.

---

## 11. Threat: Retrieval Leakage Through Semantic Similarity

### Risk

Semantically similar memories belonging to unauthorized users could appear
to be valid candidates if authorization is applied only after retrieval.

### Control

Authorization is applied at the candidate retrieval boundary using
authorized memory IDs and FAISS filtering.

Semantic similarity cannot override authorization.

### Acceptance

Unauthorized memories never enter the requesting user's retrieval
candidate set.

---

## 12. Security Invariants

The following invariants are mandatory:

1. A user can retrieve only authorized memories.
2. A user cannot delete another user's memory.
3. Forgotten memories cannot be retrieved.
4. Rejected sensitive information cannot become durable memory.
5. Superseded memories cannot silently become current.
6. Historical retrieval cannot reactivate historical state.
7. Semantic similarity cannot override authorization.
8. The memory context must respect the hard token budget.

---

## 13. Remaining Security Boundaries

D4 establishes the memory-level security and privacy controls above.

The following remain outside the current D4 implementation scope:

- production authentication infrastructure;
- transport-level security;
- API gateway security;
- secrets management;
- infrastructure hardening;
- production audit-log infrastructure.

These must be addressed if the system moves toward production deployment.
