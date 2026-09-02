# M1 freeze-boundary exceptions

- **Date:** 2026-09-02
- **Status:** accepted
- **Phase / milestone:** M1 persisted end-to-end memory slice

## Context

The independent L4 review rejected the complete working-tree diff because eight supporting paths are outside the locked M1 freeze boundary in `PLAN.md`. The paths contain mandatory Genesis evidence, the approved M1 ranking interpretation, or relocation and collection support for existing M1 tests. Removing them would discard approved work or duplicate and reduce the clarity of the locked test evidence.

## Decision

For M1 only, approve these additions to the existing freeze boundary for the stated purposes. The first eight paths are the original approved exceptions; the final two paths are explicitly authorized to record and register this decision:

- `.genesis/checkpoints/CURRENT.md` — mandatory current-loop evidence.
- `.genesis/checkpoints/M1.md` — mandatory milestone evidence.
- `.genesis/decisions/M1-implementation-bindings.md` — approved M1 implementation bindings, including the exact-score ranking interpretation.
- `tests/integration/test_memory_service_sqlite_faiss.py` — shared real SQLite/FAISS M1 scenarios collected by the locked integration paths.
- `tests/integration/test_owner_scoped_retrieval.py` — existing owner-isolation evidence updated for construction-time strict Pydantic validation.
- `tests/integration/test_bounded_context.py` — deletion only, because its coverage moved to the locked `tests/regression/test_context_budget.py` path.
- `tests/__init__.py` — package marker supporting imports of shared M1 test scenarios.
- `tests/integration/__init__.py` — package marker supporting imports of shared M1 integration scenarios.
- `.genesis/decisions/M1-freeze-boundary-exceptions.md` — this approved freeze-exception record itself.
- `.genesis/decisions/decisions-manifest.md` — registration of this approved record in the existing decision manifest.

No other M1 scope, behavior, milestone, `PLAN.md`, or `DONE.html` change is authorized. This decision does not authorize staging, committing, installation, or milestone closure.

## Consequences

- Positive: the complete M1 diff has an explicit reviewed boundary covering mandatory evidence and existing test relocation/collection without weakening coverage, plus the record and manifest entry that make the approval discoverable.
- Negative / cost: reviewers must evaluate the ten exact exception paths in addition to the original M1 freeze boundary.
- No context-graph invariant is added; this is a scoped implementation-boundary decision.

## Alternatives rejected

- Revert the supporting paths — would discard pre-existing approved work or break the locked evidence layout.
- Duplicate the shared scenarios into the locked files — would increase maintenance cost and risk divergent coverage.
- Modify locked `PLAN.md` or `DONE.html` — broader than the approved exception and prohibited by the request.
