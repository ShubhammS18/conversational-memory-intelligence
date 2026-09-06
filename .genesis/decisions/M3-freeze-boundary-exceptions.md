# M3 freeze-boundary exceptions

- **Date:** 2026-09-03
- **Status:** accepted
- **Phase / milestone:** M3 explicit no-relevant-memory result

## Context

The locked M3 boundary already authorizes the approved threshold decision artifact.
It does not list the additional Genesis paths required to track and register M3.

## Decision

For M3 only, add exactly these eleven paths for the stated purposes:

- `.genesis/checkpoints/CURRENT.md` — mandatory current-loop evidence only.
- `.genesis/checkpoints/M3.md` — mandatory M3 milestone and verification evidence only.
- `.genesis/decisions/M3-freeze-boundary-exceptions.md` — this exception record only.
- `.genesis/decisions/decisions-manifest.md` — registration of the M3 threshold and
  freeze-exception records only.
- `tests/unit/application/test_admission_service.py` — explicit approved-threshold
  injection in the existing application-service harness only.
- `tests/integration/test_memory_service_sqlite_faiss.py` — explicit
  approved-threshold injection in the existing shared service helper only.
- `tests/integration/test_idempotent_admission.py` — explicit approved-threshold
  injection in the existing concurrency service helper only.
- `tests/integration/test_current_state_filtering.py` — explicit approved-threshold
  injection in its two existing service compositions and adjustment of only the
  intended current-memory score from `0.1` to the inclusive `0.50` boundary.
- `tests/integration/test_first_slice.py` — explicit approved-threshold injection in
  its existing service composition only.
- `tests/integration/test_owner_scoped_retrieval.py` — explicit approved-threshold
  injection in its existing shared service helper only.
- `tests/integration/test_real_model_first_slice.py` — explicit approved-threshold
  injection in its existing initial and restarted service compositions only.

`.genesis/decisions/M3-relevance-threshold.md` requires no exception because the
locked M3 PLAN expressly includes the approved threshold decision artifact.

The seven test exceptions preserve existing M1/M2 coverage under M3's mandatory
explicit configuration; they do not authorize new behavior, assertions, helpers, or
unrelated fixture changes. No wildcard, additional path, production configuration,
dependency, M4-M10 behavior, `PLAN.md`, `DONE.html`, staging, commit, or milestone
closure is authorized by this record.
