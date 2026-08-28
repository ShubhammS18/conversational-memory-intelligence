# PLAN — conversational-memory-intelligence

The machine-parseable implementation plan. Mirrors the milestone table in `DONE.html` (DONE.html is the
human/visual view; this is the one loops read). Sliced so each milestone ships in one L1 BUILD pass.

> Slicing rule: a milestone must have (a) a single clear outcome, (b) an exact **demo command** that
> proves it, and (c) a freeze boundary of files it may touch. If you can't write the demo command,
> the milestone is too vague — split it.

---

## Brainstorm (G0.5 — completed before slicing milestones)

> Three fundamentally different approaches to the cognitive job. The selected approach is the approved
> implementation architecture; the alternatives remain recorded so the trade-off is explicit.

### Approach A — Direct Integrated Script

Build one synchronous Python service with concrete SQLite, FAISS, SentenceTransformer, and token-counting dependencies wired directly into the workflow. Keep admission, retrieval, ranking, lifecycle, and context logic in a small number of implementation modules without formal ports.

- Strengths:
  - Provides the shortest path from the approved prototypes to a runnable integrated pipeline.
  - Minimizes initial interface and composition code.
- Weaknesses:
  - Couples memory behavior to SQLite, FAISS, SentenceTransformers, and entry-point concerns.
  - Makes failure injection, adapter replacement, and isolated domain testing harder.

### Approach B — Local Modular Monolith with Ports and Adapters

Build one synchronous Python deployment organized into domain, application, infrastructure, composition, and entry-point areas. The application service coordinates the workflow through small interfaces, deterministic rules remain framework-independent, and concrete SQLite, FAISS, SentenceTransformer, and `tiktoken` adapters are connected at one composition point.

- Strengths:
  - Preserves explicit dependency boundaries and keeps deterministic memory policy independent of storage and model tools.
  - Supports unit, adapter, restart, recovery, and end-to-end testing without introducing distributed deployment.
- Weaknesses:
  - Requires more initial interface, mapping, composition, and architecture-test work than a direct pipeline.
  - SQLite/FAISS consistency, indexing state, locking, and recovery still require explicit application-level coordination.

### Approach C — Service-Separated or Event-Driven System

Admission, indexing, retrieval, and recovery would be separated into independently operated services or workers, potentially connected through network calls or durable queues.

- Strengths:
  - Components could be isolated and scaled independently.
  - Indexing and recovery could move to durable background workers.
- Weaknesses:
  - Introduces networking, queues, distributed consistency, deployment, and operational complexity unsupported by current requirements.
  - Makes local validation slower and expands the project beyond its approved evidence and scope.

### Chosen: Local Modular Monolith with Ports and Adapters

The modular-monolith approach was selected because it preserves the approved domain and infrastructure boundaries, supports deterministic safety and recovery testing, and keeps all components in one synchronous local deployment without introducing unnecessary service or distributed-system complexity.

---

## Milestones

All test and demonstration commands in this section are planned commands. They become runnable only after the named production and test files exist. Existing prototype artifacts are characterization evidence and remain unchanged.

### M1 — Persisted End-to-End Memory Slice

