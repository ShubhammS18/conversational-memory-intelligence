# D4 — API Contracts

## Purpose

This document defines the logical API contracts for the conversational memory
system.

The contracts describe the operations required by the D4 design:

- store a memory;
- retrieve relevant memories;
- retrieve historical memories;
- forget a memory.

Authentication, transport, deployment, and production API infrastructure are
outside the current D4 scope.

### Integrated-package boundary

The JSON examples below are retained as D4 logical-contract evidence.
They are not the concrete identity-transport contract for the later
integrated package.

The first integrated slice exposes synchronous Python operations:

```text
admit(context: RequestContext, request: AdmissionRequest) -> AdmissionResult
retrieve(context: RequestContext, request: RetrievalRequest) -> RetrievalResult
```

The authoritative `user_id` and request ID come from the separate trusted
`RequestContext`. Request payloads do not contain an authoritative
`user_id`. Admission also requires an idempotency key whose uniqueness is
scoped by the trusted `(user_id, idempotency_key)` database key. Exact
normalization and conflict behavior are defined in
`.genesis/decisions/M1-implementation-bindings.md`.

---

## 1. Store Memory

### Operation

Store an admitted durable memory for a user.

### Request

```json
{
  "user_id": "user_001",
  "content": "I currently prefer Qdrant for vector search.",
  "type": "preference",
  "provenance": {
    "source": "explicit",
    "conversation_id": "conv_123"
  },
  "subject": "vector database",
  "value": "Qdrant",
  "valid_from": null,
  "valid_until": null
}
```

### Response

```json
{
  "memory_id": "mem_002",
  "status": "stored"
}
```

### Rules

- The memory must pass admission/privacy checks before durable storage.
- Sensitive information rejected by admission must not be embedded, stored,
  mapped, or added to FAISS.
- In the integrated package, trusted `RequestContext.user_id` identifies the owner.
- A 768-dimensional float32 embedding is generated for durable memory.
- Conflict resolution determines whether the new memory supersedes an
  existing memory.

---

## 2. Retrieve Current Memories

### Operation

Retrieve memories relevant to the current user query.

### Request

```json
{
  "user_id": "user_001",
  "query": "What vector database do I currently prefer?",
  "top_k": 5
}
```

### Response

```json
{
  "memories": [
    {
      "memory_id": "mem_002",
      "content": "I currently prefer Qdrant for vector search.",
      "score": 0.91
    }
  ]
}
```

### Rules

- Retrieval is always scoped to the requesting `user_id`.
- Unauthorized memory IDs must not enter the candidate retrieval set.
- Forgotten memories must not be returned.
- Superseded memories are excluded from current-state retrieval.
- Conflict resolution is applied before final context construction.
- The result may contain no memories when no candidate reaches the relevance
  threshold.
- `top_k` limits the number of returned candidates before final context
  construction.

---

## 3. Retrieve Historical Memories

### Operation

Retrieve memories relevant to a historical query.

### Request

```json
{
  "user_id": "user_001",
  "query": "What vector database did I use before Qdrant?",
  "top_k": 5
}
```

### Response

```json
{
  "memories": [
    {
      "memory_id": "mem_001",
      "content": "I prefer FAISS for vector search.",
      "score": 0.88,
      "lifecycle_status": "superseded"
    }
  ]
}
```

### Rules

- Retrieval remains scoped to the requesting `user_id`.
- Superseded memories may be returned when the query explicitly asks for
  historical state.
- Forgotten memories remain excluded.
- Historical retrieval must not cause a superseded memory to become current.
- The response identifies the lifecycle state of returned historical
  memories.

---

## 4. Forget Memory

### Operation

Explicitly forget a memory belonging to the requesting user.

### Request

```json
{
  "user_id": "user_001",
  "memory_id": "mem_002"
}
```

### Response

```json
{
  "memory_id": "mem_002",
  "status": "forgotten"
}
```

### Rules

- The requesting user must be authorized to forget the specified memory.
- An unauthorized deletion request must not modify the memory.
- Forgetting is idempotent.
- A forgotten memory must not be retrievable.
- Physical FAISS deletion failure must not make a forgotten memory
  retrievable again.
- Forgetting a newer memory must not automatically reactivate an older
  superseded memory.

---

## 5. No-Memory Response

When no stored memory is sufficiently relevant:

```json
{
  "memories": []
}
```

The system must be able to return no memory rather than forcing an
unrelated memory into the response.

---

## 6. Error Conditions

| Condition | Expected behavior |
|---|---|
| Unauthorized memory access | Reject the operation |
| Unauthorized deletion | Reject without modifying memory |
| Memory does not exist | Return a not-found result or idempotent success for an already-forgotten memory |
| Sensitive information during admission | Reject before durable storage |
| No sufficiently relevant memory | Return an empty memory result |
| Storage/index failure | Return an error without violating authorization or forgetting guarantees |

---

## 7. Security Boundary

`user_id` is an authorization boundary, not merely a search filter. The D4
examples show it inline to express the logical operation, while the integrated
package obtains it only from `RequestContext`. A `user_id` found inside a
payload or conversation cannot override that context.

The retrieval flow is conceptually:

```text
request
   ↓
authenticate/authorize user
   ↓
resolve authorized memory IDs
   ↓
scoped retrieval
   ↓
ranking/conflict resolution
   ↓
context construction
```

Semantic similarity must not override user authorization.

---

## 8. Context Construction Boundary

Retrieval APIs return memory candidates.

Final prompt/context construction is a separate system operation.

The context-construction layer:

- applies the hard token budget;
- removes unnecessary historical pollution;
- preserves the most relevant current memory;
- produces the memory context supplied to the downstream model.

The API contract does not expose the full prompt construction logic.

---

## 9. Current Scope

These contracts define the logical interfaces required by the D4 design.

The following are intentionally not specified here:

- HTTP framework;
- authentication provider;
- deployment infrastructure;
- API gateway;
- rate limiting;
- production service discovery;
- database transaction implementation.

Those decisions belong to implementation/deployment work rather than the
current D4 design.
