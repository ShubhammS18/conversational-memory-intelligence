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
A local Python caller and CLI, SQLite, persisted FAISS files, SentenceTransformers using `all-mpnet-base-v2`, an approved tokenizer adapter, a trusted UTC clock, approved configuration, and the local filesystem. Remote services, external LLM calls, and response generation are not integration points for this milestone.

## Auth requirements
The caller establishes identity. Every normal memory operation is scoped to that identity. No cross-user read, update, or deletion is allowed even when a memory ID is known. Recovery uses a separate internal interface. Implementing authentication is outside scope.

## Compliance constraints
No formal GDPR, SOC 2, HIPAA, or data-residency compliance is claimed for this milestone. Preserve the approved privacy controls and require a separate design decision before making compliance claims.

## Primary failure mode (the honest one)
SQLite and FAISS diverge during a partial failure or restart, causing a valid memory to disappear or an ineligible memory to remain retrievable.

## Quality bar ("embarrassed to ship if...")
Any cross-user disclosure, sensitive-memory retention, retrieval of deleted or invalid current-state memory, token-budget violation, duplicate side effect on retry, or demonstration that relies on mocked SQLite, FAISS, or embeddings.

## Known unknowns → research spikes needed
- Select and verify the exact tokenizer used for context accounting without introducing response generation into scope.
- Measure crash recovery, atomic FAISS replacement, rebuild duration, and single-writer capacity on the target local environment.

## Assumptions never stated aloud (agent-inferred from answers above)
- The initial local workload is small enough for one process and serialized admission, deletion, and recovery writes to remain adequate until measurements show otherwise.
- The calling application establishes a trustworthy user identity before creating `RequestContext`.
- The fixed workload and real-component restart tests provide sufficient evidence for the integrated pre-production milestone, but not for production readiness.