- **Observable outcome:** One real memory is admitted, embedded, committed to SQLite as pending, indexed and durably saved in FAISS, marked indexed, reloaded after restart, retrieved only for its owner, and serialized into an exact token-bounded context.
- **Included behavior:** Installable `src`-layout packaging; synchronous `admit(context, request)` and `retrieve(context, request)` operations; trusted `RequestContext`; Pydantic boundary models; frozen domain records; deterministic initial admission; actual `all-mpnet-base-v2` embeddings; SQLite records and float32 embedding BLOBs; pending/indexed/failed indexing state; stable memory/vector IDs; persisted FAISS; idempotency by `(user_id, idempotency_key)`; owner-scoped retrieval; deterministic ranking tie-breakers; complete-memory context selection using `tiktoken` `cl100k_base`; normal restart; safe partial-indexing failure.
- **Packaging and dependency authority:** `pyproject.toml` is the installable package configuration for `src/conversational_memory/` and is authoritative for the new integrated `conversational_memory` package and its runtime and test dependencies. The existing `requirements.txt` is preserved unchanged as legacy/prototype environment history unless a later reviewed migration explicitly replaces or regenerates it. It is not authoritative for the new integrated package and must not be used to override `pyproject.toml`.
- **Exclusions:** Full current-state lifecycle filtering, no-relevant-memory threshold behavior, automatic supersession, historical retrieval, expiration, forgetting, full reconciliation, structured event coverage, fixed-workload evaluation, response generation, and all other approved scope exclusions.
- **Intended files / freeze boundary:** `pyproject.toml`; `src/conversational_memory/domain/**`; `src/conversational_memory/application/**`; `src/conversational_memory/infrastructure/**`; `src/conversational_memory/infrastructure/sqlite/migrations/**`; `src/conversational_memory/composition/**`; `src/conversational_memory/entrypoints/**`; `tests/architecture/**`; `tests/unit/**`; `tests/adapter/**`; `tests/integration/test_admission_indexing.py`; `tests/integration/test_first_slice.py`; `tests/integration/test_idempotent_admission.py`; `tests/integration/test_partial_index_failure.py`; `tests/integration/test_restart_persistence.py`; `tests/regression/test_context_budget.py`. The existing `requirements.txt` is outside the M1 write boundary and is preserved as legacy/prototype history.
- **Existing characterization evidence:** `experiments/d4_deletion/test_sensitive_admission.py`; `experiments/d4_scoped_retrieval/test_scoped_faiss.py`; `experiments/d4_context_construction/test_context_selection.py`; `experiments/d4_engineering_reality/api_boundaries.md`; `experiments/d4_engineering_reality/migration_analysis.md`.
- **Proposed test evidence:** Packaging and architecture tests, domain/application validation tests, SQLite and FAISS adapter tests, and real-component admission, restart, isolation, idempotency, failure-state, and token-budget integration tests. SQLite, FAISS, SentenceTransformers, and `cl100k_base` must not be mocked in the first-slice integration proof.
- **Setup command (future):** `python -m pip install -e .`
- **Packaging smoke command (future):** `python -m conversational_memory.entrypoints.cli --help`
- **Verification command (future):** `python -m pytest tests/architecture tests/unit tests/adapter tests/integration/test_admission_indexing.py tests/integration/test_first_slice.py tests/integration/test_idempotent_admission.py tests/integration/test_partial_index_failure.py tests/integration/test_restart_persistence.py tests/regression/test_context_budget.py -q`
- **Demo command (future):** `python -m conversational_memory.entrypoints.cli demo-first-slice`
- **Binary success conditions:**
  - `python -m pip install -e .` completes successfully from the repository root.
  - After editable installation, `python -m conversational_memory.entrypoints.cli --help` resolves successfully from the repository root and exits with status zero.
  - `pyproject.toml` is authoritative for the integrated package's runtime and test dependencies; the preserved legacy `requirements.txt` does not override it.
  - The integration test uses a real on-disk SQLite database, persisted FAISS index, real `all-mpnet-base-v2`, and exact `cl100k_base`.
  - Successful admission returns one stable memory ID with `indexing_state: indexed` and `retrievable: true`.
  - Restart preserves the SQLite record, FAISS mapping, model metadata, dimension, and owner retrieval.
  - Another user retrieves none of the memory even when using similar text.
  - Retrying the same idempotency key creates no additional row or vector.
  - An indexing/save failure leaves the SQLite record pending or failed and non-retrievable.
  - The serialized context contains complete memories and its reported token count does not exceed the supplied allowance.
- **Dependencies:** Approved G0, G0.5, G2 graph, G3 references, and G4 gates; no implementation milestone dependency.
- **Applicable G4 gates:** Phase 0 prerequisite evidence; Phase 1 architecture; Phase 3 backend/API; Phase 6 memory architecture; incremental Phase 9 characterization; Phase 12 idempotency, failure state, and restart; cross-phase verification.
- **Loops:** L1, L4.
- **Skills / wiki pointers:** `genesis`; `agentic-swe-master`; `modular-architecture`; `data-systems-engineering`; `security-engineering`; `$AGENTIC_SWE_WIKI_ROOT/clean-architecture/concepts/Dependency-Rule.md`; `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Transactions-and-Isolation.md`; `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Encoding-and-Schema-Evolution.md`; `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Derived-Data-Systems.md`; `$AGENTIC_SWE_WIKI_ROOT/security-engineering/concepts/Access-Control.md`; `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Tracer-Bullets.md`; `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Ruthless-Testing.md`.
- **Main risk:** SQLite may commit while FAISS insertion or durable saving fails, producing divergent state. The approved pending/indexed state machine, non-retrievability rule, and real failure-path tests are the evidence-backed controls.
- **Token budget:** 50000

