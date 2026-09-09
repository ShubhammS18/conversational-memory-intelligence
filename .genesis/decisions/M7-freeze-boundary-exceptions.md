# M7 freeze-boundary exceptions

- Status: Accepted
- Date: 2026-09-08
- Scope: M7 only

The locked M7 PLAN boundary is extended by exactly these paths:

1. `.genesis/decisions/M7-forgetting-bindings.md` — record the approved M7
   public, persistence, cleanup, retry, concurrency, and failure rules.
2. `.genesis/decisions/M7-freeze-boundary-exceptions.md` — record this exact
   exception set.
3. `.genesis/decisions/decisions-manifest.md` — register the two M7 decisions.
4. `.genesis/checkpoints/M7.md` — record mandatory M7 gate and iteration
   evidence.
5. `.genesis/checkpoints/CURRENT.md` — maintain canonical rolling M7 state.
6. `tests/integration/test_current_state_filtering.py` — update only the exact
   schema-migration list assertion from `[1, 2]` to `[1, 2, 3]` after the
   approved ordered M7 forgetting migration; all current-state assertions are
   frozen unchanged.
7. `tests/regression/__init__.py` — add only an empty package marker so the
   locked M7 command can collect its required regression file without a module
   name collision with `tests/unit/domain/test_forgetting.py`.
8. `src/conversational_memory/infrastructure/faiss_index.py` — modify only the
   M7 targeted-removal load path so missing or unverifiable durable generation
   files fail closed instead of treating cached in-memory absence as durable
   absence.

These exceptions do not authorize other changes to completed milestone
bindings or tests, PLAN, DONE, context graph, implementation notes, wiki,
dependencies, or files outside the locked M7 implementation boundary. They do
not authorize M8-M10 behavior, installation, staging, commits, pushes, review
approval, or milestone closure.
