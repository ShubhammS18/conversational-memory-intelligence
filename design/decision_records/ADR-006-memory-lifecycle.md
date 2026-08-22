# ADR-006 --- Memory Lifecycle

-   **Status:** Accepted
-   **Date:** 2026-08-17

## Context

D4 established that memory cannot be treated as only text plus an embedding. The system needs lifecycle information to distinguish current information from historical information and handle changes without
destroying useful history.

ADR-001 already provides `lifecycle_status`, optional `valid_from` / `valid_until`, and explicit relationships such as `supersedes` / `superseded_by`. ADR-002 establishes that a genuinely new preference or decision can supersede an existing one, while uncertain or temporary statements should not automatically do so. ADR-004 establishes that lifecycle and explicit supersession relationships are part of hierarchical conflict resolution.

The remaining lifecycle question was how these states and transitions should behave over time, including corrections, expiration, reversion to an older value, historical retrieval, forgetting, and possible future consolidation or reflection.

Lifecycle state must interact with query intent: a superseded or expired memory is not necessarily useless because it may be needed to answer a historical question.

## Decision drivers

-   Preserve historical information when a memory is superseded.
-   Distinguish current state from historical state.
-   Treat corrections as supersession rather than a separate lifecycle
    state.
-   Support explicit temporal validity when known.
-   Avoid arbitrary age-based expiration or decay without evidence.
-   Treat forgetting differently from supersession or expiration.
-   Never automatically reactivate an old superseded memory.
-   Do not add consolidation or reflection without evidence that the
    workload requires them.
-   Allow lifecycle state to interact with query intent.
-   Keep the lifecycle model small enough to test independently.

## Options considered

### Option A --- Delete the old memory when new information appears

Rejected because it destroys useful history, prevents historical questions, and conflates normal updates with explicit forgetting.

### Option B --- Create a separate lifecycle status for every event

Rejected because it mixes state with event/operation, creates unnecessary complexity, and would require separate states such as `CORRECTED`, `CONSOLIDATED`, and `REFLECTED` without evidence that they
are needed.

### Option C --- Minimal lifecycle state with explicit transitions and separate operations

Selected.

``` text
ACTIVE
  ├── superseded by newer memory → SUPERSEDED
  └── validity period ends       → EXPIRED

forget request
  └── deletion operation         → memory removed
```

Corrections create a new memory that supersedes the old one.
Consolidation and reflection remain optional operations.

## Decision

We will use a **minimal lifecycle model with explicit transitions and separate lifecycle operations**.

### 1. Active

A newly admitted durable memory is normally `active`. An active memory can be considered current subject to relevance, provenance, temporal validity, and conflict resolution.

### 2. Supersession

When new authoritative information genuinely replaces an existing memory, the old memory becomes `superseded` and the new memory becomes `active`. The old memory is not deleted.

A supersession relationship must identify the specific memory being replaced rather than being inferred merely from matching subjects and different values.

### 3. Correction

A correction is treated as supersession:

``` text
M1: Python 3.10
M2: Correction: Python 3.12

M1 → superseded by M2
M2 → active
```

A separate `corrected` lifecycle status is not required.

### 4. No automatic reactivation

If an old value becomes current again, create a new memory rather than reactivate the old superseded record:

``` text
M1: FAISS
M2: Qdrant
M3: FAISS

M1 = superseded
M2 = superseded
M3 = active
```

This preserves the sequence of state changes.

### 5. Expiration

A memory may become expired when an explicit validity boundary has ended:

``` text
valid_from  = 2026-08-01
valid_until = 2026-08-15
```

After the validity period, `status = expired`. Expired memories are not treated as current by default.

Generic age-based expiration or decay is not adopted without evidence that it is needed.

### 6. Deletion / forgetting

Explicit forgetting is separate from supersession and expiration. When implemented, forgetting must remove the memory from authoritative storage and retrieval paths rather than merely marking it superseded.

Authorization, consistency-window, and index-deletion behavior will be designed and validated in the later isolation/privacy work.

### 7. Consolidation

Consolidation is not part of the mandatory lifecycle state machine. It may be considered later only if measured redundancy creates a problem that the existing representation, retrieval, ranking, and
context-selection mechanisms cannot adequately handle. Provenance and source relationships must remain traceable if introduced.

### 8. Reflection

Reflection is not part of the core lifecycle state machine at this stage. D3 does not establish a need for generated higher-level memories. If introduced later, derived memories must remain distinguishable from explicit user evidence through provenance.

### 9. Lifecycle-aware retrieval

Lifecycle state is an eligibility signal, not a universal retrieval filter:

``` text
query
  ↓
determine current-vs-historical intent
  ↓
retrieve authorized candidates
  ↓
apply lifecycle-aware eligibility
  ↓
resolve conflicts and rank
  ↓
construct context
```

A current-state question should normally favor active memories and exclude superseded/expired states from current-state selection.

A historical question may legitimately require a superseded historical memory.

> **Lifecycle state describes the currentness and validity of a memory;
> query intent determines whether that lifecycle state is relevant to
> the question.**

The experiments validate this policy once intent is known; they do not yet validate an automatic natural-language intent classifier.

## Consequences and trade-offs

### Benefits

-   Current and historical states are distinguishable.
-   Superseded memories remain available for historical questions.
-   Corrections reuse supersession.
-   Explicit validity periods are supported without arbitrary global
    decay.
-   Forgetting remains a distinct operation.
-   The model stays small and testable.
-   The design fits ADR-001 and complements ADR-004.
-   Query intent prevents historical memories from being treated as
    current.

### Costs and risks

-   Retrieval is more complex because lifecycle and query intent both
    matter.
-   Historical and current questions need different eligibility rules.
-   Expiration depends on reliable temporal information.
-   Deletion requires consistency across storage and retrieval indexes.
-   Superseded memories consume storage.
-   Different memory types may eventually need different lifecycle
    semantics.
-   Query-intent handling could introduce errors or latency.
-   Consolidation and reflection remain open capabilities.

## Validation performed

### Supersession --- PASS

The existing D4 conflict-resolution experiment demonstrated that an explicitly superseded memory becomes `superseded`, points to its replacement, and is excluded from current active memories.

### No automatic reactivation --- PASS

A controlled sequence `M1: FAISS → M2: Qdrant → M3: FAISS` produced `M1 = superseded`, `M2 = superseded`, `M3 = active`. The original FAISS record was not reactivated.

### Explicit expiration --- PASS

A memory with `valid_until = 2026-08-15` was evaluated on 2026-08-16 and became `expired`; a memory without an expiration boundary remained active.

### Query-intent-dependent eligibility --- PASS

With `M1 = FAISS → superseded` and `M2 = Qdrant → active`, the current-state query selected only M2, while the historical query allowed M1 to remain eligible.

These are component-level controlled validations, not production-scale performance claims.

## Revisit conditions

Revisit this decision if testing shows that:

-   the minimal lifecycle model cannot represent an important state;
-   current-state retrieval still selects superseded or expired
    memories;
-   historical queries cannot recover relevant historical memories;
-   explicit validity periods are too unreliable;
-   different memory types require substantially different lifecycle
    semantics;
-   consolidation becomes necessary to control measured redundancy or
    storage growth;
-   reflection provides measurable value that cannot be achieved
    otherwise;
-   deletion cannot be implemented consistently;
-   query-intent-dependent eligibility introduces unacceptable errors or
    latency.

The lifecycle states, transitions, and retrieval eligibility rules should be refined if broader D4 evaluation produces evidence that the current model is insufficient.