### M2 — Current-State Filtering

- **Observable outcome:** Current-state retrieval returns only memories that are owned by the caller, active, indexed, and valid at the trusted current time.
- **Included behavior:** Deterministic ownership, indexing-state, deletion-state, lifecycle, and validity filtering before ranking; policy precedence over similarity; seeded-state coverage for pending, failed, expired, superseded, and deleted records.
- **Exclusions:** Automatic supersession, historical retrieval, automatic expiration transition, forgetting operations, reconciliation, full event coverage, and fixed-workload evaluation.
- **Intended files / freeze boundary:** `src/conversational_memory/domain/**`; `src/conversational_memory/application/**`; `src/conversational_memory/infrastructure/sqlite/**`; `src/conversational_memory/entrypoints/**`; `tests/unit/domain/**`; `tests/integration/test_current_state_filtering.py`; `tests/regression/test_lifecycle.py`.
- **Existing characterization evidence:** `experiments/d4_scoped_retrieval/test_scoped_faiss.py`; `experiments/d4_conflict_resolution/test_lifecycle_query_intent.py`; `experiments/d4_conflict_resolution/test_expiration.py`; `experiments/d4_deletion/test_forgetting.py`.
- **Proposed test evidence:** Domain eligibility truth-table tests plus integrated retrieval tests seeded with every ineligible state.
- **Verification command (future):** `python -m pytest tests/unit/domain tests/integration/test_current_state_filtering.py tests/regression/test_lifecycle.py -q`
- **Demo command (future):** `python -m conversational_memory.entrypoints.cli demo-current-state`
- **Binary success conditions:**
  - An owned active, indexed, currently valid memory can be selected.
  - Deleted, pending, failed, expired, and superseded memories are absent from current-state results.
  - An unauthorized memory is absent regardless of its similarity score.
  - No probabilistic score can override an eligibility rejection.
- **Dependencies:** M1.
- **Applicable G4 gates:** Phase 1 dependency direction; Phase 3 retrieval workflow; Phase 6 deterministic policy precedence and lifecycle eligibility; Phase 9 characterization; cross-phase verification.
- **Loops:** L1, L4.
- **Skills / wiki pointers:** `agentic-swe-master`; `security-engineering`; `llmops-ai-agents`; `$AGENTIC_SWE_WIKI_ROOT/security-engineering/concepts/Access-Control.md`; `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Ruthless-Testing.md`.
- **Main risk:** Lifecycle filtering could occur after vector selection and allow an ineligible candidate to influence output. Tests must prove deterministic exclusion before final ranking and context construction.
- **Token budget:** 50000

### M3 — Explicit No-Relevant-Memory Result

- **Observable outcome:** An unrelated query returns a successful empty result and context with `no_relevant_memory` instead of forcing the nearest vector match.
- **Included behavior:** An explicitly configured relevance threshold; empty selected-memory list; empty context; structured exclusion and no-memory reason; normal success semantics; an explicit M3 configuration decision selecting and documenting the threshold.
- **Threshold decision:** No numeric default relevance threshold is currently approved. M3 must select the initial configured value using the existing D4 no-memory evidence and new boundary tests. The selected value, comparison semantics, and rationale must be recorded in approved configuration or a decision record before M3 can be marked complete. G5 does not assign a numeric value.
- **Exclusions:** An undocumented or hard-coded threshold default; threshold selection based only on intuition; learned thresholding or reranking; supersession; historical retrieval; expiration transitions; forgetting; recovery; observability rollout; and full fixed-workload scoring.
- **Intended files / freeze boundary:** `src/conversational_memory/domain/**`; `src/conversational_memory/application/**`; `src/conversational_memory/composition/**`; `src/conversational_memory/entrypoints/**`; `tests/unit/domain/**`; `tests/integration/test_no_memory.py`; `tests/integration/test_retrieval_metadata.py`; `tests/regression/test_no_memory.py`; and the approved configuration or decision artifact that records the selected threshold and rationale.
- **Existing characterization evidence:** `experiments/d4_evaluation/test_no_memory.py`; `experiments/naive_baseline/workload/case6_cold_start.json`; `experiments/baseline_protocol.md`.
- **Proposed test evidence:** Threshold-selection evidence based on the D4 cold-start scenario; integrated unrelated-query regression coverage; and explicit boundary tests:
  - A score immediately below the configured threshold is excluded.
  - A score equal to the configured threshold follows the comparison semantics recorded by the M3 configuration decision.
  - A score immediately above the configured threshold is eligible, subject to all deterministic ownership and lifecycle rules.
