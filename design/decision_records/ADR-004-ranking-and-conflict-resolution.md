# ADR-004 — Ranking and Conflict Resolution

- **Status:** Proposed
- **Date:** 2026-08-14

## Context

D3 showed that semantic similarity is useful for finding memories that are related to a query, but it does not tell the system which of several related memories is still valid.

In the baseline, old and new versions of the same decision could both be retrieved. The problem was therefore not only retrieval relevance. The system also needed a way to reason about changing state, supersession, and conflicting memories.

The memory representation already gives us some of the information needed for this: provenance, temporal fields, lifecycle state, and explicit relationships such as `supersedes`.

I therefore tested several small conflict-resolution rules instead of assuming that a single ranking score would be sufficient.

The experiments showed three important things:

1. Recency alone is not enough.
2. A single additive score is not a safe way to combine recency, provenance, and relationship information.
3. Conflict resolution needs to distinguish between explicit relationships, evidence authority, recency, and genuinely unresolved conflicts.

## Decision drivers

- Avoid treating semantic relevance as equivalent to current validity.
- Preserve historical memories instead of deleting them when they are superseded.
- Prevent inferred information from silently overriding stronger explicit user evidence.
- Avoid inventing supersession relationships simply because two memories have the same subject and different values.
- Use recency when it provides useful evidence, but not as a universal override.
- Allow the system to represent an unresolved conflict instead of forcing an unsupported winner.
- Keep the decision logic simple enough to test with controlled cases.
- Preserve the existing memory representation rather than introducing a separate ranking infrastructure without evidence.

## Options considered

### Option A — Similarity score only

The system could continue to rank memories only by semantic similarity, as in the D3 baseline.

**Advantages**

- Simple.
- Already implemented in the baseline.
- Works reasonably for straightforward memory lookup.

**Problems**

- Does not distinguish current from historical information.
- Can retrieve multiple versions of the same decision.
- Cannot represent explicit supersession.
- Cannot distinguish explicit user statements from system inference.

**Rejected.**

D3 already demonstrated that similarity can retrieve both old and new states of the same topic.

### Option B — Newest memory always wins

The system could treat the most recent memory as the current state.

**Advantages**

- Very simple.
- Naturally handles some changing preferences and decisions.
- Does not require a complicated scoring model.

**Problems**

- A newer inferred memory can incorrectly override an older explicit memory.
- A newer memory does not necessarily replace an older memory.
- Historical facts do not become false simply because a newer state exists.
- It can create false supersession relationships.

**Rejected as a universal rule.**

The D4 experiment with an older explicit FAISS memory and a newer inferred Qdrant memory showed why recency cannot automatically override provenance.

### Option C — Combine recency, provenance, and relationship into one score

The system could calculate a single score such as:

```text
recency + source authority + relationship strength
```

and select the highest-scoring memory.

**Advantages**

- Gives one ranking value to downstream retrieval.
- Can combine several useful signals.
- Looks straightforward to implement.

**Problems**

- The signals do not naturally have the same scale.
- In the experiment, the date ordinal was much larger than the provenance and relationship values.
- As a result, recency overwhelmed the other signals.
- The resulting ranking selected a newer inferred memory above an older explicit memory.

**Rejected.**

The experiment showed that blindly adding heterogeneous signals does not give us a defensible conflict-resolution rule.

### Option D — Hierarchical conflict resolution

Instead of reducing everything to one score, the system applies the signals in stages:

```text
candidate memories
        ↓
exclude superseded memories
        ↓
respect explicit supersession relationships
        ↓
compare evidence authority
        ↓
use recency when authority is comparable
        ↓
leave the state unresolved if evidence is still tied
```

**Advantages**

- Keeps lifecycle and relationship decisions separate from ranking.
- Explicit evidence can be stronger than inferred evidence.
- Recency can still resolve conflicts between comparable evidence.
- Does not require the system to invent a winner when evidence is genuinely ambiguous.
- Each step can be tested independently.

**Problems**

- More logic than similarity-only ranking.
- Requires provenance, lifecycle, temporal, and relationship fields to be reliable.
- Some memory types may need additional semantic rules later.
- The exact handling of unresolved conflicts and downstream context construction still needs validation.

**Selected.**

## Decision

I will use **hierarchical conflict resolution rather than a single combined ranking score**.

