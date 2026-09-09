# M7 global idempotent forgetting bindings

- Status: Accepted
- Date: 2026-09-08
- Scope: M7 only

## Public operation

Forgetting is a separate synchronous application operation:

```python
MemoryService.forget(context: RequestContext, request: ForgetRequest) -> ForgetResult
```

`RequestContext.user_id` is the sole authoritative owner identity. The request
contains no user or owner field.

`ForgetRequest` contains exactly one `memory_id`. It is a strict string and an
opaque stable identifier. Empty values and values with leading or trailing
whitespace are rejected; accepted identifiers are preserved unchanged.

The public outcomes are `forgotten`, `cleanup_pending`, and `not_found`.
Every result includes `outcome`, `reason`, `memory_id`, persisted `deleted_at`,
`retrievable`, `cleanup_complete`, and `retryable`.

Successful first completion uses reason `forgotten`; an owner-authorized
completed repeat uses `already_forgotten`. Both have outcome `forgotten`, a
non-null exact memory ID and trusted UTC deletion time, `retrievable=false`,
`cleanup_complete=true`, and `retryable=false`.

Known-ID incomplete cleanup uses outcome `cleanup_pending`, reason
`physical_cleanup_pending`, `retrievable=false`, `cleanup_complete=false`, and
`retryable=true`. Missing-identity cleanup uses the same flags with reason
`physical_cleanup_identity_pending`.

## Opaque owner boundary

A missing memory ID and an ID belonging to another user return the identical
result: outcome `not_found`, reason `memory_not_found`, null memory ID and
deletion time, and all three flags false. Neither case reads the clock, mutates
SQLite or FAISS, or reveals whether the identifier exists. An unavailable
authoritative owner lookup fails closed through the typed storage boundary.

## Target eligibility and missing mappings

Any durable memory owned by the trusted caller may be logically forgotten,
whether active, superseded, expired, pending, failed, indexed, already
tombstoned, or missing an authoritative vector mapping. Forgetting never
changes lifecycle/indexing state, content, provenance, validity, admission
idempotency, embeddings, or supersession relationships and never reactivates a
predecessor.

An owned pending or failed record without an authoritative mapping is
atomically tombstoned and recorded with `vector_id=NULL`,
`cleanup_state='cleanup_pending'`, and `completed_at=NULL`. It returns retryable
`physical_cleanup_identity_pending`. M7 makes zero FAISS calls, never guesses or
reconstructs an ID, never marks it complete, and every M7 retry remains pending
without clock reads or mutation. M8 exclusively owns recovery of that identity.
The same rule applies to any other owner-authorized durable record without a
mapping. Mapping absence never proves physical completion.

## Durable cleanup schema

`deleted_at` remains the retrieval tombstone; no deletion lifecycle status is
added. Physical cleanup uses a separate table equivalent to:

```sql
CREATE TABLE memory_forgetting (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    vector_id INTEGER,
    cleanup_state TEXT NOT NULL
        CHECK (cleanup_state IN ('cleanup_pending', 'complete')),
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK (vector_id IS NULL OR vector_id > 0),
    CHECK (
        (cleanup_state = 'cleanup_pending' AND completed_at IS NULL)
        OR (
            cleanup_state = 'complete'
            AND vector_id IS NOT NULL
            AND completed_at IS NOT NULL
        )
    ),
    UNIQUE (vector_id),
    FOREIGN KEY (user_id, memory_id)
        REFERENCES memories(user_id, memory_id)
        ON DELETE CASCADE
);
```

Pending cleanup always has null `completed_at`. Complete cleanup requires a
known vector ID, trusted UTC `completed_at`, no live mapping, and verified
durable FAISS absence. Migration backfills mapped tombstones as pending with
their ID and unmapped tombstones as pending with null ID; both retain
`deleted_at` as `requested_at` and use null `completed_at`.

## Trusted timestamps

Fresh logical deletion reads the injected clock exactly once under the process
write lock, validates the M6 aware-UTC contract, and persists the unchanged
value as `deleted_at` and `requested_at`. Invalid output raises
`ConfigurationError("invalid_trusted_clock")` before mutation.

After known-ID durable FAISS absence is verified, completion reads and validates
the same injected clock exactly once and atomically stores it unchanged as
`completed_at`. No SQLite time, caller time, fallback, FAISS metadata time, or
reused request timestamp may supply it. Invalid completion time leaves cleanup
pending, its completion time null, and its mapping retained.

