# D4 Phase 10 — Engineering Reality Gap Analysis

## Purpose

I am using this phase to check how the D4 design holds up when I move from component-level design and tests toward something that would actually have to run.

I am not treating this as a production implementation phase. The goal here is to identify what has already been decided, what has some evidence behind it, and what is still an engineering question.


## Current engineering-reality picture

| Area | What I already have | Current evidence | Status for Phase 10 |
|---|---|---|---|
| Scale / capacity | Shared FAISS index; authorized-ID filtering; one-index-per-user rejected as the default | Only small controlled experiments | **Open** |
| Latency | D3 has six single-run retrieval timings; no production benchmark | Baseline reference only | **Open** |
| Cost | Local FAISS and embedding-based design are established, but no cost model was measured | No D3 cost measurement | **Open** |
| Failure recovery | FAISS cleanup failure was tested; logical exclusion protects retrieval while physical cleanup can fail | Component-level PASS | **Partially covered** |
| API contracts | Retrieval/deletion operations exist conceptually in the design, but no complete API contract has been defined | No integrated API implementation | **Open** |
| Data/schema migration | Memory schema has been designed, but migration/versioning for schema or embedding changes is not defined | No migration experiment | **Open** |
| Index migration | FAISS `memory_id` mapping is established; future index replacement is not specified | Design-level only | **Open** |
| Observability | Minimal privacy audit events are defined (`ADMIT`, `REJECT`, `FORGET`); experiments record validation results | No persistent operational observability system | **Partially covered** |
| Evaluation | D4 component-level results and Phase 9 comparison artifacts exist | Component-level evidence; integrated evaluation remains limited | **Covered with limitation** |
| Acceptance criteria | Several hard invariants and focused tests exist | Tested at component level | **Partially covered** |
| Operational availability | No availability target or failure-domain design has been established | No evidence | **Open** |

## What is already decided

### Shared FAISS index

I am currently using a shared FAISS index with explicit memory IDs and user-scoped authorized-ID filtering.

I rejected a separate FAISS index for every user as the default because the number of indexes would grow with the number of users and create additional operational overhead.

This decision is documented in ADR-003. It is still a design decision rather than a proof that the approach will scale indefinitely.

### Logical exclusion before physical FAISS cleanup

For forgetting, the memory is first made unavailable to retrieval and then removed from FAISS.

If physical FAISS deletion fails, the memory must remain logically excluded.

This was tested successfully, but the experiments explicitly do not establish a production-grade transaction between authoritative memory state and FAISS.

### Lifecycle model

Supersession, expiration, historical eligibility, and forgetting have separate meanings.

Superseded memories can remain historical. Forgetting is a separate operation.

Consolidation and reflection were deliberately left deferred rather than being added without evidence.

### Context budget

Context construction uses query-ranked greedy selection under a hard token budget with response-token reservation.

The token accounting itself has been tested, but there is not yet an end-to-end production pipeline around it.

## What is still open

### 1. Scale

I have not established:

- expected users;
- memories per user;
- memory growth per month;
- total vector count;
- acceptable index size;
- when the shared FAISS + authorized-ID approach becomes too expensive or slow.

The existing experiments are too small to answer these questions.

I therefore should not claim that the current FAISS design is production-scale.

### 2. Latency

The D3 baseline provides a reference point, but each case had only one recorded run and the report explicitly says those values are not production p50/p95 benchmarks.

D4 has also not produced a complete latency budget covering:

- extraction/admission;
- embedding;
- authorization/scope lookup;
- vector retrieval;
- conflict/lifecycle filtering;
- context construction;
- final model generation.

A real latency budget is therefore still open.

### 3. Cost

No D3 monetary-cost measurement exists.

The current design also has several operations whose cost needs to be understood before making a production claim:

- embedding on memory admission;
- embedding queries;
- vector search;
- storage of embeddings and metadata;
- possible index maintenance/rebuilds;
- model inference for extraction or reasoning.

I should not invent a cost number without defining an operating assumption first.

### 4. Failure recovery

We have one useful recovery rule already:

> If FAISS physical deletion fails, logical exclusion must still prevent retrieval.

What is missing is the broader recovery story.

For example:

- What is the authoritative source of truth?
- How is an index repaired if it becomes inconsistent?
- How is a missing vector detected?
- How is a stale vector detected?
- When should an index be rebuilt?
- What happens after a process crash during a write/delete operation?

These are engineering questions that the current component tests do not answer.

### 5. API contracts

The design has conceptual operations such as retrieval and forgetting, but I have not defined a complete external contract.

Still open:

- request/response shape;
- required `user_id` handling;
- error semantics;
- idempotency behavior exposed through the API;
- what happens when memory retrieval returns nothing;
- synchronous vs background operations.


### 6. Data and index migrations

The memory representation has been designed, but I have not defined what happens when:

- the memory schema changes;
- a field becomes required;
- the embedding model changes;
- embedding dimensionality changes;
- FAISS index configuration changes;
- the project moves away from FAISS.

This is a real engineering gap because the vector representation is tied to the embedding model and index configuration.

### 7. Observability

There is a minimal audit model for privacy-sensitive operations:

```text
ADMIT
REJECT
FORGET
```

with information such as memory ID, user ID, operation, result, and time.

The experiments also produce evidence useful for debugging.

But this is not yet an operational observability system. I have not defined the full set of metrics/logs needed to answer questions such as:

- why was a memory retrieved?
- why was a memory rejected?
- how often does retrieval return no memory?
- how often do conflicts remain unresolved?
- how often does FAISS cleanup fail?
- how much memory is stored per user?
- where is latency being spent?

## Important boundary

I am not going to solve all of these gaps by immediately adding production infrastructure.

Phase 10 is first about making the engineering assumptions explicit.

For each open area, I need to decide whether the right next action is:

```text
measure
→ decide
→ prototype
→ defer
```

rather than automatically implementing a larger system.

## Current conclusion

The D4 design is reasonably well-defined at the memory-decision level, but several operational questions are still open.

The biggest gaps are:

1. scale/capacity assumptions;
2. a real latency budget;
3. cost assumptions;
4. recovery and source-of-truth behavior;
5. API contracts;
6. schema/embedding/index migration;
7. operational observability.

The existing experiments give useful evidence for some of these areas, especially retrieval isolation, deletion failure handling, lifecycle behavior, and token budgeting. They do not establish production-scale behavior.

That is the starting point for the rest of this phase.
