# M10 fixed-workload evaluation bindings

- Status: Proposed; effective only after explicit approval and application
- Date: 2026-09-18
- Scope: M10 only; component and integration evaluation
- Authority: locked PLAN M10 and DONE; M1-M9 accepted bindings remain unchanged

## Authorization and evidence

M9 is closed after independent L4 APPROVE, quiz PASS 3/3, and authorized
closure. M10 G0 inspection is authorized. This record does not authorize BUILD.
Extend the existing integrated service; do not add extraction or inference.
Use baseline_protocol.md, baseline_results.csv, the six original workloads,
and D4 evaluation_matrix.py/evaluation_results.py as read-only evidence.
D4 component verdicts are not M10 executions or substitutes for real tests.

## Exact fixed inputs

Read these files in this order from experiments/naive_baseline/workload/:
1. case1_irrelevant_contradictory.json
2. case2_preference_change.json
3. case3_long_context.json
4. case4_multi_user.json
5. case5_sensitive_memory.json
6. case6_cold_start.json

Require the matching case1-case6 identifiers and these SHA-256 bytes:
- case1: c674bdb684b1f6699954fb984f42d106b8c2c5c2be30a81c07c5463ac70a3b98
- case2: 1ae1b665c66381d7c49e3a8bf2004a4bf6b86be3fdb0d4f17aec3b9783ddae2d
- case3: 7d1a5beb79f7f8e7aa44a9376a016dc0e1bf00778fbace70270498977e427762
- case4: 51e2f14e49917da2dffd3afe835d4bedfed5178e3820fee108cc2d241aa37917
- case5: 24d03d0811f68c53221be7084ae6b6f77d2375cf79402538d807d45d62b6b768
- case6: 2dac46c31aadef03800903b328151aabc0193ff3be7245e2c165bd5863560a48

Never rewrite, regenerate, paraphrase, deduplicate, drop repeated turns, add
padding, replace queries, or substitute fixtures. Case2 has three user turns
and no fillers despite the historical protocol's padding statement.
Each primary query is exactly evaluation_query.query, with explicit CURRENT.
User turns become candidates; assistant turns remain input but are not admitted.
Fillers become candidates after conversation turns, in their original order.
Case5 conversation index 2 is the sole control-action exception defined below.

## Trusted evaluation-only adaptation

Indices are zero-based indices in the original conversation/filler arrays.
Null-owner cases use trusted RequestContext.user_id = m10-caseN-user.
Case4 preserves user_a/user_b; its ownerless fillers and query belong to user_a.
Case6 uses the same populated owner for admissions and query, not a new owner.
Each case uses a separate temporary durable store, never a shared case pool.

For conversation index i, label cIIII; for filler index j, label fJJJJ,
where numbers are zero-padded to four digits. Planned memory ID is
m10-caseN-LABEL; conversation_id is m10-caseN; turn_id is LABEL;
idempotency_key is m10-caseN-admit-LABEL; admission request_id is that key.
The injected ID provider returns that candidate's planned ID if requested;
rejected admissions allocate no ID and must not shift subsequent IDs.
Request IDs for queries are m10-caseN-primary, m10-caseN-zero-budget,
and m10-caseN-post-restart. Preserve repository-assigned stable vector IDs.

Pass original candidate text unchanged as both content and value.
Default memory_type=fact, subject=m10.caseN.LABEL, source_type=explicit_user.
Only the following decision chains override memory_type to decision and
subject to project.storage; value remains the original text:
- case1: c0002 ordinary; c0004 explicitly targets m10-case1-c0002.
- case2: c0000 ordinary; c0002 targets m10-case2-c0000;
  c0004 targets m10-case2-c0002.
- case3: c0010 ordinary; c0025 targets m10-case3-c0010;
  c0040 targets m10-case3-c0025.
No other candidate has supersedes_memory_id. Case4 framework candidates
c0000 and c0002 use memory_type=preference and subject=deployment.framework.
These annotations are trusted harness/caller declarations, not inferred links,
NLP extraction, truth judgements, or evidence of automatic contradiction handling.
Existing application canonicalization remains unchanged.

## Frozen execution settings

T0 = 2026-01-01T00:00:00Z. Candidate creation clock and source_event_at are
T0 + original conversation index seconds; fillers use T0 + (conversation
array length + filler index) seconds. valid_from equals that time;
valid_until is null. All timestamps are aware UTC.
Primary and repeated retrieval use trusted time T0 + one day.
Threshold is explicitly injected 0.50; score >= 0.50 is relevant.
Retrieval limit is 10; primary memory-only budget is 128 cl100k_base tokens.
Do not tune settings, normalize scores, truncate blocks, or change ranking.