- **Verification command (future):** `python -m pytest tests/unit/domain tests/integration/test_no_memory.py tests/integration/test_retrieval_metadata.py tests/regression/test_no_memory.py -q`
- **Demo command (future):** `python -m conversational_memory.entrypoints.cli demo-no-memory`
- **Binary success conditions:**
  - The selected numeric threshold, its comparison semantics, supporting D4 evidence, and rationale are present in approved configuration or a decision record.
  - No undocumented numeric default or fallback threshold exists.
  - Boundary tests immediately below, equal to, and above the selected threshold pass according to the recorded comparison semantics.
  - An unrelated query produces no selected memories.
  - The context is empty and within budget.
  - The result is successful and contains the `no_relevant_memory` reason.
  - The nearest candidate is not forced into context below the configured threshold.
- **Dependencies:** M2.
- **Applicable G4 gates:** Phase 1 dependency direction; Phase 3 typed retrieval result and validated configuration; Phase 6 explicit no-memory metadata; Phase 9 characterization; cross-phase verification.
- **Loops:** L1, L4.
- **Skills / wiki pointers:** `agentic-swe-master`; `llmops-ai-agents`; `$AGENTIC_SWE_WIKI_ROOT/llmops-ai-agents/concepts/Evaluation-Frameworks.md`; `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Ruthless-Testing.md`.
- **Main risk:** An unsupported threshold could hide useful memory or admit unrelated memory. M3 therefore cannot complete until its value and comparison semantics are evidence-backed, documented, and covered on both sides of the boundary.
- **Token budget:** 50000

### M4 — Supersession and Conflict Handling

- **Observable outcome:** A clear newer correction supersedes the specific older memory, and only the new memory influences current-state retrieval.
- **Included behavior:** Explicit supersession relationship; newer-first ranking; explicit-over-inferred authority; stable-ID final tie-breaker; uncertain conflict remains unsuperseded or temporary rather than silently overwriting state.
- **Exclusions:** Historical retrieval, automatic expiration, forgetting, recovery, observability rollout, and fixed-workload completion.
- **Intended files / freeze boundary:** `src/conversational_memory/domain/**`; `src/conversational_memory/application/**`; `src/conversational_memory/infrastructure/sqlite/**`; `src/conversational_memory/infrastructure/sqlite/migrations/**`; `src/conversational_memory/entrypoints/**`; `tests/unit/domain/test_supersession.py`; `tests/integration/test_supersession.py`; `tests/regression/test_lifecycle.py`.
- **Existing characterization evidence:** `experiments/d4_conflict_resolution/test_supersession.py`; `experiments/naive_baseline/workload/case1_irrelevant_contradictory.json`; `experiments/naive_baseline/workload/case2_preference_change.json`.
- **Proposed test evidence:** Deterministic conflict/ranking unit tests and an integrated explicit-correction regression test.
- **Verification command (future):** `python -m pytest tests/unit/domain/test_supersession.py tests/integration/test_supersession.py tests/regression/test_lifecycle.py -q`
- **Demo command (future):** `python -m conversational_memory.entrypoints.cli demo-supersession`
- **Binary success conditions:**
  - A clear explicit correction records the exact bidirectional supersession relationship.
  - Current-state retrieval returns the newer current value and excludes the older superseded value.
  - An uncertain relationship does not silently supersede either durable memory.
  - Equal-score ordering follows recency, then authority, then stable identifier.
