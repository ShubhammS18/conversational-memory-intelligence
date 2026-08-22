# ADR-007 — Privacy, Isolation, and Forgetting

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

In the earlier D4 work, I already handled the basic cross-user retrieval problem by making `user_id` a hard constraint before FAISS search. The shared FAISS index is searched only over IDs that belong to the requesting user.

I also already decided that sensitive information should be handled at the admission stage rather than by adding sensitivity fields to the core memory representation.

For lifecycle, ADR-006 established that forgetting is different from supersession and expiration. A superseded memory can still be useful for historical queries, but if the user explicitly asks to forget something, that memory should no longer be available through retrieval.

So for this Phase, I am not redesigning the isolation or lifecycle mechanisms that are already in place. I am adding the missing privacy behavior around forgetting, sensitive-memory admission, auditability, and adversarial retrieval.

## Decision drivers

- keep the existing hard `user_id` isolation;
- make forgetting different from supersession and expiration;
- prevent a forgotten memory from being retrieved, including through historical queries;
- prevent one user from deleting another user's memory;
- remain safe if FAISS deletion fails;
- make repeated deletion safe;
- avoid introducing a distributed transaction system for the current project;
- keep sensitive-memory handling at admission;
- have enough audit information to understand what happened;
- avoid adding a generic anti-poisoning system without an actual demonstrated failure.

## Options considered

### Option 1 — Treat forgetting as supersession or expiration

I rejected this because those states have different meanings.

Supersession means a newer memory replaced an older one, while expiration means a memory is no longer valid after a defined period. Neither represents an explicit user request to remove the memory.

### Option 2 — Only mark the memory as deleted

I rejected this as the complete deletion mechanism because the vector could still physically remain in FAISS.

Logical exclusion is still necessary, but I also want to remove the corresponding FAISS entry.

### Option 3 — Rebuild the complete FAISS index after every deletion

I rejected this because we already use `memory_id` with `IndexIDMap2`, so targeted deletion is available. Rebuilding the whole index for every forget operation would add unnecessary work and complexity.

### Option 4 — Logical exclusion first, then targeted FAISS deletion

I chose this. The memory is first made unavailable to retrieval and then its `memory_id` is removed from FAISS. This gives me a safer failure mode if the physical FAISS deletion fails.

## Decision

### 1. Keep the existing user isolation

I will continue using the Phase 4 mechanism:

```text
requesting user
      ↓
resolve authorized / eligible memory IDs
      ↓
FAISS IDSelector
      ↓
search
```

Phase 8 does not replace this.

A deleted memory is simply removed from the set of eligible IDs in addition to the existing user and lifecycle checks.

### 2. Forgetting is a separate operation

I will not represent forgetting as `superseded` or `expired`.

I also will not introduce a new lifecycle state such as `DELETING`.

Conceptually, the operation is:

```text
forget(memory_id, requesting_user_id)
```

The exact persistent schema for recording deletion is not fixed yet because the current D4 work has not established a production-like persistent storage layer.

### 3. Authorization

Before deleting a memory, I will check:

```text
memory.user_id == requesting_user_id
```

If this does not match, the deletion is rejected and the memory remains unchanged.

### 4. Deletion ordering

For an authorized deletion, I will use:

```text
authorize
   ↓
make memory unavailable
   ↓
exclude it from retrieval
   ↓
remove memory_id from FAISS
   ↓
complete deletion
```

This ordering is intentional.

If FAISS deletion fails, the memory must still remain unavailable through normal retrieval. The physical cleanup can then be retried.

### 5. Targeted FAISS deletion

I will remove only the affected `memory_id` from the FAISS index rather than rebuilding the entire index.

This is tied to the current `IndexIDMap2` design and should be revisited if the index architecture changes later.

### 6. Idempotency

Deleting a memory that has already been forgotten should be safe.

A repeated request should not corrupt state or cause unrelated memories to be affected.

### 7. Interaction with supersession

Forgetting a newer memory must not reactivate an older superseded memory.

For example:

```text
M1: FAISS
M2: Qdrant supersedes M1
forget(M2)

M1 = superseded
M2 = forgotten
```

The no-reactivation rule from ADR-006 remains unchanged.

