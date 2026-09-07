# M5 Freeze-Boundary Exceptions

- **Date:** 2026-09-07
- **Status:** Accepted
- **Phase / milestone:** M5 historical retrieval

## Context

The locked M5 implementation boundary in `PLAN.md` includes domain, application,
entry-point, and named test paths. It omits the SQLite adapter change required to
construct a complete owner-scoped, indexed, non-deleted historical allowlist
before FAISS search. It also omits mandatory Genesis evidence records.

## Decision

For M5 only, authorize exactly these additional paths:

- `src/conversational_memory/infrastructure/sqlite/repository.py` — implement the
  bound historical allowlist and defensive hydration rules only.
- `.genesis/checkpoints/CURRENT.md` — current M5 loop state only.
- `.genesis/checkpoints/M5.md` — mandatory M5 iteration and verification evidence.
- `.genesis/decisions/M5-historical-retrieval-bindings.md` — the approved M5
  interpretation only.
- `.genesis/decisions/M5-freeze-boundary-exceptions.md` — this exception record
  only.
- `.genesis/decisions/decisions-manifest.md` — registration of these two M5
  decisions only.

## Boundaries

These exceptions do not authorize schema migrations, changes to locked `PLAN.md`
or `DONE.html`, context-graph or implementation-note changes, dependency changes,
or files outside the locked M5 boundary and the six exact paths above.

They do not authorize M6–M10 behavior, automatic intent inference, staging,
commits, pushes, review approval, or milestone closure.