- **Dependencies:** M3.
- **Applicable G4 gates:** Phase 1 dependency direction; Phase 3 admission workflow; Phase 6 supersession and ranking rules; Phase 9 characterization; cross-phase verification.
- **Loops:** L1, L4.
- **Skills / wiki pointers:** `agentic-swe-master`; `engineering-mindset`; `llmops-ai-agents`; `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Ruthless-Testing.md`.
- **Main risk:** An ambiguous statement could silently replace valid current state. The approved fail-closed uncertainty rule and explicit relationship tests prevent that mutation.
- **Token budget:** 50000

### M5 — Historical Retrieval

- **Observable outcome:** An explicitly historical query can retrieve an authorized superseded memory without changing current state.
- **Included behavior:** Explicit historical intent in `RetrievalRequest`; authorized superseded-memory eligibility; lifecycle metadata; continued exclusion of deleted memories; no lifecycle mutation during retrieval.
- **Exclusions:** Automatic expiration, forgetting operations, recovery, observability rollout, and fixed-workload completion.
- **Intended files / freeze boundary:** `src/conversational_memory/domain/**`; `src/conversational_memory/application/**`; `src/conversational_memory/entrypoints/**`; `tests/unit/domain/**`; `tests/integration/test_historical_retrieval.py`; `tests/regression/test_lifecycle.py`.
- **Existing characterization evidence:** `experiments/d4_conflict_resolution/test_lifecycle_query_intent.py`; `design/api_contracts.md`.
- **Proposed test evidence:** Separate current-versus-historical eligibility tests and an integrated historical CLI scenario.
- **Verification command (future):** `python -m pytest tests/unit/domain tests/integration/test_historical_retrieval.py tests/regression/test_lifecycle.py -q`
- **Demo command (future):** `python -m conversational_memory.entrypoints.cli demo-history`
- **Binary success conditions:**
  - A historical request can select the owned superseded value.
  - The same value remains excluded from current-state retrieval.
  - Deleted memories remain excluded from historical retrieval.
  - Retrieval does not reactivate or otherwise mutate the superseded record.
- **Dependencies:** M4.
- **Applicable G4 gates:** Phase 1 dependency direction; Phase 3 typed retrieval intent; Phase 6 historical lifecycle behavior; Phase 9 characterization; cross-phase verification.
- **Loops:** L1, L4.
- **Skills / wiki pointers:** `agentic-swe-master`; `llmops-ai-agents`; `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Ruthless-Testing.md`.
- **Main risk:** Historical eligibility could leak into ordinary retrieval. Independent current and historical demonstrations must prove the two paths remain distinct.
- **Token budget:** 50000

### M6 — Trusted-Clock Expiration

- **Observable outcome:** A memory stops appearing in current-state retrieval when its explicit `valid_until` is reached according to the trusted UTC clock.
- **Included behavior:** Timezone-aware trusted clock; validation of explicitly supplied validity dates; expiration decisions based only on trusted current time; caller timestamps retained solely as source-event or provenance information.
- **Exclusions:** Forgetting, recovery, observability rollout, inferred dates, and fixed-workload completion.
- **Intended files / freeze boundary:** `src/conversational_memory/domain/**`; `src/conversational_memory/application/**`; `src/conversational_memory/entrypoints/**`; `tests/unit/domain/test_expiration.py`; `tests/integration/test_expiration.py`; `tests/regression/test_lifecycle.py`.
- **Existing characterization evidence:** `experiments/d4_conflict_resolution/test_expiration.py`; `design/data_model.md`.
- **Proposed test evidence:** Frozen-clock boundary tests before, at, and after `valid_until`, plus caller-timestamp adversarial cases.
- **Verification command (future):** `python -m pytest tests/unit/domain/test_expiration.py tests/integration/test_expiration.py tests/regression/test_lifecycle.py -q`
- **Demo command (future):** `python -m conversational_memory.entrypoints.cli demo-expiration`
- **Binary success conditions:**
  - The memory is current before its trusted-clock expiry boundary.
  - It is excluded at and after the approved expiry boundary.
  - A caller-supplied event timestamp cannot control creation time, default validity, or expiration.
  - Historical behavior remains governed by explicit query intent and lifecycle policy.
