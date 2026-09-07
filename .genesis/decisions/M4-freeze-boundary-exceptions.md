# M4 Freeze-Boundary Exceptions

- **Date:** 2026-09-06
- **Status:** Accepted
- **Phase / milestone:** M4 supersession and conflict handling

## Context

The locked M4 implementation boundary in `PLAN.md` covers production and test
paths but not the mandatory Genesis checkpoint, approved binding, exception, and
manifest records required to enter and verify the milestone.

## Decision

For M4 only, authorize exactly these additional paths for the stated purposes:

- `.genesis/checkpoints/CURRENT.md` — current M4 loop state only.
- `.genesis/checkpoints/M4.md` — mandatory M4 iteration and verification evidence.
- `.genesis/decisions/M4-supersession-and-conflict-bindings.md` — the approved M4
  interpretation only.
- `.genesis/decisions/M4-freeze-boundary-exceptions.md` — this exception record only.
- `.genesis/decisions/decisions-manifest.md` — registration of these two M4
  decisions only.

## Boundaries

These exceptions do not expand M4 behavior or authorize changes to `PLAN.md`,
`DONE.html`, `context-graph.json`, implementation notes, wiki content,
dependencies, or files outside the locked M4 implementation boundary. They do not
authorize M5–M10 behavior, staging, commits, pushes, review approval, or milestone
closure.
