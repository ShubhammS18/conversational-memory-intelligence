# decisions-manifest — conversational-memory-intelligence
Generated: 2026-08-28 via KICKOFF-INTERVIEW.md

## Trade-off ranking
1. Reliability
2. Maintainability
3. Cost
4. Speed

## Scale
Launch: One local process used for development, demonstration, and the fixed workload; no production user, request, or data-volume target is assumed.
12 months: Unknown until a real deployment target exists; measurement and research are required before any scale commitment.

## Project type
Prototype — a pre-production reference implementation with production-quality correctness for the approved privacy and lifecycle invariants.

## Performance constraints (non-negotiable)
- No hard latency SLO is approved for the first implementation; capture admission and retrieval timings during integration.
- Measure startup and FAISS rebuild time on the fixed workload, then establish limits from evidence before expanding scale.

## UX / brand constraints
No brand requirements. Provide concise human-readable CLI output plus stable structured decision metadata, while never displaying secrets or logging sensitive content.

## Failure behaviour
Use typed errors and explicit status metadata. Retry only safe idempotent work. Allow degraded startup only when all returned memories remain authorized and consistent; otherwise refuse startup.

## Integration points
A local Python caller and CLI, SQLite, persisted FAISS files, SentenceTransformers using `all-mpnet-base-v2`, `tiktoken` using the exact `cl100k_base` encoding for context-budget accounting, a trusted UTC clock, approved configuration, and the local filesystem. `tiktoken` is used only for token counting and does not introduce response generation or an external LLM call. Remote services, external LLM calls, and response generation are not integration points for this milestone.

## Approved implementation layout
- Production package root: `src/conversational_memory/`
- Top-level production areas: `domain`, `application`, `infrastructure`, `composition`, and `entrypoints`
- SQLite migrations: `src/conversational_memory/infrastructure/sqlite/migrations/`
- Test areas:
  - `tests/architecture/`
  - `tests/unit/`
  - `tests/adapter/`
  - `tests/integration/`
  - `tests/regression/`
  - `tests/evaluation/`
- CLI module: `python -m conversational_memory.entrypoints.cli`
- Milestone-specific CLI subcommands may be defined during G5.

## Authoritative implementation sequence
1. Admit, embed, persist pending, index, restart, retrieve, and construct bounded context.
2. Current-state filtering.
3. No-relevant-memory behavior.
4. Supersession and conflict handling.
5. Historical retrieval.
6. Expiration.
7. Forgetting.
8. Recovery and reconciliation.
9. Privacy-safe observability.
10. Complete fixed-workload evaluation.

This ten-slice sequence governs Genesis implementation planning. `design/sprint_plan.md` remains historical design material but does not override this sequence.

## Auth requirements
The caller establishes identity. Every normal memory operation is scoped to that identity. No cross-user read, update, or deletion is allowed even when a memory ID is known. Recovery uses a separate internal interface. Implementing authentication is outside scope.

## Compliance constraints
No formal GDPR, SOC 2, HIPAA, or data-residency compliance is claimed for this milestone. Preserve the approved privacy controls and require a separate design decision before making compliance claims.

## Primary failure mode (the honest one)
SQLite and FAISS diverge during a partial failure or restart, causing a valid memory to disappear or an ineligible memory to remain retrievable.

## Quality bar ("embarrassed to ship if...")
Any cross-user disclosure, sensitive-memory retention, retrieval of deleted or invalid current-state memory, token-budget violation, duplicate side effect on retry, or demonstration that relies on mocked SQLite, FAISS, or embeddings.

## Known unknowns → research spikes needed
- Measure crash recovery, atomic FAISS replacement, rebuild duration, and single-writer capacity on the target local environment.

## Assumptions never stated aloud (agent-inferred from answers above)
- The initial local workload is small enough for one process and serialized admission, deletion, and recovery writes to remain adequate until measurements show otherwise.
- The calling application establishes a trustworthy user identity before creating `RequestContext`.
- The fixed workload and real-component restart tests provide sufficient evidence for the integrated pre-production milestone, but not for production readiness.

## Binding implementation decision records

