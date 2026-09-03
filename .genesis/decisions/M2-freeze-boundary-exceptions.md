# M2 freeze-boundary exceptions

- **Date:** 2026-09-03
- **Status:** accepted
- **Phase / milestone:** M2 current-state filtering

## Context

The locked M2 freeze boundary in `PLAN.md` covers implementation and test paths but
does not list the mandatory Genesis checkpoint and decision records needed to govern
and evidence the milestone. The user explicitly approved five exact Genesis paths
for those purposes only.

## Decision

For M2 only, add exactly these paths to the freeze boundary for the stated purposes:

- `.genesis/checkpoints/CURRENT.md` — mandatory current-loop evidence only.
- `.genesis/checkpoints/M2.md` — mandatory M2 milestone and verification evidence only.
- `.genesis/decisions/M2-current-state-eligibility-bindings.md` — the approved M2
  eligibility interpretation only.
- `.genesis/decisions/M2-freeze-boundary-exceptions.md` — this exception record only.
- `.genesis/decisions/decisions-manifest.md` — registration of the two M2 decision
  records only.

No wildcard, other path, production behavior, test behavior, dependency, milestone,
`PLAN.md`, or `DONE.html` change is authorized. This exception does not authorize
staging, committing, M2 closure, or work from another milestone.

## Consequences

- M2 reviewers may evaluate the five exact Genesis paths alongside the locked M2
  implementation boundary.
- These paths may contain only the scoped checkpoint evidence and decision/manifest
  records described above.
- No context-graph or product invariant is changed.