The current decision flow is:

```text
retrieved candidate memories
        ↓
remove memories marked superseded
        ↓
respect explicit supersession relationships
        ↓
compare provenance / evidence authority
        ↓
use recency when competing evidence has comparable authority
        ↓
if evidence remains equally strong, keep the conflict unresolved
```

### Lifecycle

A memory that is superseded will not be deleted.

It will remain stored as historical information and will be marked as superseded so that current-state retrieval can distinguish it from active memories.

For example:

```text
M1 → old state
M2 → new state

M1:
status = superseded
superseded_by = M2

M2:
status = active
```

### Explicit supersession

A memory should not be considered to supersede every older memory that happens to have the same subject and a different value.

The newer memory must identify the specific memory it supersedes.

For example:

```text
M5:
relationship = supersedes
supersedes = [3]
```

should produce:

```text
M3 → M5
```

but should not automatically create:

```text
M2 → M5
```

just because M2 and M5 concern the same subject.

This rule came directly from the D4 experiment where the initial broader rule incorrectly generated a false `M2 → M5` relationship.

### Evidence authority

For the current design:

```text
explicit user evidence > inferred evidence
```

An inferred memory can remain stored as evidence, but it must not automatically override a conflicting explicit user memory.

### Recency

Recency is a **tie-breaker rather than a universal override**.

When two conflicting memories have comparable evidential authority, the newer memory can be treated as the stronger current candidate.

For example:

```text
M2 → FAISS     explicit   Aug 1
M5 → Qdrant    explicit   Aug 12
```

results in M5 being ranked ahead of M2.

### Unresolved conflicts

If two memories have comparable authority and there is no stronger temporal or relationship signal, the system should not invent a winner.

For example:

```text
M6 → FAISS     explicit   Aug 10
M7 → Qdrant    explicit   Aug 10
```

with no supersession relationship should remain an unresolved conflict.

Both memories can remain stored as evidence rather than silently discarding one.

## Consequences and trade-offs

### Benefits

- The system no longer treats semantic similarity or recency as equivalent to current validity.
- Historical memories can remain available without automatically influencing current-state decisions.
- Explicit user statements have stronger authority than inferred memories.
- Supersession relationships are explicit instead of being inferred from weak signals.
- Recency is still useful when competing evidence has comparable authority.
- The system has a defined way to represent genuine ambiguity instead of forcing unsupported decisions.
- The approach fits the structured memory representation selected for D4.

### Costs and risks

- Conflict resolution is more complex than similarity-only retrieval.
- The system depends on correctly extracted provenance, temporal information, lifecycle state, and relationships.
- The hierarchy may not be sufficient for every memory type.
- Unresolved conflicts require downstream context and response logic that can handle ambiguity correctly.
- The current experiments are small controlled tests, not a production-scale evaluation.

## Validation plan

The decision will be validated using controlled cases derived from the D3 failure workload and the D4 conflict experiments.

At minimum, validation should demonstrate:

- a superseded memory remains stored but is excluded from current-state retrieval;
- an explicit supersession relationship only affects the memory it explicitly targets;
- an inferred memory does not automatically override a conflicting explicit memory;
- a newer explicit memory can outrank an older explicit memory when they represent competing current states;
- equally authoritative and equally recent conflicting memories are not silently resolved into an unsupported winner;
- historical information remains available when a query explicitly asks about past state;
- the conflict-resolution stage does not break user-scoped retrieval established in ADR-003.

The D4 prototype has already demonstrated the individual behaviors above in controlled experiments. The next implementation work will integrate these rules with the actual retrieval pipeline and then validate them against the fixed D3 workload.

## Revisit conditions

I will revisit this decision if testing shows that:

- hierarchical conflict resolution does not improve the D3 contradiction and changing-state failures;
- explicit-versus-inferred authority is not sufficient for real conflict cases;
- recency produces incorrect current-state decisions even when evidence has comparable authority;
- the system produces too many unresolved conflicts to be useful;
- the relationship extraction required for targeted supersession is too unreliable;
- the added lifecycle and conflict-resolution complexity does not provide enough improvement over the simpler baseline;
- different memory types require substantially different conflict rules that cannot be represented by this hierarchy.

The exact conflict-resolution rules may also need to be refined when the implementation is tested against the broader D4 evaluation workload.
