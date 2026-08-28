# Wiki Index — conversational-memory-intelligence

The project knowledge base. Same schema as the agentic-swe-kit wiki: concept pages in `concepts/`,
each with frontmatter and ≥2 `[[wikilinks]]`. The L3 RESEARCH loop writes here; G0 reads here first.

> **Read this file before any milestone (G0 step 1).** Pick candidate pages by name-matching the
> milestone's nouns, then drill in. The wiki is what prevents rebuilding work that already exists.

## Entities (the things this system has)

- **Memory Service** — synchronous application coordinator exposing first-slice `admit` and `retrieve` operations; see [API contracts](../../design/api_contracts.md) and [API boundaries](../../experiments/d4_engineering_reality/api_boundaries.md).
- **SQLite Memory Store** — authoritative durable store for typed memory records, embeddings, indexing state, idempotency, lifecycle, and migration metadata; see [memory data model](../../design/data_model.md), [migration analysis](../../experiments/d4_engineering_reality/migration_analysis.md), and the approved G0.5 approach.
- **FAISS Derived Index** — persisted but rebuildable vector index derived from SQLite using stable memory IDs; see [ADR-003](../../design/decision_records/ADR-003-user-scoped-retrieval.md), [ADR-007](../../design/decision_records/ADR-007-privacy-isolation-and-forgetting.md), and [recovery analysis](../../experiments/d4_engineering_reality/recovery_analysis.md).
- **Trusted Request Context** — caller-established identity and request metadata used as the mandatory scope for every memory operation; see [ADR-003](../../design/decision_records/ADR-003-user-scoped-retrieval.md), [ADR-007](../../design/decision_records/ADR-007-privacy-isolation-and-forgetting.md), and [threat model](../../design/threat_model.md).

## Concepts (how it works)

- **Admission and Indexing State Machine** — `validate → admit → embed → SQLite pending → FAISS add/save → SQLite indexed`; pending or failed records are non-retrievable.
- **Lifecycle-Aware Scoped Retrieval** — authorize IDs before vector search, then apply current-versus-historical lifecycle eligibility; see [ADR-003](../../design/decision_records/ADR-003-user-scoped-retrieval.md) and [ADR-006](../../design/decision_records/ADR-006-memory-lifecycle.md).
- **Forgetting Without Reactivation** — accepted forget requests immediately exclude memory from every retrieval path, remain idempotent, and never reactivate older superseded state; see [ADR-007](../../design/decision_records/ADR-007-privacy-isolation-and-forgetting.md).
- **Startup Recovery and Index Reconciliation** — verify SQLite, embedding metadata, and memory/vector mappings; rebuild derived FAISS state and retry pending records when safe; see [recovery analysis](../../experiments/d4_engineering_reality/recovery_analysis.md).
- **Token-Bounded Context Construction** — select complete serialized memories in approved rank order without exceeding the caller-provided memory allowance; see [ADR-005](../../design/decision_records/ADR-005-context-construction-and-token-budgeting.md).
- **Fixed-Workload Evaluation** — preserve and rerun the six baseline failure cases against the integrated pipeline; see [baseline protocol](../../experiments/baseline_protocol.md) and [sprint plan](../../design/sprint_plan.md).

## Sources (research distilled by L3)
<!-- - [[concepts/<source-slug>]] — one-line summary | filed <date> -->

## Seeded from agentic-swe-kit

Relevant global concept pages for this project's Backend/API implementation, completed memory architecture, and post-integration evaluation:

- `$AGENTIC_SWE_WIKI_ROOT/clean-architecture/concepts/Dependency-Rule.md` — enforce inward dependencies across domain, application, adapters, composition, entry points, and tests.
- `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Transactions-and-Isolation.md` — define SQLite transaction, concurrency, retry, and idempotency behavior.
- `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Encoding-and-Schema-Evolution.md` — guide versioned schema migrations and persisted compatibility metadata.
- `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Derived-Data-Systems.md` — preserve SQLite as system of record and FAISS as rebuildable derived data.
- `$AGENTIC_SWE_WIKI_ROOT/security-engineering/concepts/Access-Control.md` — enforce caller-established identity, mandatory user scope, and pre-search authorization.
- `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Recovery-Patterns.md` — design pending-state recovery, reconciliation, safe degradation, and fail-closed startup.
- `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Transparency-and-Observability.md` — add privacy-safe events, readiness state, and diagnostic measurements.
- `$AGENTIC_SWE_WIKI_ROOT/llmops-ai-agents/concepts/Evaluation-Frameworks.md` — structure repeatable component, integration, and fixed-workload evaluation.
- `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Tracer-Bullets.md` — build and verify one real vertical slice at a time.
- `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Ruthless-Testing.md` — preserve prototype behavior through automated boundary, failure-state, and regression testing.