- **Dependencies:** M5.
- **Applicable G4 gates:** Phase 1 dependency direction; Phase 3 trusted input boundary; Phase 6 freshness and lifecycle rules; Phase 9 characterization; cross-phase verification.
- **Loops:** L1, L4.
- **Skills / wiki pointers:** `agentic-swe-master`; `data-systems-engineering`; `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Ruthless-Testing.md`.
- **Main risk:** Mixing provenance timestamps with trusted lifecycle time could prematurely retain or expire data. Clock-controlled tests must prove the separation.
- **Token budget:** 50000

### M7 — Global, Idempotent Forgetting

- **Observable outcome:** An authorized forget request makes its memory unavailable through every retrieval path, remains safe when repeated or partially cleaned up, and never reactivates an older superseded memory.
- **Included behavior:** Separate typed forgetting operation; mandatory trusted user scope; immediate logical exclusion; serialized write lock; SQLite and FAISS cleanup; pending physical deletion state; idempotent retry; no-reactivation invariant; unauthorized request rejection.
- **Exclusions:** General recovery/rebuild beyond deletion completion, observability rollout, production authentication, and fixed-workload completion.
- **Intended files / freeze boundary:** `src/conversational_memory/domain/**`; `src/conversational_memory/application/**`; `src/conversational_memory/infrastructure/sqlite/**`; `src/conversational_memory/infrastructure/faiss/**`; `src/conversational_memory/infrastructure/sqlite/migrations/**`; `src/conversational_memory/entrypoints/**`; `tests/unit/domain/**`; `tests/adapter/**`; `tests/integration/test_forgetting.py`; `tests/regression/test_forgetting.py`.
- **Existing characterization evidence:** `experiments/d4_deletion/test_forgetting.py`; `experiments/d4_deletion/test_adversarial_memory.py`; `experiments/d4_conflict_resolution/test_no_reactivation.py`; `design/decision_records/ADR-007-privacy-isolation-and-forgetting.md`.
- **Proposed test evidence:** Owner and cross-user deletion tests, repeated deletion, partial FAISS-cleanup failure, all retrieval paths, and no-reactivation regression.
- **Verification command (future):** `python -m pytest tests/unit/domain tests/adapter tests/integration/test_forgetting.py tests/regression/test_forgetting.py -q`
- **Demo command (future):** `python -m conversational_memory.entrypoints.cli demo-forgetting`
- **Binary success conditions:**
  - An authorized accepted forget request immediately blocks current and historical retrieval.
  - Repeating the request does not create a new side effect or error unsafe state.
  - A FAISS cleanup failure leaves the memory blocked and reports incomplete deletion.
  - Another user cannot forget the memory even when its ID is known.
  - Forgetting the newer memory does not reactivate the older superseded memory.
- **Dependencies:** M6.
- **Applicable G4 gates:** Phase 1 dependency direction; Phase 3 authorization and typed operation; Phase 6 deletion/lifecycle precedence; Phase 9 characterization; Phase 12 idempotent partial-failure handling; cross-phase verification.
- **Loops:** L1, L4.
- **Skills / wiki pointers:** `agentic-swe-master`; `security-engineering`; `data-systems-engineering`; `$AGENTIC_SWE_WIKI_ROOT/security-engineering/concepts/Access-Control.md`; `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Transactions-and-Isolation.md`; `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Ruthless-Testing.md`.
- **Main risk:** A partial deletion or newer-memory deletion could expose deleted or superseded data. Immediate source-store blocking and all-path failure tests enforce the approved invariant.
- **Token budget:** 50000

### M8 — Recovery and Reconciliation