Use real SQLite, durable FAISS, and CPU-only normalized float32 MPNet:
sentence-transformers/all-mpnet-base-v2 at
e8c3b32edf5434bc2275fc9bab85f82640a19130, dimension 768.
Require verified local embedding/tokenizer caches; no downloads or fallback.
Create a fresh service against the same case store before primary retrieval;
repeat primary retrieval after another fresh restart. Compare the exact tuple
(outcome, ordered included IDs, ordered exclusions, tokens_used, context);
do not print context or substitute ID-only equality. Startup recovery must not be bypassed.
Execute cases sequentially, preserving source order. Seed 10 applies only to
any evaluation-local random choice; the mapping itself uses no randomness.

## Sensitive case and forgetting proof

Case5 c0000 is submitted normally and must be rejected as sensitive_credential
before embedding or persistence. Never seed the credential into SQLite/FAISS.
Process c0002 as ForgetRequest(memory_id=m10-case5-c0000), request_id
m10-case5-forget-rejected: expect opaque not_found and no mutation.
Do not admit this control text as durable memory. Admit all fifteen fillers.
The control action reads no clock and performs no vector work because its target
was never persisted. Primary query remains the original API-key query; expected empty outcome is
no_relevant_memory, not an artificially empty-owner no_eligible_memory.
Report the baseline's 17 candidates versus M10's 16 admission attempts
(1 rejected, 15 admitted) and 1 control action as an adaptation distinction.
After primary/repeat evidence, forget admitted f0000 under the same owner
at T0 + two days, with completion at T0 + two days + one second.
Use request_id m10-case5-forget-f0000. Require cleanup complete.
Restart and query that filler's original text with CURRENT and HISTORICAL,
limit 10 and budget 128; deleted f0000 must be absent from both.
These are supplemental checks using an original filler, not a seventh case
or replacements for the primary query. No supersession reactivation is allowed.

## Expected results and hard safety

Required primary IDs: case1 c0004; case2 c0004; case3 c0040;
case4 c0000. Their earlier linked targets must be excluded.
Case5 and case6 require successful empty context, zero selected IDs, and
no_relevant_memory. Case6 has 17 owned admitted candidates; no_eligible_memory
cannot demonstrate its relevance policy and is FAIL as required_state_failure.
Case1 c0000 (apartment) must never be selected.
Evaluate selected records and exact constructed context, not IDs alone.

For every primary, repeated, supplemental, and zero-budget result, enforce:
- owner scope, indexed state, current eligibility where CURRENT is requested;
- absence of tombstones, superseded current records, and sensitive content;
- selected relevance >= 0.50 and complete, unmodified serialized blocks;
- exact cl100k_base tokens_used equal to counted context and <= token_budget;
- agreement between selected memories, included IDs, outcome, and context.
Any violation is FAIL, never PARTIAL. Any selected memory in case6 is FAIL.
All forbidden apartment output in case1 and sensitive output in case5 is FAIL.
Require an additional original-query CURRENT retrieval with budget 0 for each
case: empty context/IDs/tokens; relevant candidates yield budget_excluded,
otherwise retain the truthful no_relevant_memory/no_eligible_memory outcome.
Do not manufacture relevant candidates to force a budget-only outcome.

## Verdicts and failures

Use only PASS, PARTIAL, FAIL; no weighted score or compensating aggregate.
PASS: all mandatory setup, safety, repeatability, and supplemental checks pass
and the exact primary expectation is met with no extra selected memories.
PARTIAL: all those checks pass, but an expected useful ID is not selected,
safe extra memories occur in cases1-5 (excluding case1 apartment), or an
otherwise safe empty result has a different expected empty outcome.
Case6 non-empty output is never PARTIAL.
FAIL: any hard violation, fixture/baseline mismatch, required admission/link/
cleanup failure, unexpected operation failure, missing prerequisite, or
non-reproducible result. Never skip a case or convert an error to PASS.
A useful-memory omission alone is PARTIAL; incorrect relationship/state is FAIL.

Closed reason codes: expected_behavior_confirmed, expected_memory_not_selected,
extra_memory_selected, unexpected_empty_outcome, safety_violation,
fixture_mismatch, baseline_mismatch, prerequisite_unavailable,
required_state_failure, unexpected_operation_failure, non_reproducible_result.
Assign FAIL first, then PARTIAL, then PASS; if several reasons apply, use
their order in this list within that severity and report all failed check IDs.
All hard checks are automated assertions; tests may accept truthful quality
PARTIAL but must fail on FAIL. No expected-failure marks or weakened gates.

## Reproducibility, reporting, and baseline comparison

