# ADR-001: Memory Representation

Status: proposed
Date: 2026-08-11

## Context

The D3 baseline represented memory mainly as stored text plus an embedding and used semantic similarity to retrieve the top-k memories.

That was enough to find semantically related information, but D3 showed that relevance is not the same as validity or ownership. The baseline could retrieve multiple historical states of the same topic, another user's memory, and a sensitive memory that should no longer be retained.

This means the improved system needs some information beyond the memory text and embedding. At the same time, I do not want to make every memory a highly structured graph object because that would add extraction and maintenance complexity without evidence that the project needs it.

## Decision drivers

The representation should:

- preserve the original memory content;
- support semantic retrieval;
- identify which user owns the memory;
- give downstream components enough information to reason about the type and meaning of a memory;
- represent time when a memory's validity changes;
- preserve historical memories instead of overwriting them;
- record how the memory was obtained, especially whether it was explicitly stated or inferred;
- support explicit relationships such as supersession when they are needed;
- remain simple enough to implement and evaluate in this project.

## Options considered

### Option A — Text + embedding only

Each memory would contain its natural-language content and embedding.

I rejected this because it is essentially the D3 approach. It does not give the system an explicit representation of user ownership, lifecycle, provenance, or changing validity.

### Option B — Fully structured memory records

Each memory would be forced into a rigid structure such as subject, value, type, and temporal fields.

I rejected making all of these fields mandatory. Some memories do not naturally fit a subject/value representation, and forcing them into one could make extraction unnecessarily brittle.

### Option C — Full entity/relationship graph

The system could represent memories as entities and relationships in a graph.

I am not choosing this as the default because D3 did not establish that a graph database or full graph architecture is necessary. It would also introduce additional entity extraction, resolution, and graph-maintenance complexity.

### Option D — Hybrid representation

Keep the original natural-language memory and embedding, but add a small set of structured attributes and explicit relationships where they are useful.

This gives the system more information for reasoning about identity, meaning, time, provenance, and lifecycle without committing the project to a full graph architecture.

## Decision

I will use a hybrid memory representation.

### Required fields

- `memory_id` — unique identifier for the memory.
- `user_id` — owner/tenant identity and part of the retrieval scope.
- `content` — the natural-language memory.
- `embedding` — semantic representation used for candidate retrieval.
- `type` — controlled memory category.
- `provenance` — how the memory was obtained, including whether it was explicitly stated by the user or inferred by the system.
- `created_at` — when the memory record was created.
- `lifecycle_status` — the current lifecycle state of the record.

### Optional fields

- `subject` — what the memory is about, when a structured subject is useful.
- `value` — the structured value, when the memory naturally has one.
- `valid_from` — when the information became valid, when known.
- `valid_until` — when the information stopped being valid, when known.
- `relationship` — an explicit relationship to another memory, such as `supersedes` or `superseded_by`.

A memory that is replaced by a newer memory will not simply be overwritten. The older memory will remain available as historical information and its lifecycle/relationship data will indicate that it has been superseded.

I am deliberately not making `retention_intent`, `allowed_to_store`, or `sensitivity` core memory fields at this point. D3 Case 5 points more directly toward an admission and deletion policy, which will be designed separately.

## Consequences and trade-offs

### Benefits

- The system has more information than semantic similarity alone when deciding which memory is useful.
- User identity can become an explicit retrieval constraint.
- Older memories can be preserved without treating them as current.
- Provenance can distinguish an explicit user statement from a system inference.
- Temporal information can support changing-state questions.
- Explicit relationships can support supersession without requiring a full graph database.

### Costs and risks

- More fields mean more extraction and validation work.
- Incorrect structured fields could cause incorrect decisions downstream.
- Temporal and lifecycle logic will make updates more complicated than simply storing another embedding.
- The hybrid design still depends on semantic retrieval for candidate generation.
- We need to validate whether the additional structure actually improves the D3 failure cases enough to justify its complexity.

## Validation plan

This representation will be validated against the D3 failure workload as the D4 components are implemented.

At minimum, validation should check that:

- memories are scoped to the correct `user_id`;
- a newer memory can supersede an older memory without destroying the historical record;
- explicit and inferred memories can be distinguished;
- temporal fields can represent changing validity when the information is known;
- structured fields do not introduce unacceptable extraction errors or unnecessary complexity.

The final D4 design should be compared with the frozen D3 baseline using the same six failure cases.

## Revisit conditions

I will revisit this decision if testing shows that the structured fields do not improve the required memory decisions enough to justify their extraction and maintenance cost and if the representation creates unacceptable extraction errors, latency, storage overhead, or lifecycle complexity.

If later evidence shows that a full graph representation is necessary for a specific requirement, that should be considered as a separate design decision rather than added by default.
