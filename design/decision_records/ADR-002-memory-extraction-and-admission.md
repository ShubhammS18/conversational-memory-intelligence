# ADR-002: Memory Extraction and Admission

- **Status:** Proposed
- **Date:** 2026-08-12

## Context

The naive baseline stores every extracted memory and relies on semantic similarity at retrieval time. D3 showed several problems with this approach: sensitive information could remain retrievable, old and new versions of the same state could both be retrieved, and the system had no way to distinguish useful durable information from information that should not become long-term memory.

The main design question is therefore not only how to extract information from a conversation, but also how to decide what should enter memory and how strong the evidence for that memory is.

I want extraction and admission to be separate responsibilities. Extraction identifies potentially meaningful information. Admission decides whether that information should be retained.

## Decision drivers

- Prevent sensitive or prohibited information from entering durable memory.
- Avoid turning temporary or uncertain statements into false long-term memories.
- Support both durable memories and explicitly scoped temporary information.
- Preserve whether a memory was explicitly stated by the user or inferred by the system.
- Allow a genuinely new preference or decision to supersede an older one.
- Prevent a weaker inferred memory from silently overriding a conflicting explicit user memory.
- Keep the policy understandable and observable enough to validate against the D3 failure cases.

## Options considered

### Option 1 — Store every extracted memory

This is close to the D3 baseline.

**Advantages**
- Simple to implement.
- Low risk of missing potentially useful information.

**Problems**
- Does not protect against sensitive information being stored.
- Stores temporary and low-value information.
- Does not distinguish uncertain statements from actual user decisions.
- Leaves conflict and supersession entirely to retrieval.

### Option 2 — Use extraction and admission as separate stages

First extract memory candidates, then evaluate each candidate for retention.

Possible admission outcomes:

- `DURABLE_STORE`
- `TEMPORARY_STORE`
- `DON'T_STORE`
- `REJECT`

**Advantages**
- Separates extraction failures from admission failures.
- Allows safety and retention rules to be applied before storage.
- Supports temporary scoped information without treating it as a permanent preference.
- Gives us explicit states that can be tested.

**Problems**
- Adds complexity compared with storing everything.
- Requires us to define and validate admission rules.

### Option 3 — Only store explicitly stated user information

This would make explicit user statements the only source of durable memory.

**Advantages**
- Conservative.
- Reduces the risk of inventing preferences from weak evidence.

**Problems**
- Throws away useful information that can be reliably inferred from repeated evidence.
- Does not by itself solve sensitive-memory admission, temporary information, or supersession.

## Decision

We will use **separate extraction and admission stages**.

The admission policy will use four outcomes:

1. **`DURABLE_STORE`** — information expected to remain useful beyond the immediate context and allowed to be retained.
2. **`TEMPORARY_STORE`** — information that matters beyond the immediate turn but is explicitly limited to a scope or short-lived context.
3. **`DON'T_STORE`** — information that is too temporary, uncertain, uncommitted, or otherwise not worth retaining as memory.
4. **`REJECT`** — information that should not be retained because it is unsafe or prohibited.

A candidate will be classified before admission. The initial durable memory types are:

- `preference`
- `fact`
- `decision`
- `constraint`

`temporary_state` can be identified during extraction, but it will normally not become a durable memory type.

### Provenance

Admitted memories must preserve provenance.

At minimum, the system distinguishes:

- `explicit_user`
- `inferred`

An explicit user statement has stronger evidential weight than a system inference for conflict resolution.

An inferred memory may be stored when there is sufficient supporting evidence, but it must remain marked as inferred. An inferred memory must not automatically supersede a conflicting explicit user memory.

### Uncertainty

Statements that only express consideration or uncertainty are not treated as confirmed preferences or decisions.

For example:

> "I'm considering switching from FAISS to Qdrant. I haven't decided yet."

should result in `DON'T_STORE`, rather than creating a Qdrant preference.

### Temporary scope

A statement explicitly limited to a temporary context can be retained as `TEMPORARY_STORE`.

For example:

> "For this experiment, let's use Qdrant."

must not automatically supersede a durable preference for FAISS.

The exact representation and expiry mechanism for temporary scope will be decided during the detailed design and validation.

### Supersession

When a genuinely new preference or decision replaces an existing one:

- the new memory becomes current;
- the old memory is retained as historical and marked superseded.

A merely possible or uncertain change does not trigger supersession.

## Consequences and trade-offs

### Positive

- The system has a write-side control point instead of relying only on retrieval.
- Sensitive information can be rejected before entering durable memory.
- Temporary information does not have to become permanent memory.
- Explicit and inferred evidence can be distinguished during later conflict resolution.
- Superseded memories can remain available as history without being treated as current.

### Negative

- Admission becomes more complex than the naive baseline.
- The system needs clear rules for deciding whether information is durable, temporary, uncertain, or unsafe.
- Inferred memories introduce a risk of false inference and therefore require stronger evidence and provenance tracking.
- Temporary memory requires scope and expiry semantics that are not fully specified by this ADR.

## Validation plan

This decision will be validated using the fixed D3 workload as a regression baseline and additional controlled cases for admission behavior.

At minimum, validation must demonstrate:

- sensitive information is rejected and is not retrievable through the memory system;
- temporary scoped information does not incorrectly supersede a durable preference;
- uncertain or uncommitted statements do not create confirmed preferences or decisions;
- explicit user memories are not silently overridden by weaker inferred memories;
- an explicit change can supersede the previous current memory;
- the system can distinguish extraction from admission failures in its observable output.

For deletion and forget behavior, the final consistency guarantee will be specified separately and validated as part of the system design.

## Revisit conditions

Revisit this decision if testing shows that:

- the four admission outcomes are insufficient to represent real workloads;
- inferred-memory errors are frequent enough that inferred memories should not be durably stored;
- temporary scoped memory provides insufficient value relative to its lifecycle complexity;
- the admission stage introduces unacceptable latency or cost;
- the policy fails to prevent sensitive information from entering retrievable memory;
- explicit-versus-inferred provenance is insufficient for resolving real conflicts;
- a simpler design achieves the same safety and correctness requirements.