A forgotten memory must also be excluded from historical retrieval.

### 8. Sensitive-memory admission

I will reject information that the admission policy identifies as sensitive and prohibited from durable memory.

I will keep this at the admission layer rather than changing the core memory representation.

The Phase 8 policy test also confirmed that temporary and uncertain information can be kept separate from normal durable memory.

This is currently a policy-level validation, not an integrated extraction/admission pipeline.

### 9. Auditability

For privacy-sensitive operations, I want enough information to answer:

- which `memory_id` was affected;
- which `user_id` performed the operation;
- what operation happened;
- what the result was;
- when it happened.

The initial operation types are:

```text
ADMIT
REJECT
FORGET
```

I am not introducing a full enterprise audit system at this stage.

### 10. Adversarial retrieval

The existing user-scoped retrieval mechanism is also the main protection against an adversarial memory from another user being semantically closer to the query.

I tested this by making another user's memory more similar to the query. The unauthorized memory was still excluded because it was not in the authorized ID set.

I am not adding a separate generic poisoning-defense subsystem because we do not currently have evidence of a specific remaining poisoning failure that requires one.

## Consequences and trade-offs

### What this gives me

- `user_id` remains a hard retrieval boundary.
- Forgetting has a different meaning from normal lifecycle transitions.
- A forgotten memory becomes unavailable before FAISS cleanup finishes.
- FAISS cleanup is targeted instead of rebuilding the whole index.
- Deletion can safely be retried.
- Historical retrieval cannot bring back a forgotten memory.
- Sensitive-memory handling stays at admission.
- Privacy operations have a minimal audit model.
- I am not adding complexity for an unproven poisoning problem.

### What this does not solve yet

- A failed FAISS deletion can temporarily leave the physical vector in the index, although it must remain excluded from retrieval.
- I do not yet have a production-grade transaction spanning authoritative memory state and FAISS.
- The exact persistent representation of deletion is not integrated into a production-like storage layer yet.
- Sensitive admission has only been tested at the policy level.
- Audit logging is designed but not implemented as a persistent audit system.
- A future vector-index architecture may require a different physical deletion mechanism.

## Validation performed

### Authorized deletion — PASS

The owner successfully deleted a memory and a later retrieval did not return it.

### Unauthorized deletion — PASS

A user could not delete another user's memory.

### Idempotent deletion — PASS

Deleting an already-deleted memory was safe.

### Other-user isolation — PASS

Deleting one user's memory did not affect another user's memory.

### FAISS deletion failure — PASS

When FAISS deletion was deliberately made to fail, the memory was still excluded from the retrieval candidate set.

### No reactivation after forgetting — PASS

Forgetting a newer superseding memory did not reactivate the older superseded memory.

### Sensitive admission policy — PASS

Sensitive information was rejected, rejected information was not stored, temporary and uncertain information were classified separately, and normal durable information was admitted.

### Adversarial cross-user retrieval — PASS

A deliberately more similar memory belonging to another user was still excluded by the user-scoped retrieval constraint.

These are focused component-level validations. I am not treating them as a full end-to-end D4 evaluation.

## What I will validate later

The broader evaluation still needs to show that these rules work together with the complete D4 memory flow.

In particular, I need to verify that:

- user isolation survives the complete retrieval pipeline;
- forgotten memories cannot be retrieved through current or historical queries;
- a failed FAISS deletion cannot expose a forgotten memory;
- sensitive admission decisions are actually enforced before durable storage;
- privacy operations can be traced using the selected audit information;
- deletion does not conflict with lifecycle and conflict-resolution behavior.

I am keeping this broader validation separate from these Phase 8 component tests.

## When I would revisit this decision

I will revisit this ADR if testing shows that:

- the existing user-scoped retrieval is not sufficient to prevent leakage;
- logical exclusion is not reliable during FAISS cleanup failures;
- targeted FAISS deletion stops fitting the index architecture;
- deletion cannot be kept consistent with the authoritative memory state;
- sensitive admission produces unacceptable behavior;
- the audit requirements become more extensive;
- a concrete poisoning failure appears that the existing controls cannot handle;
- the privacy mechanisms introduce unacceptable complexity or performance costs.
