# M4 Supersession and Conflict Bindings

- **Status:** Accepted
- **Date:** 2026-09-06
- **Scope:** M4 supersession and conflict mutation only

## Context

M4 must integrate explicit supersession without inferring a replacement from
semantic similarity, subject overlap, value differences, or recency alone. The
existing model and SQLite schema already represent `supersedes` and
`superseded_by`, while M2 excludes records unless they are both active and
indexed and have no `superseded_by` relationship. This decision binds the
missing write-side rules before BUILD.

## Request contract

An admission request may identify at most one replacement target through nullable
`supersedes_memory_id`.

- An absent target is an ordinary admission and creates no supersession link.
- M4 never infers a target from content, subject, value, similarity, or time.
- A supplied target is accepted only when provenance authority is exactly the
  existing `EvidenceAuthority.EXPLICIT_USER` enum member, whose persisted value
  is `explicit_user`.
- `EvidenceAuthority.INFERRED`, whose persisted value is `inferred`, must never
  create a supersession link.
- Ambiguous input must not create a link; M4 adds no natural-language ambiguity
  classifier or inferred relationship extraction.
- The target ID is included in canonical request fingerprinting, so changing it
  under an existing owner-scoped idempotency key is a conflict before mutation.

## Target validation

Before embedding or durable mutation, the application resolves the target through
the trusted requesting user and requires:

1. the target exists and belongs to that user;
2. its indexing state is exactly `indexed`;
3. its lifecycle status is exactly `active`;
4. `superseded_by` is absent;
5. replacement and target have exactly equal normalized subjects;
6. replacement and target have exactly equal normalized memory types;
7. replacement and target are distinct; and
8. the proposed relationship does not create a supersession cycle.

Cross-owner, missing, mismatched, self-referential, cyclic, non-indexed, inactive,
or already-superseded targets are rejected without embedding, persistence, FAISS
publication, or relationship mutation.

Subject equality uses the existing admission normalization. A targeted
supersession therefore requires both normalized subjects to be present and equal.
Memory-type equality uses the normalized controlled `MemoryType` value.

## Write ordering and atomic current-state transition

The approved process write lock covers target validation, idempotency resolution,
replacement persistence and indexing, and relationship acknowledgement.

The replacement is first persisted with `indexing_state=pending`. It may have the
existing logical lifecycle value `active`, but it is not current-state eligible or
retrievable because M2 requires both `lifecycle_status=active` and
`indexing_state=indexed`.

FAISS publication occurs while the replacement remains pending. The ordinary M1
`mark_indexed` acknowledgement must not run separately for a targeted
supersession.

After FAISS publication, SQLite performs one atomic transaction that:

- revalidates both records under the trusted owner;
- requires the replacement to remain active, pending, and unlinked;
- requires the target still to be active, indexed, and unsuperseded;
- changes the replacement from pending to indexed, thereby activating it for
  current-state eligibility;
- writes the target ID as the replacement's sole `supersedes` relationship;
- changes the target lifecycle to `superseded`; and
- writes the replacement ID to the target's `superseded_by` relationship.

The transaction commits the replacement's indexed/current eligibility and both
relationship directions together, or commits none of them. FAISS retains the
older vector; SQLite current-state eligibility excludes it only after the atomic
transaction succeeds.

## Failure and retry behavior

M4 does not define a new FAISS retry, publication-detection, or recovery policy.
It reuses the existing M1 `pending` / `indexed` / `failed` state machine, stored
embedding and stable vector ID, copy-on-write duplicate prevention, and
fail-closed treatment of publication uncertainty.

If replacement persistence fails, no replacement or FAISS mutation exists. If
FAISS publication fails or its durability is uncertain:

- no supersession relationship transaction is attempted;
- the replacement remains `pending` or `failed` under the existing M1 contract;
- the replacement is non-retrievable;
- the target remains active, indexed, unlinked, and the only current memory; and
- retry and recovery follow the existing M1 contract without a new M4 shortcut.

An M1 retry may reuse the stored embedding and stable vector ID and safely execute
the existing copy-on-write FAISS protocol. Arbitrary index uncertainty,
reconciliation, orphan handling, or generation recovery remains M8 work.

Only after durable FAISS publication has been verified may M4 attempt the atomic
SQLite current-state/relationship transaction. If that transaction fails:

- both relationship directions remain absent;
- the replacement remains pending and non-retrievable;
- the target remains active, indexed, unlinked, and the only current memory; and
- the result reports `supersession_acknowledgement_failed`,
  `indexing_state=pending`, `retrievable=false`, and a retryable error.

An identical idempotent retry may skip embedding and vector publication and retry
only the atomic SQLite transaction when durable publication of that exact stable
vector ID is known. In that case it:

- reuses the existing replacement, embedding, vector ID, and existing FAISS
  publication;
- does not create another memory, embedding, or vector;
- retries only the atomic SQLite current-state/relationship transaction;
- treats an already-complete exact transaction as the same success; and
- never duplicates, widens, or rewrites an existing relationship.

If durable publication is not known, the retry remains governed by M1 and must not
assume publication success merely because a vector may have been written. It
performs no relationship mutation until the M1 publication protocol again
establishes durable success.

The transaction revalidates target and replacement state so competing replacement
attempts cannot both supersede the same current record.

## Conflict and reversion rules

- An inferred memory never supersedes explicit-user evidence.
- Ambiguous statements remain unlinked.
- A later return to an older value creates a new pending replacement, which becomes
  current only through the same successful atomic transaction.
- A previously superseded record is never reactivated.
- Relationship mutation does not establish truth beyond the explicit targeted
  correction supplied through the trusted workflow.

## Milestone boundaries

This decision does not add historical retrieval, automatic expiration, forgetting,
physical vector deletion, recovery, reconciliation, observability rollout, learned
conflict resolution, or fixed-workload completion. Those remain assigned to later
milestones.

No schema migration is required by this binding because the existing schema
already persists indexing state and both relationship directions. A migration may
be added only if BUILD evidence demonstrates a necessary schema constraint within
M4's locked boundary.

## Verification requirements

Tests must prove:

- exact target validation and zero mutation on every rejection;
- exact `EvidenceAuthority.EXPLICIT_USER` enforcement;
- pending replacement exclusion before acknowledgement;
- atomic activation/indexing and bidirectional persistence;
- indexing-before-relationship ordering;
- rollback and lock release after acknowledgement failure;
- old-target continuity after every pre-acknowledgement failure;
- idempotent retry without duplicate embedding, memory, vector, or relationship;
- competing replacement safety;
- current-state exclusion of the target only after atomic success;
- restart persistence;
- reversion without reactivation; and
- preservation of owner isolation, M2 eligibility, M3 relevance, and M1 ranking.
