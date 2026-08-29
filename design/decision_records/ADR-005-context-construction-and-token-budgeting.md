# ADR-005 --- Context Construction and Token Budgeting

-   **Status:** Proposed
-   **Date:** 2026-08-16

## Context

D3 showed that the naive baseline retrieved the top-k memories and passed them into context without token-budget-aware selection. Case 3 showed that a larger memory pool can return multiple historical versions of the same topic, increasing context pollution and ambiguity. The D3 report identified context selection and token budgeting as a design response.

D4 therefore needs a context-construction step between memory retrieval/ranking and the final model prompt.

The D4 experiments tested fixed memory counts, current-first selection, query-specific ranking, token-budget selection, real tokenizer accounting, response-token reservation, oversized memories, weak short memories, and the limitations of greedy selection.

## Decision drivers

-   Respect a hard context budget.
-   Reserve explicit space for the model's response.
-   Use actual model-token accounting rather than whitespace-based
    estimates.
-   Preserve the upstream query-relevance ranking.
-   Avoid injecting a low-value memory merely because it is short.
-   Keep the selection algorithm simple, deterministic, and bounded.
-   Preserve memory metadata after selection.
-   Allow the selector to return no memory when no suitable candidate
    fits.
-   Avoid claiming globally optimal context packing without evidence.

## Options considered

### Option A --- Pass all retrieved memories into context

**Advantages**

-   Simplest implementation.
-   Preserves all retrieved information.
-   No context-selection logic required.

**Problems**

-   Repeats the historical/context-pollution problem identified in D3.
-   Context usage grows with the retrieved memory set.
-   Irrelevant or conflicting memories can consume context space.
-   Provides no hard token-budget guarantee.

**Rejected.**

### Option B --- Select a fixed number of memories

For example, `max_memories=2`.

**Advantages**

-   Very simple.
-   Easy to implement and test.

**Problems**

-   Memories have different token costs.
-   Two long memories can consume much more context than two short
    memories.
-   A memory-count limit is not a token-budget guarantee.
-   The chosen count is arbitrary.

**Rejected as the context-budget mechanism.**

### Option C --- Always prefer current memories

**Advantages**

-   Helps some current-state questions.
-   Simple policy.

**Problems**

-   Fails for historical questions.
-   A historical memory can be the most relevant evidence for a query
    about a past state.
-   Selection should depend on the query rather than one universal
    temporal preference.

**Rejected as a universal rule.**

### Option D --- Greedy query-ranked selection under a token budget

The selector ranks candidates by query relevance, reserves response capacity, and adds each memory only when it fits within the remaining memory budget.

**Advantages**

-   Provides a hard token-budget constraint.
-   Preserves upstream relevance ranking.
-   Simple and deterministic.
-   Easy to test and reason about.
-   Skips memories that cannot fit.
-   Can return an empty context when no suitable memory fits.
-   Preserves the selected memory objects and metadata.

**Problems**

-   Greedy selection is not globally optimal.
-   A lower-ranked combination can sometimes have greater total utility.
-   The policy does not explicitly optimize diversity or information
    coverage.

**Selected.**

## Decision

I will use **query-ranked greedy context selection under a hard token budget**.

The current flow is:

``` text
query
  ↓
retrieve relevant memories
  ↓
rank candidates by query relevance
  ↓
reserve response-token budget
  ↓
count candidate memory tokens
  ↓
add ranked memories while they fit
  ↓
skip candidates that exceed the remaining budget
  ↓
return selected memory objects
```

### Token accounting

The prototype uses a real tokenizer:

``` python
import tiktoken

tokenizer = tiktoken.get_encoding("cl100k_base")

def estimate_tokens(text):
    return len(tokenizer.encode(text))
```

This replaces the earlier experimental approximation:

``` python
len(text.split())
```

That statement records the D4 prototype decision. The later Genesis
implementation decision fixes `cl100k_base` as the exact tokenizer for
the first integrated reference implementation. This is tokenizer-only
use and does not introduce response generation or an external LLM call.

### Integrated-package serialization

The integrated M1 package serializes each complete memory exactly as:

```text
Memory <memory_id>:
<content>
```

The label is literal ASCII, the colon is followed by one LF, blocks are
separated by two LFs, and the final context has no leading or trailing
separator. Empty context is the empty string.

