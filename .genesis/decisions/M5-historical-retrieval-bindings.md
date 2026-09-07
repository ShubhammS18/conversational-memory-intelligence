# M5 Historical Retrieval Bindings

- **Status:** Accepted
- **Date:** 2026-09-07
- **Scope:** M5 explicit historical retrieval only

## Context

M5 must expose historical retrieval without weakening current-state retrieval,
owner isolation, deletion exclusion, relevance filtering, context budgeting, or
M4 relationship integrity. ADR-006 establishes that lifecycle eligibility depends
on query intent and that the D4 evidence does not validate automatic intent
classification. The D4 characterization permits active and superseded records for
an explicitly historical query.

The existing repository operations cannot implement the complete rule:
`current_state_vector_ids` excludes superseded records, while
`eligible_vector_ids` and `hydrate_indexed` do not exclude tombstoned records.

## Decision

### Explicit typed intent

Add a strict `RetrievalIntent` enumeration with exactly:

- `current`
- `historical`

`RetrievalRequest.intent` has the schema default `current` so existing ordinary
retrieval retains its established meaning. Historical access occurs only when the
caller explicitly supplies `RetrievalIntent.HISTORICAL`. Query text, embeddings,
similarity, or inferred meaning must not select historical mode.

The current schema default is part of the typed API contract. It is not an
environment fallback, configuration fallback, or intent classifier.

### Historical eligibility

A memory is eligible for M5 historical retrieval only when:

1. it belongs to the trusted requesting user;
2. its indexing state is exactly `indexed`;
3. `deleted_at` is `NULL`; and
4. either:
   - its lifecycle status is `active` and `superseded_by` is `NULL`; or
   - its lifecycle status is `superseded` and `superseded_by` is non-empty.

Pending and failed records remain excluded. Deleted records remain excluded from
every retrieval mode.

`valid_from` and `valid_until` describe the interval in which information was
valid and do not exclude an otherwise eligible historical record. M5 does not
create an expiration transition, infer a date, or change M6 trusted-clock policy.
Lifecycle states other than `active` and `superseded` remain ineligible in M5.

SQLite applies the complete owner, indexing, tombstone, and lifecycle rule before
FAISS search. Hydration repeats the same rule and fails closed if state changes or
an unexpected vector appears.

### Downstream retrieval

After eligibility, historical candidates reuse the existing explicit `0.50`
relevance threshold, deterministic ranking, complete-block context construction,
token budgeting, and structured exclusions unchanged.

The existing outcome distinctions remain:

- no lifecycle-eligible vectors: `no_eligible_memory`;
- eligible vectors but none meeting relevance: `no_relevant_memory`;
- relevant memories excluded only by budget: `budget_excluded`;
- one or more included memories: `memories_selected`.

Historical retrieval is read-only. It must not reactivate a superseded memory,
alter lifecycle state, write or remove a relationship, or modify SQLite or FAISS.

## Milestone boundaries

M5 does not add automatic intent classification, expiration transitions,
forgetting, tombstone writes, physical vector deletion, recovery, reconciliation,
observability rollout, or fixed-workload completion. M6–M10 retain those concerns.

No migration is required because the existing schema already stores lifecycle,
relationship, indexing, validity, and tombstone metadata.

## Verification requirements

Tests must prove:

- historical intent is strict and explicit;
- omitted intent preserves current retrieval behavior;
- owner, indexed-state, tombstone, and lifecycle rules are applied before search
  and repeated during hydration;
- active and superseded historical records may be selected;
- pending, failed, deleted, inconsistent, and other-owner records cannot enter
  historical results;
- the same superseded record remains excluded from current retrieval;
- historical retrieval does not mutate or reactivate any record;
- M3 relevance outcomes and exact context budgeting remain unchanged; and
- restart preserves historical retrieval behavior.
