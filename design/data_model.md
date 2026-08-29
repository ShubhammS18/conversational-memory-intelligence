# D4 — Memory Data Model

## Purpose

This document defines the durable memory representation used by the conversational memory system.

The model is intentionally small. It stores the information required for retrieval, user isolation, conflict resolution, temporal reasoning, and lifecycle management without turning the memory system into a full graph model.

The model represents a memory as a natural-language statement plus structured attributes needed by downstream decisions.

---

## 1. Memory record

Each durable memory is represented by the following logical record:

| Field | Type | Required | Description |
|---|---|---:|---|
| `memory_id` | string | Yes | Unique logical identifier for the memory. D4 prototypes sometimes used it directly as a FAISS ID; the integrated package uses a separate signed-int64 `vector_id` mapping. |
| `user_id` | string | Yes | Owner/tenant identity. Used as a hard retrieval boundary. |
| `content` | string | Yes | Natural-language statement representing the memory. |
| `embedding` | vector<float32> | Yes | Semantic representation used for candidate retrieval. Dimension is 768 in the current implementation. |
| `type` | string | Yes | Controlled memory category determined by the memory extraction/admission layer. |
| `provenance` | object | Yes | Evidence/source information, including whether the memory came from an explicit user statement or inference. |
| `created_at` | timestamp | Yes | Time at which the memory record was created, supplied by the trusted UTC clock rather than caller provenance. |
| `lifecycle_status` | enum | Yes | Current lifecycle state of the memory. |
| `subject` | string | No | Structured subject when the memory naturally has one. |
| `value` | string/object | No | Structured value when useful for the memory. |
| `valid_from` | timestamp | No | Time from which the memory is known to be valid. |
| `valid_until` | timestamp | No | Explicit end of the memory's validity period. |
| `supersedes` | list[string] | No | Memory IDs explicitly replaced by this memory. |
| `superseded_by` | string | No | Memory ID that explicitly replaced this memory. |

---

## 2. Lifecycle status

`lifecycle_status` uses the minimal lifecycle model established in D4.

Allowed values:

```text
active
superseded
expired 
```

---


## 3. Provenance

Each memory records how it was obtained.

Supported sources:

```
explicit
inferred 
```

Explicit user statements have higher authority than inferred information
when resolving conflicts.

Example:


```
{ "source": "explicit" }
```

---

## 4. Relationships

The primary memory relationship is supersession.

Example:

```
M2 supersedes M1

represented as:

{
  "memory_id": "M2",
  "supersedes": ["M1"]
}

and:

{
  "memory_id": "M1",
  "superseded_by": "M2"
}
```

Supersession must identify the specific memory being replaced.

---

## 5. Temporal Fields

Temporal information is optional.

A memory may contain:

valid_from
valid_until

These fields allow the system to represent memories that are valid only
during a particular period.

When both are present:

valid_from <= valid_until 

--- 

## 6. Embedding

The current implementation uses:

```
Dimension: 768
Data type: float32
```

The embedding is used for semantic retrieval.

The embedding does not determine:

ownership;
lifecycle state;
provenance authority;
forgetting.

Those properties are represented separately in the memory metadata.

### Integrated-package persistence fields

The preceding record is the D4 logical model. The integrated package additionally persists:

| Field | Type | Description |
|---|---|---|
| `embedding_model` | string | Model name/version used to produce the stored embedding. |
| `embedding_dimension` | integer | Stored vector dimension, validated against configuration and FAISS metadata. |
| `indexing_state` | enum | Derived-index progress, separate from lifecycle state. |
| `idempotency_key` | string | Normalized key unique within the trusted owner scope. |
| `request_fingerprint` | string | SHA-256 digest used to distinguish an identical retry from a conflicting key reuse. |

Allowed indexing states are:

```text
pending
indexed
failed
```

SQLite also stores a one-to-one mapping between the string `memory_id`
and a positive signed-int64 `vector_id`. FAISS contains the `vector_id`;
the domain identifier is never coerced into an integer.

The exact integrated-package normalization, state-transition, and
persistence rules are defined by
`.genesis/decisions/M1-implementation-bindings.md`.

---

## 7. Example Memory

```{
  "memory_id": "mem_002",
  "user_id": "user_001",
  "content": "I currently prefer Qdrant for vector search.",
  "embedding": "<768-dimensional float32 vector>",
  "type": "preference",
  "provenance": {
    "source": "explicit"
  },
  "created_at": "2026-08-12T10:00:00Z",
  "lifecycle_status": "active",
  "subject": "vector database",
  "value": "Qdrant",
  "valid_from": null,
  "valid_until": null,
  "supersedes": ["mem_001"],
  "superseded_by": null}
```


---

## 8. Core Invariants

1. Every memory has a unique memory_id.
2. Every memory belongs to exactly one user_id.
3. Every durable memory has an associated embedding.
4. Supersession identifies the specific memory being replaced.
5. Superseded memories are not automatically treated as current.
6. Forgotten memories cannot be returned by retrieval.
7. Explicit provenance has higher authority than inferred provenance.
8. Historical state is preserved when a memory is superseded.
9. The string `memory_id`, authoritative SQLite record, signed-int64 `vector_id`, and FAISS entry must remain consistently mapped.
10. Caller timestamps are provenance only; creation time and default lifecycle time come from the trusted UTC clock.
