# M8 freeze-boundary exceptions

- Status: Accepted
- Date: 2026-09-09
- Scope: M8 only

The locked M8 PLAN boundary is extended by exactly these paths:

1. `.genesis/decisions/M8-recovery-and-reconciliation-bindings.md` — record
   the approved M8 authority, audit, reconciliation, readiness, publication,
   locking, and failure rules.
2. `.genesis/decisions/M8-freeze-boundary-exceptions.md` — record this exact
   exception set.
3. `.genesis/decisions/decisions-manifest.md` — register the two M8 decisions.
4. `.genesis/checkpoints/M8.md` — record mandatory M8 gate and iteration
   evidence.
5. `.genesis/checkpoints/CURRENT.md` — maintain canonical rolling M8 state.
6. `src/conversational_memory/infrastructure/faiss_index.py` — implement the
   M8 FAISS audit, exact-set rebuild, orphan removal, and verified durable
   publication behavior. This exact flat adapter path replaces the PLAN’s
   nonexistent `src/conversational_memory/infrastructure/faiss/**` layout.
7. `src/conversational_memory/infrastructure/__init__.py` — export only the M8
   recovery adapter surface required by composition.

All other future BUILD files must remain inside the existing locked M8 boundary:

- `src/conversational_memory/application/**`
- `src/conversational_memory/infrastructure/sqlite/**`
- `src/conversational_memory/infrastructure/sqlite/migrations/**`
- `src/conversational_memory/composition/**`
- `src/conversational_memory/entrypoints/**`
- `tests/adapter/**`
- `tests/integration/test_recovery.py`
- `tests/integration/test_startup_readiness.py`
- `tests/regression/**`

No new SQLite migration is authorized merely by this decision. If
implementation demonstrates that a schema change or a completed-milestone test
change is necessary, its exact path and purpose require separate approval.

These exceptions do not authorize changes to PLAN, DONE, context graph,
implementation notes, completed milestone bindings or tests, wiki,
dependencies, or any path outside the locked M8 boundary and the seven paths
above. They do not authorize M9-M10 behavior, installation, staging, commits,
pushes, review approval, or milestone closure.
