# M10 freeze-boundary exceptions

- Status: Proposed; effective only after explicit approval and application
- Date: 2026-09-18
- Scope: M10 only

Extend the locked PLAN M10 boundary by exactly these governance paths:
1. `.genesis/decisions/M10-fixed-workload-evaluation-bindings.md` — approved
   fixture adaptation, safety, verdict, repeatability, reporting, and CLI policy.
2. `.genesis/decisions/M10-freeze-boundary-exceptions.md` — this exact exception.
3. `.genesis/decisions/decisions-manifest.md` — register both M10 records.
4. `.genesis/checkpoints/M10.md` — mandatory G0, iteration, and gate evidence.
5. `.genesis/checkpoints/CURRENT.md` — canonical rolling M10 state.

Future BUILD remains limited to the existing PLAN paths:
- `tests/evaluation/**`
- `tests/regression/**`
- `src/conversational_memory/entrypoints/**`

Anticipated first slice:
- `src/conversational_memory/entrypoints/evaluation.py`
- `tests/evaluation/test_fixed_workload.py`
- `tests/evaluation/__init__.py` — empty marker only if required.
Later CLI wiring may change `src/conversational_memory/entrypoints/cli.py`.

Original workload, baseline protocol/results, prototype outputs, completed-
milestone tests outside tests/regression, architecture tests, source outside
entrypoints, migrations, dependencies, PLAN, DONE, context graph, implementation
notes, wiki, and all other paths remain frozen. Exact further exceptions require
separate approval. Closure documentation is not authorized by this record.

This exception does not activate BUILD, execute evaluation, install/download,
run migrations, authorize staging/commits/pushes, approve L4, close M10, or
authorize production or post-M10 behavior.
