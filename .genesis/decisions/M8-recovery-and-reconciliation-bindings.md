# M8 recovery and reconciliation bindings

- Status: Accepted
- Date: 2026-09-09
- Scope: M8 only

## Authority and operation boundary

SQLite is the sole authoritative store for memory identity, ownership,
embedding bytes and metadata, indexing state, lifecycle state, supersession
relationships, tombstones, forgetting state, and stable vector mappings.
FAISS is derived state and never repairs or overrides SQLite.

Recovery is an internal startup operation. It accepts no user identity or
request content and grants no normal memory-operation authority. Authentication,
remote recovery workers, queues, distributed coordination, and high
availability remain outside M8.

Recovery uses the configured immutable embedding model identifier and vector
dimension. It generates no embeddings and performs no model download.

## Strict recovery result

The internal result uses exactly these readiness values:

- `ready`: every authoritative vector that may be retrieved is present in one
  verified durable FAISS generation, no unsafe inconsistency exists, and no
  safely excluded recovery work remains.
- `degraded`: the verified durable generation is safe for every retrievable
  memory, while one or more unresolved records are deterministically excluded
  by authoritative SQLite state.
- `unavailable`: SQLite authority cannot be established, an authoritative
  retrievable record cannot be represented safely, durable publication cannot
  be verified, or any inconsistency could expose or omit an eligible memory.

The immutable result records readiness, a stable reason code, whether a
generation was rebuilt, final vector count, removed orphan count, and counts of
unresolved pending, failed, and forgetting-cleanup records. Composition may
return a service only for `ready` or `degraded`. `unavailable` refuses startup;
it is never silently converted to an empty or degraded service.

The approved reason codes are:

- `ready_existing_generation`
- `ready_rebuilt_generation`
- `degraded_excluded_work_pending`
- `unavailable_sqlite_integrity`
- `unavailable_schema_or_migration`
- `unavailable_authoritative_identity`
- `unavailable_embedding_configuration`
- `unavailable_rebuild`
- `unavailable_publication`
- `unavailable_post_publication_verification`

## SQLite audit

Recovery first applies only the existing ordered, checksum-verified migrations.
A migration gap, changed migration checksum, failed migration, or schema newer
than the implementation is `unavailable_schema_or_migration`.

Under one consistent SQLite read transaction, recovery must verify:

1. SQLite integrity and foreign-key checks succeed.
2. Required tables, columns, indexes, uniqueness rules, and CHECK constraints
   match the applied migration history.
3. Every admission-idempotency, embedding, mapping, and forgetting reference
   resolves to the expected memory and owner.
4. Stable vector IDs are positive, unique, and associated with at most one
   memory.
5. Stored embedding blobs decode as finite float32 values with their recorded
   dimension and exact configured model identifier.
6. Indexing, lifecycle, supersession, deletion, mapping, and forgetting states
   satisfy their existing M1-M7 invariants.

Recovery does not infer ownership, memory identity, relationships, or missing
stable IDs. Ambiguous identity or relationship state is unavailable unless an
explicit safely-excluded rule below applies.

## Authoritative rebuild set

The desired FAISS vector set is derived only from audited SQLite rows.

Include a mapping when its memory:

- is not tombstoned;
- has a valid stored embedding for the configured model and dimension; and
- has indexing state `indexed`, `pending`, or `failed`.

This includes valid active, superseded, and expired records because explicit
historical retrieval may use indexed superseded or expired records. Current and
historical eligibility remain governed by M2, M5, and M6 after startup.

Pending and failed records may have their known stable vectors restored, but
remain excluded from retrieval by SQLite. Physical reconstruction never changes
their indexing state.

Exclude every tombstoned memory from the rebuild set, including cleanup-pending
records. A completed forgetting record must have no live mapping. A completed
record with a mapping is unavailable.

## Mapping and embedding failures

For an `indexed`, non-deleted memory:

- a missing mapping, missing embedding, invalid embedding, duplicate identity,
  or incompatible model/dimension makes startup unavailable;
- recovery never allocates a replacement vector ID or re-embeds content.

For a `pending` or `failed`, non-deleted memory:

- a valid mapping and embedding are included under the same stable vector ID;
- a missing mapping or unusable embedding remains safely excluded and produces
  degraded readiness;
- recovery does not invent an ID, promote indexing state, or declare the
  admission successful.

An orphan embedding, dangling reference, conflicting non-null vector identity,
or owner inconsistency is authoritative corruption and makes startup
unavailable.

## FAISS audit, rebuild, and orphan handling

A durable FAISS generation is accepted only when its index and metadata both
exist and verify the existing format version, checksum, generation identifier,
index kind, configured model, configured dimension, vector count, and exact
vector-ID digest.

If that verified vector-ID set exactly equals the authoritative rebuild set,
recovery reuses it without publication.

If the pair is missing, incomplete, damaged, configuration-incompatible, or
contains a different ID set, recovery rebuilds from audited SQLite embeddings:

- missing authoritative IDs are restored with their existing stable IDs;
- IDs absent from the authoritative rebuild set are orphans and are removed by
  omission from the rebuilt generation;
- no vector is copied from an unverified FAISS generation;
- no embedding is recomputed.

An orphan never creates a SQLite memory or mapping. Its presence alone is
repairable when SQLite has passed the full audit.

