# D4 Phase 10 — Migration Analysis

## Existing migration history

The project previously migrated from `vector_store.pkl` to FAISS.

This is relevant because the memory system itself now depends on:

- memory metadata;
- memory IDs;
- lifecycle state;
- user ownership;
- FAISS vector IDs.

A future storage/index migration would therefore need to preserve more than just the vector embeddings.

## What must remain consistent during a migration

A migration of the memory system must preserve the relationship between:

- `memory_id`;
- user ownership;
- memory content;
- embedding/vector;
- subject/value;
- lifecycle status;
- supersession relationships;
- source/authority information.

Changing only the vector index would not be sufficient if the metadata and lifecycle state were left inconsistent.

## Current D4 position

No future storage migration is being implemented as part of D4 Phase 10.

The scale experiment identified a practical limitation of the current FAISS approach, but it did not establish that a migration is currently required for the project's scope.

Therefore I am recording migration as an operational requirement rather than introducing a new storage system.

## If migration becomes necessary

A future migration would need to establish:

1. the source of truth during migration;
2. how existing memories are exported;
3. how embeddings and metadata are transferred;
4. how memory IDs are preserved;
5. how user isolation is preserved;
6. how lifecycle and supersession state is preserved;
7. how the migrated index is validated before cutover;
8. how rollback would work if validation fails.

These have not been implemented or experimentally validated yet.

## Current conclusion

The current D4 design does not include a migration mechanism.

The important engineering requirement identified here is that any future migration must preserve memory identity, ownership, lifecycle state, and vector-to-memory mappings together.

This is an identified operational requirement, not an implemented feature.