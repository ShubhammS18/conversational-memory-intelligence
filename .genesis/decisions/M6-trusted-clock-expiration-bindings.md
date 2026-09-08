# M6 trusted-clock and expiration bindings

- Status: Accepted
- Date: 2026-09-08
- Scope: M6 only

## Trusted clock

Current-state retrieval obtains time exclusively from the injected trusted
clock. A valid trusted-clock result:

- is a `datetime`;
- is timezone-aware;
- has a UTC offset of exactly zero; and
- is returned and used unchanged as the single trusted `now` for that
  retrieval.

Missing values, non-`datetime` values, naive datetimes, and non-UTC offsets are
invalid. Invalid trusted-clock output fails closed with
`ConfigurationError("invalid_trusted_clock")`.

Each current-state retrieval calls the trusted clock exactly once, after
request/configuration validation and before expiration or eligibility work.
That same validated value is supplied to:

1. the owner-scoped expiration transition;
2. the pre-search current-state SQLite allowlist; and
3. defensive current-state hydration.

No second clock reading may occur inside that retrieval.

Historical retrieval does not consult the clock and must not trigger an
expiration transition.

## Lazy expiration transition

Expiration is evaluated lazily during current-state retrieval. Before FAISS
search, the application invokes one owner-scoped SQLite operation under the
existing approved process write lock.

The SQLite operation uses one atomic write transaction and may transition only
a row satisfying all of the following at the supplied trusted `now`:

- `user_id` equals the trusted owner;
- `lifecycle_status = active`;
- `indexing_state = indexed`;
- `deleted_at IS NULL`;
- `superseded_by IS NULL`;
- `valid_until IS NOT NULL`; and
- `valid_until <= now`.

The exact validity boundary is inclusive for expiration: at
`now == valid_until`, the memory is no longer current and is transitioned to
`expired`. This is the approved implementation of the existing
inclusive-start/exclusive-end current-validity contract.

`valid_from` is not an additional transition precondition: `valid_until`
determines whether an otherwise-current record has expired.

Rows that are pending, failed, deleted, superseded, already expired, owned by
another user, or lack `valid_until` are not transitioned.

The transition changes only `lifecycle_status` from `active` to `expired`.
It preserves:

- `valid_from` and `valid_until`;
- embeddings and stable vector mappings;
- FAISS vectors;
- idempotency state;
- `supersedes_memory_id`; and
- `superseded_by`.

No FAISS deletion, publication, rebuilding, reconciliation, or other vector
mutation is part of M6 expiration.

The operation is idempotent. Repeating it with the same or later trusted time
may update zero rows and still succeeds.

If the transition cannot be completed or its result cannot be trusted, current
retrieval fails closed before FAISS search. It must not return candidates from
a possibly stale allowlist or report a successful partial retrieval.

## Historical retrieval of expired memories

Historical retrieval remains explicitly requested, owner-scoped, and strictly
read-only. In addition to the M5 active and superseded cases, a persisted
expired memory is historically eligible only when:

- it belongs to the trusted owner;
- `indexing_state = indexed`;
- `deleted_at IS NULL`;
- `lifecycle_status = expired`; and
- `superseded_by IS NULL`.

Historical retrieval does not apply current-time validity-window filtering.
It does not reactivate, rewrite, or otherwise mutate expired records.

An expired record with inconsistent relationship state fails closed and is not
historically eligible. Current-state retrieval always excludes persisted
expired records.

## Admission timestamps

Trusted `created_at` values and default `valid_from` values continue to come
from the injected trusted clock. Explicit temporal inputs must be timezone-aware
instants, are canonicalized to UTC for persistence and fingerprinting, and must
satisfy `valid_from <= valid_until` when both are supplied. Naive or invalid
temporal values are rejected before embedding or durable mutation.

`source_event_at` remains provenance supplied by the request; it is not a
substitute for the trusted processing clock.

## Frozen exclusions

M6 does not add a scheduler, background/global expiration scan, physical
deletion, forgetting, retention enforcement, supersession inference, historical
reactivation, FAISS reconciliation, distributed locking, or any M7-M10
behavior.
