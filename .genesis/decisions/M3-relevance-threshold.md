# M3 relevance-threshold decision

- **Date:** 2026-09-03
- **Status:** accepted
- **Phase / milestone:** M3 explicit no-relevant-memory result

## Context

The D3 cold-start case showed that nearest-neighbor retrieval forces unrelated memory
into context when no relevance boundary exists. The D4 controlled experiment used a
minimum score of `0.50`, treated equality as relevant, and returned no memory when its
synthetic candidates scored `0.31`, `0.28`, and `0.25`. That evidence is partial rather
than a production calibration, but it is the approved initial M3 policy and must be
covered by integrated and exact-boundary tests.

## Decision

M3 uses an explicitly supplied relevance threshold of exactly `0.50`.

- A finite candidate score is relevant when `score >= 0.50`.
- A score below `0.50` is not relevant and cannot be ranked into context.
- Equality is included. Boundary verification must cover the immediately lower value,
  exact `0.50`, and the immediately higher value without rounding, epsilon comparison,
  quantization, or score buckets.
- Deterministic ownership and M2 current-state eligibility run before relevance. No
  unauthorized or otherwise ineligible memory may be disclosed through relevance
  exclusions or result metadata.

The threshold is required configuration supplied through the approved application
composition boundary. A missing value, boolean, non-numeric value, non-finite value,
out-of-range value for normalized inner-product scoring, or value other than the
approved `0.50` fails closed as a typed configuration error before retrieval. There is
no implicit default, environment fallback, alternate threshold, forced nearest match,
or degraded similarity-only mode. A different numeric value is a policy change and
requires a new approved decision.

## Distinct empty-result outcomes

The retrieval contract must preserve these distinctions:

1. **No eligible memory:** the authoritative pre-search M2 allowlist is empty. Return a
   successful empty result identified as `no_eligible_memory`; do not embed or search,
   and do not invent relevance exclusions.
2. **No relevant memory:** eligible memories were searched but every returned score is
   below `0.50`. Return a successful empty result identified as
   `no_relevant_memory`, with empty context and structured below-threshold exclusions
   only for authorized, eligible candidates.
3. **Budget-excluded memory:** at least one candidate met `score >= 0.50`, but context
   construction excluded it for `budget_exceeded`. Preserve the budget exclusions and
   do not label the result `no_eligible_memory` or `no_relevant_memory`.

Mixed results retain selected relevant memories and structured exclusions for any
authorized eligible candidates below threshold or over budget. Relevance filtering
occurs before ranking and token-budget context construction.

## Scope limits

This decision does not add learned thresholding, reranking, threshold tuning,
supersession, historical retrieval, expiration transitions, forgetting, recovery,
observability rollout, or full fixed-workload scoring. It does not change locked
`PLAN.md` or `DONE.html`.
