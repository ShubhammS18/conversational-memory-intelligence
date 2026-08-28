# PLAN — conversational-memory-intelligence

The machine-parseable implementation plan. Mirrors the milestone table in `DONE.html` (DONE.html is the
human/visual view; this is the one loops read). Sliced so each milestone ships in one L1 BUILD pass.

> Slicing rule: a milestone must have (a) a single clear outcome, (b) an exact **demo command** that
> proves it, and (c) a freeze boundary of files it may touch. If you can't write the demo command,
> the milestone is too vague — split it.

---

## Brainstorm (G0.5 — completed before slicing milestones)

> Three fundamentally different approaches to the cognitive job. The selected approach is the approved
> implementation architecture; the alternatives remain recorded so the trade-off is explicit.

### Approach A — Direct Integrated Script

Build one synchronous Python service with concrete SQLite, FAISS, SentenceTransformer, and token-counting dependencies wired directly into the workflow. Keep admission, retrieval, ranking, lifecycle, and context logic in a small number of implementation modules without formal ports.

- Strengths:
  - Provides the shortest path from the approved prototypes to a runnable integrated pipeline.
  - Minimizes initial interface and composition code.
- Weaknesses:
  - Couples memory behavior to SQLite, FAISS, SentenceTransformers, and entry-point concerns.
  - Makes failure injection, adapter replacement, and isolated domain testing harder.

### Approach B — Local Modular Monolith with Ports and Adapters

Build one synchronous Python deployment organized into domain, application, infrastructure, composition, and entry-point areas. The application service coordinates the workflow through small interfaces, deterministic rules remain framework-independent, and concrete SQLite, FAISS, SentenceTransformer, and `tiktoken` adapters are connected at one composition point.

- Strengths:
  - Preserves explicit dependency boundaries and keeps deterministic memory policy independent of storage and model tools.
  - Supports unit, adapter, restart, recovery, and end-to-end testing without introducing distributed deployment.
- Weaknesses:
  - Requires more initial interface, mapping, composition, and architecture-test work than a direct pipeline.
  - SQLite/FAISS consistency, indexing state, locking, and recovery still require explicit application-level coordination.

### Approach C — Service-Separated or Event-Driven System

Admission, indexing, retrieval, and recovery would be separated into independently operated services or workers, potentially connected through network calls or durable queues.

- Strengths:
  - Components could be isolated and scaled independently.
  - Indexing and recovery could move to durable background workers.
- Weaknesses:
  - Introduces networking, queues, distributed consistency, deployment, and operational complexity unsupported by current requirements.
  - Makes local validation slower and expands the project beyond its approved evidence and scope.

### Chosen: Local Modular Monolith with Ports and Adapters

The modular-monolith approach was selected because it preserves the approved domain and infrastructure boundaries, supports deterministic safety and recovery testing, and keeps all components in one synchronous local deployment without introducing unnecessary service or distributed-system complexity.

---

## Milestones

### M1 — {{M1_NAME}}
- **Outcome:** {{M1_OUTCOME}}
- **Phase (swe-master):** {{M1_PHASE}}
- **Files / freeze boundary:** `{{M1_FILES}}`
- **Demo command:** `{{M1_DEMO}}`
- **Success criteria:** {{M1_SUCCESS}}
- **Loops:** L1, L4
- **Skills:** canon + tdd + {{M1_SKILLS}}
- **Token budget:** 50000

### M2 — {{M2_NAME}}
- **Outcome:** {{M2_OUTCOME}}
- **Phase:** {{M2_PHASE}}
- **Files:** `{{M2_FILES}}`
- **Demo command:** `{{M2_DEMO}}`
- **Success criteria:** {{M2_SUCCESS}}
- **Loops:** L1, L3 (research), L4
- **Skills:** canon + tdd + {{M2_SKILLS}}
- **Token budget:** 50000

<!-- duplicate the block per milestone -->

---

## Progress (loops append here on milestone completion — newest last)

- _(none yet — first loop fills this)_
