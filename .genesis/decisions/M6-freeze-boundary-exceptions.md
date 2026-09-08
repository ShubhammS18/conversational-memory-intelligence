# M6 freeze-boundary exceptions

- Status: Accepted
- Date: 2026-09-08
- Scope: M6 only

The locked M6 PLAN boundary is extended by exactly these paths:

1. `src/conversational_memory/infrastructure/sqlite/repository.py`
   - implement the owner-scoped, idempotent expiration transition;
   - extend historical allowlisting and hydration to consistent persisted
     `expired` records.

2. `tests/unit/domain/test_historical_eligibility.py`
   - amend the completed-M5 eligibility truth table only for M6's approved
     persisted-expired historical case.

3. `tests/integration/test_historical_retrieval.py`
   - amend completed-M5 integration expectations only to prove the approved
     M6 expired-history extension remains owner-scoped and read-only.

4. `.genesis/decisions/M5-historical-retrieval-bindings.md`
   - add a prospective M6 compatibility clarification without rewriting M5's
     historical decision.

5. `.genesis/decisions/M6-trusted-clock-expiration-bindings.md`
   - record the mandatory M6 policy.

6. `.genesis/decisions/M6-freeze-boundary-exceptions.md`
   - record this exact exception set.

7. `.genesis/decisions/decisions-manifest.md`
   - register the two M6 decisions.

8. `.genesis/checkpoints/M6.md`
   - record mandatory M6 gate and iteration evidence.

9. `.genesis/checkpoints/CURRENT.md`
   - maintain the canonical rolling state.

These exceptions authorize only the stated M6 changes. They do not authorize:

- changes to any other completed-M5 test;
- migration or schema work;
- dependency changes;
- PLAN, DONE, context-graph, or wiki changes; or
- M7-M10 behavior.