- **Observable outcome:** Startup safely rebuilds missing or inconsistent FAISS state from authoritative SQLite embeddings and retries pending records without duplicate vectors or unsafe retrieval.
- **Included behavior:** SQLite integrity and schema checks; ordered transactional migrations; model/dimension metadata validation; empty-index creation; FAISS load and mapping comparison; orphan removal; rebuild from stored float32 embeddings; pending/failed retry; stable mapping; atomic replacement of a verified rebuilt index; degraded readiness only for safely excluded records; fail-closed unsafe startup; shared process write lock.
- **Exclusions:** Multiple simultaneous embedding models, remote workers, queues, distributed recovery, high availability, and production-scale optimization.
- **Intended files / freeze boundary:** `src/conversational_memory/application/**`; `src/conversational_memory/infrastructure/sqlite/**`; `src/conversational_memory/infrastructure/sqlite/migrations/**`; `src/conversational_memory/infrastructure/faiss/**`; `src/conversational_memory/composition/**`; `src/conversational_memory/entrypoints/**`; `tests/adapter/**`; `tests/integration/test_recovery.py`; `tests/integration/test_startup_readiness.py`; `tests/regression/**`.
- **Existing characterization evidence:** `experiments/d4_engineering_reality/recovery_analysis.md`; `experiments/d4_engineering_reality/migration_analysis.md`; `experiments/d4_engineering_reality/test_faiss_scale.py`.
- **Proposed test evidence:** New database and migration tests, missing/damaged/orphaned index cases, pending retry, duplicate prevention, model/dimension mismatch, degraded readiness, and unsafe-start rejection.
- **Verification command (future):** `python -m pytest tests/adapter tests/integration/test_recovery.py tests/integration/test_startup_readiness.py tests/regression -q`
- **Demo command (future):** `python -m conversational_memory.entrypoints.cli demo-recovery`
- **Binary success conditions:**
  - A missing or inconsistent FAISS index is rebuilt from SQLite embeddings with the same memory/vector mappings.
  - Pending records are retried without duplicate rows or vectors.
  - Deleted, unauthorized, and otherwise ineligible records do not become retrievable during recovery.
  - A model or dimension mismatch never mixes incompatible vectors.
  - Unsafe SQLite, migration, mapping, or rebuild state prevents readiness.
  - Safely unindexed records may produce degraded readiness but remain excluded from retrieval.
- **Dependencies:** M7.
- **Applicable G4 gates:** Phase 1 dependency direction; Phase 3 startup and service availability; Phase 6 authoritative/derived storage; Phase 12 checkpointing, retry, resumption, and consistency; cross-phase verification.
- **Loops:** L1, L3, L4.
- **Skills / wiki pointers:** `agentic-swe-master`; `data-systems-engineering`; `production-readiness`; `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Transactions-and-Isolation.md`; `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Encoding-and-Schema-Evolution.md`; `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Derived-Data-Systems.md`; `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Recovery-Patterns.md`.
- **Main risk:** Reconciliation could treat stale FAISS state as authoritative or replace a usable index with an incomplete rebuild. Source-of-truth checks and verified atomic replacement are required; rebuild duration and single-writer capacity remain measurement questions.
- **Token budget:** 50000

### M9 — Privacy-Safe Observability

- **Observable outcome:** Every important operation emits structured decision and failure events sufficient for tracing without logging memory or authentication content.
- **Included behavior:** Events for admission, storage, indexing, retry, retrieval, startup, recovery, configuration mismatch, degraded readiness, and forgetting; request/memory identifiers; anonymized user identifier; reason/status codes; retry counts; durations; token use; index size; model/dimension metadata; readiness state.
- **Exclusions:** Raw conversation text, query text, memory values, embeddings, authentication data, secrets, sensitive rejection details, production monitoring infrastructure, and remote telemetry services.
- **Intended files / freeze boundary:** `src/conversational_memory/application/**`; `src/conversational_memory/infrastructure/**`; `src/conversational_memory/composition/**`; `src/conversational_memory/entrypoints/**`; `tests/unit/application/test_events.py`; `tests/integration/test_observability.py`; `tests/regression/**`.
- **Existing characterization evidence:** `experiments/d4_engineering_reality/observability_analysis.md`; `.genesis/decisions/decisions-manifest.md`.
- **Proposed test evidence:** Event-schema and coverage tests plus forbidden-field scans over captured structured logs.
- **Verification command (future):** `python -m pytest tests/unit/application/test_events.py tests/integration/test_observability.py tests/regression -q`
- **Demo command (future):** `python -m conversational_memory.entrypoints.cli demo-observability`
- **Binary success conditions:**
  - Every approved operation category emits its required structured event.
  - Admission and retrieval results retain their approved decision metadata.
  - Degraded startup emits `startup_degraded` and exposes degraded readiness.
  - Captured logs contain none of the explicitly forbidden content fields.
  - Sensitive rejection events expose only a general reason code.