A first call that completes may read twice: once per distinct persisted event.
Null-ID retries and completed repeats read zero times. Known-ID completion
attempts read once after durable absence is established.

## Workflow and ordering

The complete workflow shares the M1 process-wide write lock with admissions and
retries:

1. validate context and request;
2. owner-scope target resolution, returning opaque `not_found` when absent;
3. return completed state without clock/FAISS work;
4. return existing null-ID pending state without clock/FAISS work;
5. for a fresh target, obtain the trusted deletion time;
6. atomically write/retain the tombstone and create pending cleanup, copying an
   authoritative mapping or storing null;
7. commit logical exclusion before any FAISS call;
8. return null-ID pending with zero FAISS calls, or remove the known ID using
   the verified copy-on-write durable-generation protocol;
9. after verified absence, obtain the trusted completion time; and
10. atomically mark cleanup complete, store `completed_at`, and remove the
    matching live mapping.

If stored and live non-null IDs disagree, fail closed with zero FAISS mutation;
M8 owns reconciliation. FAISS failure never clears the tombstone. A verified
generation already lacking the ID is idempotent success without publication.
Unrelated vectors must remain intact, and supported same-process instances
reload durable state under the shared lock. No distributed lock is added.

## Mapping, retry, restart, and concurrency

Known mappings remain throughout pending cleanup and are copied to the cleanup
record before FAISS mutation. Only the completion transaction removes the live
mapping. Null-ID M7 operations never create, adopt, restore, replace, or remove
a mapping—even if one later appears—and remain assigned to M8.

Known-ID retries reuse persisted identity/timestamps and retry only removal or
acknowledgement. If removal succeeded but acknowledgement failed, retry verifies
absence, reads one completion time, and retries acknowledgement. Uncertain FAISS
durability remains pending; M7 does not rebuild or reconcile.

Correctness after restart follows persisted SQLite state and verified durable
FAISS state. Concurrent operations serialize under the existing process lock;
owner/state resolution is revalidated transactionally, and every exception
releases workflow and transaction resources.

## Failure outcomes

| Condition | SQLite state | FAISS | Result |
|---|---|---|---|
| Invalid request/context | unchanged | zero calls | validation error |
| Missing/cross-owner | unchanged | zero calls | opaque `not_found` |
| Owner lookup unavailable | unchanged/unknown | zero calls | storage error |
| Invalid deletion clock | unchanged | zero calls | `invalid_trusted_clock` |
| Tombstone transaction failure | unchanged | zero calls | storage error |
| Owned record without mapping | tombstoned, null-ID pending, no completion time | zero calls | `physical_cleanup_identity_pending` |
| Null-ID retry, even if mapping appears | unchanged null-ID pending | zero calls | same pending result; M8 owns recovery |
| Known stored/live ID mismatch | pending; no rewrite | zero calls | storage error |
| FAISS failure/uncertainty | tombstoned, mapped, pending | targeted attempt | `physical_cleanup_pending` |
| Invalid completion clock | tombstoned, mapped, pending, null completion time | vector absent | `invalid_trusted_clock` |
| Acknowledgement failure | tombstoned, mapped, pending, null completion time | vector absent | `physical_cleanup_pending` |
| Completion succeeds | tombstoned, mapping removed, complete with completion time | vector absent | `forgotten` |
| Completed repeat | unchanged complete state | zero calls | `already_forgotten` |

Every post-tombstone state is excluded from current and historical retrieval.

## Required evidence

Tests must cover strict/exact request and result contracts; opaque missing and
cross-owner zero-effect behavior; every lifecycle/indexing target state;
mapped/unmapped tombstone migration; null-ID zero-clock/zero-FAISS same-process
and restart retries; both trusted timestamps and their state invariants;
all-path exclusion; targeted deletion preserving unrelated vectors; failures
and lock release; durable restart; concurrency; mapping retention/removal; no
reactivation; and persisted `demo-forgetting` output.

## Frozen exclusions

M7 adds no lifecycle reactivation/rewrite, inferred conflict handling, mapping
reconstruction, global FAISS scan, rebuild/reconciliation/degraded startup,
persistent observability/audit rollout, production authentication/compliance,
distributed locking, background worker/scheduler, or M8-M10 behavior.