## Pending and failed admission recovery

M8 reuses the M1 persisted pending/indexed/failed and stable-ID
duplicate-prevention contract.

For mapped pending or failed records with valid stored embeddings, recovery may
restore or verify their physical vectors under the existing stable IDs. It
must not mark them indexed.

M4 targeted replacements cannot be distinguished safely from ordinary
admissions solely from a pending memory row. Recovery therefore never invents
or completes a supersession acknowledgement. Ambiguous supersession recovery
remains fail-closed: the replacement stays pending or failed and excluded, all
relationships remain unchanged, and readiness is degraded.

The identical original admission request remains responsible for completing
the existing M1 ordinary-index acknowledgement or M4 atomic relationship
acknowledgement. Recovery adds no new admission or supersession policy.

## M7 forgetting handoff

For `cleanup_pending` with a known vector ID:

- the stored forgetting ID and live mapping must agree;
- the ID is excluded from the rebuild set;
- after the rebuilt or reused durable generation verifies its absence,
  recovery obtains a separately validated trusted UTC completion time and uses
  the existing atomic forgetting acknowledgement;
- acknowledgement removes the matching live mapping and stores non-null
  `completed_at`.

If durable absence is uncertain or acknowledgement fails, the tombstone remains
effective, cleanup stays pending, the mapping remains, and readiness is
degraded. No retrieval may expose the memory.

For `cleanup_pending` with `vector_id=NULL`:

- if no live mapping exists, recovery makes no FAISS guess, leaves cleanup
  pending, and reports degraded readiness;
- if exactly one audited live mapping later exists for that same memory,
  recovery may atomically adopt that ID into the still-pending forgetting
  record, exclude it from the rebuild set, verify durable absence, and then use
  the normal completion acknowledgement;
- any conflicting, multiple, cross-memory, or unverifiable identity makes
  startup unavailable.

A tombstoned memory without its required forgetting record after migrations is
unavailable. Recovery does not invent deletion/request timestamps or silently
backfill post-migration corruption.

## Empty-store behavior

When the authoritative rebuild set is empty and no unsafe inconsistency exists,
recovery creates or verifies an empty durable FAISS generation.

An otherwise empty store containing only safely excluded unresolved pending,
failed, or null-ID cleanup records is degraded, not unavailable. It remains
non-retrievable and no vector identity is guessed.

A genuinely empty consistent SQLite store with a verified empty generation is
ready.

## Publication and verification

Recovery builds a complete candidate generation in temporary files, syncs and
verifies the candidate, and only then replaces the fixed final index and
metadata files through the existing copy-on-write protocol.

After replacement, recovery syncs the directory and reloads and re-verifies the
complete final pair against the exact authoritative rebuild set. Readiness is
not published before this verification.

A crash between replacing the two final files may leave an incomplete pair.
That pair is never accepted; the next startup rebuilds it from SQLite. M8 does
not claim cross-file transactional filesystem semantics.

Any build, write, sync, replacement, reload, checksum, metadata, or exact-ID
verification failure returns unavailable. A cached in-memory FAISS object is
never evidence of durable publication.

## Locking, retries, and idempotency

The complete audit, rebuild, publication, forgetting reconciliation, and final
readiness audit use the same process-wide write lock as admission, retry, and
forgetting workflows. Repository and FAISS adapter locks remain subordinate.
Recovery must not acquire the process lock recursively through a public
workflow, and every exception path releases all locks and transactions.

M8 adds no cross-process or distributed lock. Concurrent processes are outside
the approved deployment model.

Repeated recovery against unchanged authoritative state is idempotent:

- an exact verified generation is not republished;
- stable mappings and embeddings are unchanged;
- completed forgetting is not acknowledged again;
- pending/failed records are not duplicated or promoted;
- unresolved safely excluded work returns the same degraded classification.

After any successful repair or forgetting acknowledgement, recovery repeats the
authoritative audit and exact durable-generation comparison before returning.

## Required evidence

Tests must cover:

- migration/schema, integrity, foreign-key, and invariant audit failures;
- ready, degraded, and unavailable outcomes and every approved reason family;
- empty-store creation and idempotent second startup;
- missing, incomplete, damaged, stale-model, wrong-dimension, and checksum-
  invalid FAISS generations;
- exact rebuild from stored float32 embeddings with stable IDs;
- missing authoritative vectors and unrelated FAISS orphan removal;
- indexed mapping/embedding failures versus safely excluded pending/failed
  failures;
- pending/failed reconstruction without promotion or duplication;
- ambiguous targeted supersession remaining fail-closed;
- known-ID and null-ID M7 handoff, later mapping adoption, acknowledgement
  failure, restart, and idempotency;
- atomic publication failures and post-publication verification failure;
- shared-lock serialization and lock release;
- preservation of owner, lifecycle, supersession, deletion, idempotency, and
  retrieval invariants; and
- durable `demo-recovery` output followed by an idempotent fresh restart.

## Frozen exclusions

M8 adds no inferred supersession, lifecycle rewrite, reactivation, re-embedding,
multi-model index, remote worker, queue, distributed lock, HA protocol,
background scheduler, production-scale optimization, observability rollout,
fixed-workload evaluation, or M9-M10 behavior.