- **Dependencies:** M8.
- **Applicable G4 gates:** Phase 1 dependency direction; Phase 3 errors and operation boundaries; Phase 12 readiness and recovery evidence; cross-phase verification.
- **Loops:** L1, L4.
- **Skills / wiki pointers:** `agentic-swe-master`; `production-readiness`; `security-engineering`; `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Transparency-and-Observability.md`; `$AGENTIC_SWE_WIKI_ROOT/security-engineering/concepts/Access-Control.md`.
- **Main risk:** Diagnostic metadata could accidentally disclose private text or identifiers. Explicit allowlisted event fields and forbidden-field tests provide the safety boundary.
- **Token budget:** 50000

### M10 — Complete Fixed-Workload Evaluation

- **Observable outcome:** The integrated system runs all six fixed workload cases and produces repeatable PASS, PARTIAL, or FAIL evidence against the naive baseline without claiming production readiness.
- **Included behavior:** Contradictory memories, preference changes, long context, cross-user isolation, sensitive-memory rejection, and no-relevant-memory cases; integrated real-component execution; expected-result assertions; reproducible CLI summary; complete applicable test suite.
- **Exclusions:** New or easier replacement cases, LLM-as-judge, response generation, production evaluation, deployment gates, model training, learned reranking, and production-readiness claims.
- **Intended files / freeze boundary:** `tests/evaluation/**`; `tests/regression/**`; `src/conversational_memory/entrypoints/**`. Existing `experiments/naive_baseline/workload/**`, `experiments/baseline_protocol.md`, and prototype outputs remain unchanged as comparison evidence.
- **Existing characterization evidence:** `experiments/naive_baseline/workload/case1_irrelevant_contradictory.json`; `experiments/naive_baseline/workload/case2_preference_change.json`; `experiments/naive_baseline/workload/case3_long_context.json`; `experiments/naive_baseline/workload/case4_multi_user.json`; `experiments/naive_baseline/workload/case5_sensitive_memory.json`; `experiments/naive_baseline/workload/case6_cold_start.json`; `experiments/baseline_protocol.md`; `experiments/baseline_results.csv`; `experiments/d4_evaluation/evaluation_matrix.py`; `experiments/d4_evaluation/evaluation_results.py`.
- **Proposed test evidence:** One parameterized integrated evaluation suite covering the unchanged six cases plus a complete regression and architecture run.
- **Verification command (future):** `python -m pytest tests/evaluation tests/regression tests/architecture -q`
- **Complete-suite command (future):** `python -m pytest tests -q`
- **Demo command (future):** `python -m conversational_memory.entrypoints.cli evaluate-fixed-workload`
- **Binary success conditions:**
  - All six original workload case files are used without replacement.
  - Every case records a reproducible PASS, PARTIAL, or FAIL result and reason.
  - Cross-user, sensitive, deleted, stale, and over-budget output never occurs.
  - The complete applicable automated suite passes.
  - The evaluation output explicitly describes the system as a pre-production reference implementation and makes no unsupported production claim.
- **Dependencies:** M9.
- **Applicable G4 gates:** Phase 1 architecture; Phase 3 integrated service; Phase 6 complete approved memory behavior; Phase 9 fixed-workload evaluation; Phase 12 failure and recovery coverage; cross-phase verification.
- **Loops:** L1, L4.
- **Skills / wiki pointers:** `agentic-swe-master`; `llmops-ai-agents`; `production-readiness`; `$AGENTIC_SWE_WIKI_ROOT/llmops-ai-agents/concepts/Evaluation-Frameworks.md`; `$AGENTIC_SWE_WIKI_ROOT/pragmatic-programmer/concepts/Ruthless-Testing.md`.
- **Main risk:** An aggregate evaluation result could conceal a privacy or lifecycle failure. Results must remain case-specific, and safety failures cannot be averaged away.
- **Token budget:** 50000

Total planned milestone token budget: 500000.

---

## Progress (loops append here on milestone completion — newest last)

- _(none yet — first loop fills this)_