For each ranked candidate, the implementation constructs the entire
prospective serialized context and counts:

```python
len(tiktoken.get_encoding("cl100k_base").encode(prospective_context))
```

This count includes labels, separators, formatting, and cross-boundary
tokenization. A memory is included only as a complete block. If it does
not fit, it receives `budget_exceeded` and later smaller candidates may
still be considered. Partial truncation is forbidden.

These integrated-package details supplement, rather than rewrite, the
earlier D4 prototype evidence. Their binding record is
`.genesis/decisions/M1-implementation-bindings.md`.

### Response reservation

The selector does not treat the entire context window as available for
memories.

``` text
total context budget
        -
reserved response tokens
        =
memory budget
```

Only the resulting memory budget is available to the selector.

### Selection rule

Candidates are processed in descending relevance-score order.

A memory is selected only when:

``` text
tokens already selected
+
candidate memory tokens
<=
available memory budget
```

If it does not fit, the selector skips it and continues evaluating the remaining candidates.

### Empty-context behavior

If no candidate satisfies the budget constraint, the selector may return an empty context.

The system should not fill unused context space with a weak or unrelated memory simply because it is short enough to fit.

### Metadata preservation

The selector returns full memory objects rather than only text, preserving fields such as:

``` text
memory_id
content
score
```

### Known limitation

The selected policy is a **greedy heuristic**, not an optimal context-packing algorithm.

The D4 experiments demonstrated that a high-scoring memory can consume enough budget to prevent a combination of lower-scoring memories from being selected, even when that combination has greater combined score.

I am accepting this limitation because a general combinatorial optimizer would add substantial complexity without evidence that the additional optimization is necessary for this project's intended workload.

This is an explicit engineering trade-off, not a claim that greedy selection is mathematically optimal.

## Consequences and trade-offs

### Benefits

-   Context construction has an explicit and testable token-budget
    boundary.
-   The model receives query-relevant memories rather than the entire
    retrieval result.
-   Response capacity is protected.
-   Token accounting uses a real tokenizer in the prototype.
-   Large memories cannot silently exceed the configured budget.
-   Weak memories are not selected merely because they are short.
-   Historical memories can still be selected when the query makes them
    relevant.
-   Selected memory identity and ranking information remain available
    downstream.
-   The implementation remains small enough to understand and test
    independently.

### Costs and risks

-   Greedy selection can be suboptimal.
-   The configured memory/response budget needs validation for the final
    model and workload.
-   `cl100k_base` was a prototype choice in D4 and is now the explicit
    tokenizer contract for the first integrated reference implementation.
-   Actual prompt token usage also includes system instructions, the
    query, formatting, and other context.
-   A strict budget can result in no memory being selected when a useful
    memory is too large to fit.
-   The current policy does not explicitly optimize diversity or
    redundancy.

## Validation plan

At minimum, validation should demonstrate:

-   selected memories never exceed the configured memory-token budget;
-   reserved response tokens remain available;
-   higher-ranked memories are considered before lower-ranked memories;
-   memories that cannot fit are skipped rather than exceeding the
    budget;
-   irrelevant low-ranked memories are not selected merely because they
    are short;
-   historical memories can be selected for historical queries;
-   current memories can be selected for current-state queries;
-   the selector can return an empty context when no suitable memory
    fits;
-   selected memory metadata remains intact;
-   context selection does not violate user-scoped retrieval established
    by ADR-003;
-   superseded memories follow the ranking/conflict-resolution policy
    established by ADR-004.

The D4 prototype has demonstrated these individual behaviors in controlled experiments. These are component-level results, not production-scale performance claims.

## Revisit conditions

I will revisit this decision if testing shows that:

-   greedy selection causes unacceptable loss of useful context;
-   a better bounded heuristic provides significant quality improvement
    at acceptable complexity;
-   measured token usage shows the budgeting policy is insufficient for
    the target model;
-   the selected tokenizer does not match the final model;
-   context selection introduces unacceptable latency or computational
    cost;
-   the system frequently returns empty context when useful memories
    could reasonably have been included;
-   memory diversity or redundancy requires an explicit selection
    objective;
-   a more complex optimization approach becomes justified by measured
    workload requirements.
