# D4 Phase 10 — API Boundaries

## Purpose

The D4 work has validated individual memory-system behaviors through experiments, but it has not yet implemented a production API around them.

This document defines the logical boundary between the memory subsystem and the application without implementing that API.

## Operations the memory subsystem needs

### 1. Store memory

Input should provide:

- user identity;
- memory content;
- memory subject/value when available;
- source/authority information;
- lifecycle information when applicable.

The operation should return the resulting memory identity and lifecycle state.

### 2. Retrieve memories

Input should provide:

- user identity;
- query;
- retrieval requirements.

The user identity must remain part of the authorization boundary rather than being treated as ordinary query text.

The result should contain only memories authorized for that user and should reflect the D4 lifecycle/conflict rules.

### 3. Delete / forget memory

Input should provide:

- user identity;
- memory identity or another explicitly supported memory selector.

The operation must enforce ownership.

Phase 8 already validated:

- authorized deletion;
- unauthorized deletion;
- idempotent deletion;
- protection of another user's memory;
- deletion failure handling;
- forgetting a newer memory does not reactivate an older memory.

This document therefore does not repeat those experiments.

### 4. Historical retrieval

Current-state retrieval and historical retrieval are different operations at the logical boundary.

Current-state retrieval should use the active/current memory state.

Historical retrieval may expose superseded memories when the query explicitly requires historical context.

## Ownership boundary

The memory subsystem must receive an authenticated user identity from the calling application.

The caller should not be able to retrieve another user's memories merely by changing query text.

The D4 retrieval experiment already demonstrated this invariant using authorized FAISS IDs and `IDSelector`.

## What is not defined yet

This document does not choose:

- HTTP versus another transport;
- authentication implementation;
- request/response schema;
- deployment topology;
- versioning strategy;
- API framework;
- public endpoint names.

Those decisions would be implementation/deployment decisions rather than something established by the current D4 evidence.

## Current conclusion

The memory subsystem has four clear logical operations:

1. store;
2. retrieve;
3. delete/forget;
4. retrieve historical memory.

The user identity is an authorization input to these operations.

The current D4 work validates the behavior behind these boundaries, but a production-facing API has not yet been implemented or validated.