- [`M1-implementation-bindings`](M1-implementation-bindings.md) — package, idempotency, credential-admission, SQLite/FAISS persistence, indexing-state, and exact context-serialization bindings for the integrated M1 slice.
- [`M1-freeze-boundary-exceptions`](M1-freeze-boundary-exceptions.md) — eight approved M1-only evidence/test-support paths plus the decision record and this manifest registration.
- [`M2-current-state-eligibility-bindings`](M2-current-state-eligibility-bindings.md) — M2 read-side tombstone, lifecycle, supersession, validity-boundary, and single-trusted-time rules for pre-search and hydration eligibility.
- [`M2-freeze-boundary-exceptions`](M2-freeze-boundary-exceptions.md) — exactly five M2-only Genesis paths for mandatory checkpoint evidence and scoped decision/manifest records.
- [`M3-relevance-threshold`](M3-relevance-threshold.md) — exact `0.50` relevance boundary, inclusive comparison, fail-closed configuration, and distinct empty-result semantics for M3.
- [`M3-freeze-boundary-exceptions`](M3-freeze-boundary-exceptions.md) — four M3-only Genesis governance paths plus seven exact test-maintenance paths for explicit threshold injection and one current-state fixture-boundary adjustment.
- [`M4-supersession-and-conflict-bindings`](M4-supersession-and-conflict-bindings.md) — one-target explicit supersession, fail-closed target validation, indexing-before-linking, atomic bidirectional relationships, idempotent retry, and no-reactivation rules.
- [`M4-freeze-boundary-exceptions`](M4-freeze-boundary-exceptions.md) — exactly five M4-only Genesis paths for current-loop evidence and scoped decision, checkpoint, exception, and manifest records.
- [`M5-historical-retrieval-bindings`](M5-historical-retrieval-bindings.md) — explicit typed historical intent, owner-scoped indexed and non-deleted lifecycle eligibility, unchanged relevance/context semantics, and read-only no-reactivation rules.
- [`M5-freeze-boundary-exceptions`](M5-freeze-boundary-exceptions.md) — the SQLite historical-read adapter path plus exactly five M5-only Genesis governance paths.
- [`M6-trusted-clock-expiration-bindings`](M6-trusted-clock-expiration-bindings.md) — one validated UTC clock reading per current retrieval, lazy owner-scoped expiration, fail-closed transition handling, and read-only historical access to consistent expired records.
- [`M6-freeze-boundary-exceptions`](M6-freeze-boundary-exceptions.md) — the SQLite expiration path, two exact completed-M5 test-maintenance paths, one M5 compatibility amendment, and five M6 governance paths.
- [`M7-forgetting-bindings`](M7-forgetting-bindings.md) — strict owner-scoped forgetting, immediate tombstone exclusion, nullable physical-cleanup identity, trusted request/completion timestamps, zero-FAISS null-ID retries, targeted known-ID removal, and restart-safe idempotency.
- [`M7-freeze-boundary-exceptions`](M7-freeze-boundary-exceptions.md) — five M7-only Genesis governance paths, one exact completed-M2 migration-list test maintenance exception, one empty regression-package marker, and the exact M7 targeted-removal FAISS adapter path.
- [`M8-recovery-and-reconciliation-bindings`](M8-recovery-and-reconciliation-bindings.md) — SQLite-authoritative startup audit, exact derived-index rebuild, strict readiness, safely excluded degraded state, M1 pending/failed preservation, and M7 cleanup handoff.
- [`M8-freeze-boundary-exceptions`](M8-freeze-boundary-exceptions.md) — five M8 governance paths plus the existing flat FAISS adapter and its infrastructure export path.
- [`M9-privacy-safe-observability-bindings`](M9-privacy-safe-observability-bindings.md) — exact structured-event vocabulary (including mark_failed), unchanged trusted request correlation, typed allowlists, HMAC user pseudonyms, recovery-owned configuration ordering without startup duplication, timing, deterministic serialization, privacy scans, and sink-failure non-interference.
- [`M9-freeze-boundary-exceptions`](M9-freeze-boundary-exceptions.md) — exactly five M9-only Genesis governance paths; all future BUILD work remains inside the locked M9 source/test boundary.
