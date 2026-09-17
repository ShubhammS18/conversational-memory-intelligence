# M9 freeze-boundary exceptions

- Status: Accepted
- Date: 2026-09-10
- Scope: M9 only

The locked M9 PLAN boundary is extended by exactly these governance paths:

1. `.genesis/decisions/M9-privacy-safe-observability-bindings.md` — record the
   exact event, privacy, pseudonymization, ordering, timing, serialization, and
   failure rules.
2. `.genesis/decisions/M9-freeze-boundary-exceptions.md` — record this exact
   exception set.
3. `.genesis/decisions/decisions-manifest.md` — register the two M9 decisions.
4. `.genesis/checkpoints/M9.md` — record mandatory M9 gate and iteration
   evidence.
5. `.genesis/checkpoints/CURRENT.md` — maintain canonical rolling M9 state.

Future BUILD work is restricted to these existing PLAN-authorized paths:

- `src/conversational_memory/application/**`
- `src/conversational_memory/infrastructure/**`
- `src/conversational_memory/composition/**`
- `src/conversational_memory/entrypoints/**`
- `tests/unit/application/test_events.py`
- `tests/integration/test_observability.py`
- `tests/regression/**`

The anticipated bounded files are:

- `src/conversational_memory/application/events.py`
- `src/conversational_memory/application/ports.py`
- `src/conversational_memory/application/service.py`
- `src/conversational_memory/application/recovery.py`
- `src/conversational_memory/application/__init__.py`
- `src/conversational_memory/infrastructure/observability.py`
- `src/conversational_memory/infrastructure/__init__.py`
- `src/conversational_memory/composition/__init__.py`
- `src/conversational_memory/entrypoints/cli.py`
- `tests/unit/application/test_events.py`
- `tests/integration/test_observability.py`
- `tests/regression/**`

This record does not pre-authorize changes to completed-milestone tests,
architecture tests, migrations, dependencies, PLAN, DONE, context graph,
implementation notes, wiki, or any other path. If implementation proves such a
change necessary, its exact path and purpose require separate approval.

These exceptions do not authorize BUILD activation, installation, staging,
commits, pushes, review approval, milestone closure, fixed-workload evaluation,
or any M10 behavior.