Print one UTF-8 JSON object followed by newline, sorted keys, compact separators,
allow_nan=false. Cases are in case1-case6 order; check IDs are sorted.
Top-level fields: schema_version (1), implementation_stage
(pre-production reference implementation), policy_id (M10-fixed-workload-v1),
settings, comparison_notes, cases, totals.
settings contains model, revision, dimension, device, threshold, limit,
token_budget, tokenizer, frozen_time, seed; values are the settings above.
Each case contains case_id, fixture_sha256, baseline, adaptation, status,
reason_codes, checks, executed, outcome, selected_ids, exclusions, tokens_used,
token_budget, repeatable, restart_verified.
baseline contains memory_count, retrieved_count, failures_observed,
failure_names exactly from its recorded CSV row. Do not print baseline latency.
adaptation contains user_candidate_count, filler_count, admission_attempts,
accepted_count, rejected_count, control_actions.
executed is boolean; counts/budgets are strict nonnegative integers, checks
are strict booleans, IDs/reasons are closed strings, settings have the exact
types of the frozen constants (booleans are not numbers). outcome is an
existing RetrievalOutcome string, or null only if retrieval did not execute;
tokens_used is null only for such an unexecuted result. Never invent empty
retrieval evidence when setup failed.
checks uses only these fixed identifiers, omitting inapplicable checks:
fixture_bytes, baseline_bytes, candidate_accounting, admission_states,
explicit_links, sensitive_rejected, rejected_target_not_found,
restart_verified, current_owner_scope, indexed_only, current_state_only,
tombstones_absent, stale_targets_absent, sensitive_absent, apartment_absent,
relevance_boundary, complete_blocks, exact_tokens, budget_bound,
result_consistency, expected_primary_ids, expected_empty_outcome,
zero_budget_empty, zero_budget_outcome, repeatable,
forgetting_complete, deleted_current_absent, deleted_historical_absent,
no_reactivation, report_privacy.
exclusions contain only planned memory ID and existing closed exclusion reason.
totals contains pass, partial, fail counts; no overall quality average.
Selected IDs are evaluation-local planned IDs, never caller/raw owner IDs.
Unknown check IDs are rejected; no check ID contains fixture text.
Unknown fields, free-form reasons, exception text, paths, raw owner IDs,
queries, content/value/subject, credentials, keys, prompts, vectors, and raw
event dumps are forbidden in public output, diagnostics, and subprocess streams.
Recursively scan objects, serialized JSON, and UTF-8 bytes for forbidden fields
and fixture-derived sensitive/user/query/content sentinels. Preserve no reports
inside the repository unless separately approved; stdout is the evidence surface.
Do not attach variable wall time, latency, temp paths, or timestamps to the
deterministic summary. Existing M9 event contracts are not extended.

Compare historical recorded failures, not newly fabricated baseline outcomes.
Require baseline_protocol SHA-256
8731f90878db8aa656f3cc872b9856fd3c63661cc6d638c71cfe46c6094cc6e3
and baseline_results.csv SHA-256
8fff27539660715a09b9f83f9e61f1a0bc9405630a1c708b8f854f5a9cb857f5.
All six CSV rows must be present; their failures_observed are 1.
Do not run the old writer, overwrite baseline artifacts, or claim comparable
latency from different pipelines. comparison_notes explicitly disclose typed
metadata/identity/supersession adaptation, case2's lack of padding, case5's
control-action treatment, and case6's populated same-owner relevance probe.

## CLI, tests, boundaries, and gates

Exact command: python -m conversational_memory.entrypoints.cli evaluate-fixed-workload.
Exit 0: all six evaluated with no FAIL (PARTIAL is explicitly reported).
Exit 1: evaluation completed with at least one FAIL.
Exit 2: fixture/baseline/configuration/cache/prerequisite prevents completion;
emit only safe codes/checks for failed cases, mark unexecuted cases FAIL with
prerequisite_unavailable, and never represent them as executed.
No traceback, raw primary context, or model progress chatter may leak to output.

First BUILD slice, only after separate authorization: strict fixed-input loader,
immutable report contract, deterministic safe serializer, and focused tests.
Later slices: real typed adaptation/execution, parameterized case assertions,
fresh-restart and safety probes, then shared CLI runner/parser coverage.
Production helpers live in entrypoints, never import tests or prototype writers.
Entry points depend only on application/composition and stdlib; preserve the
architecture test unchanged. Test-only authoritative inspections may use real
SQLite/FAISS adapters inside the allowed evaluation/regression paths.

Locked gate: python -m pytest tests/evaluation tests/regression tests/architecture -q
Complete gate: python -m pytest tests -q
Also run architecture, real offline CLI, Ruff, explicit-source mypy, pip check,
CLI help, context-graph validation, and git diff --check during maker verification.
Require actual real-model execution, no skips, downloads, or substitutions.
Independent fresh L4 APPROVE and human quiz PASS 3/3 precede authorized closure.
Preserve installation-isolation history, unreconstructed cache provenance,
Windows-temp tokenizer limitations, unavailable review skill, and process-only
locking. M10 does not resolve those limitations or waive an existing gate.

Excluded: new/easier cases, automatic extraction/link inference, service/domain/
adapter changes, migrations/dependencies, response generation, LLM judging,
training, learned reranking, distributed/remote infrastructure, production
evaluation/deployment/compliance/readiness claims, and any post-M10 work.
