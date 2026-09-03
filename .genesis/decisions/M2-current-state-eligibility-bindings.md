# M2 Current-State Eligibility Bindings

- **Status:** Accepted
- **Date:** 2026-09-03
- **Scope:** M2 current-state retrieval only

## Context

M2 must turn the existing owner-scoped, indexed SQLite allowlist into a complete
current-state eligibility boundary before FAISS search. The approved design records
leave the persistent deletion representation and exact validity-boundary comparisons
open, so those details are fixed here before implementation.

## Decision

A memory is eligible for M2 current-state retrieval only when all of these conditions
hold:

1. it is owned by the trusted requesting user;
2. its indexing state is `indexed`;
3. its nullable read-side tombstone `deleted_at` is `NULL`;
4. its lifecycle status is exactly `active`;
5. `superseded_by` is `NULL`;
6. its validity interval contains one trusted current time: `valid_from` is absent or
   `valid_from <= now`, and `valid_until` is absent or `now < valid_until`.

The start boundary is inclusive. The end boundary is exclusive: a memory is no
longer current at exactly `valid_until`.

The application obtains `now` once from the trusted clock for each retrieval and
passes that same value to both the pre-search SQLite eligibility query and the
post-search hydration check. SQLite remains authoritative, and only IDs satisfying
the complete rule may enter the FAISS search allowlist. Hydration repeats the same
rule to fail closed if state changes or an unexpected ID appears before results are
ranked or placed in context.

`deleted_at` is a read-side tombstone only in M2. This decision does not authorize a
forget command, deletion transition, physical FAISS removal, automatic expiration
transition, automatic or inferred supersession, conflict resolution, historical
retrieval, or reactivation behavior.

## Milestone boundaries

- M4 retains ownership of supersession and conflict mutation behavior.
- M6 retains ownership of expiration transitions.
- M7 retains ownership of forgetting, tombstone writes, and physical vector removal.
- M2 may add the nullable persisted field and seed it in tests solely to prove that
  current-state reads exclude tombstoned records.

This binding refines M2 implementation details without changing locked `PLAN.md` or
`DONE.html`.
