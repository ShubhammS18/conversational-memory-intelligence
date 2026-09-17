# M9 privacy-safe observability bindings

- Status: Accepted
- Date: 2026-09-10
- Scope: M9 only

## Purpose and authority

M9 adds local, structured observability sufficient to trace approved memory
decisions and failures without exposing memory, query, authentication, or secret
content.

Application-owned immutable event contracts define the event vocabulary and
typed fields. Infrastructure may serialize and deliver those events but may not
add fields, infer reasons, inspect request payloads, or change application
results.

M9 adds no remote telemetry, production monitoring service, persistent audit
database, distributed tracing protocol, compliance claim, or M10 evaluation.

## Activation and injection

The normal M9 local composition explicitly receives:

- an `EventSinkPort`;
- a `TelemetryClockPort`; and
- an `HmacUserPseudonymizer` configured with an explicit secret key.

There is no environment lookup, global singleton, implicit production sink,
random per-request key, or hidden fallback. Missing or invalid observability
configuration prevents the observed local service from being exposed and uses
`ConfigurationError("invalid_observability_configuration")`.

The telemetry clock is separate from the trusted lifecycle clock. Observability
must not add lifecycle-clock reads or alter the one-clock rules established by
M6.

## Exact event names

Terminal event names are:

- `admission_completed`
- `retrieval_completed`
- `forgetting_completed`
- `recovery_completed`
- `startup_ready`
- `startup_degraded`
- `startup_unavailable`

Internal event names are:

- `storage_completed`
- `storage_failed`
- `indexing_completed`
- `indexing_failed`
- `admission_retry`
- `configuration_failed`

No other event name is valid in M9.

## Terminal and internal event rules

Under correctly configured, functioning observability components:

- each admission invocation emits exactly one `admission_completed`;
- each retrieval invocation emits exactly one `retrieval_completed`;
- each forgetting invocation emits exactly one `forgetting_completed`;
- each direct recovery invocation emits exactly one `recovery_completed`; and
- each composed startup emits exactly one of `startup_ready`,
  `startup_degraded`, or `startup_unavailable`.

A terminal event is emitted for both returned results and raised ordinary
application exceptions. It records only an approved outcome and reason code.
The original result or exception remains unchanged.

Internal events are emitted only for stages actually attempted:

- a storage or indexing stage emits exactly one corresponding completed or
  failed event;
- `admission_retry` is emitted once only after an exact stored idempotency match
  enters an existing retry/replay path;
- `configuration_failed` precedes the applicable operation terminal event, or
  `startup_unavailable`, when an approved configuration mismatch is detected.

Internal events precede their operation’s terminal event. A fail-fast rejection
may have no storage or indexing event. Concurrent operations may interleave,
but event order within one invocation is synchronous and deterministic.

Recovery during startup emits `recovery_completed` before the startup terminal
event. Direct recovery emits isolated `configuration_failed` before
`recovery_completed` for `unavailable_embedding_configuration` or a raised
configuration mismatch. Composed startup does not offer that configuration
event a second time, even if delivery failed; later distinct constructor
configuration failures remain separately observable.
Event emission never creates or changes memory, mapping, lifecycle,
forgetting, recovery, or readiness state.

## Exact internal stages

The closed `stage` vocabulary is:

- `idempotency_lookup`
- `supersession_lookup`
- `persist_pending`
- `mark_pending`
- `mark_failed`
- `mark_indexed`
- `acknowledge_supersession`
- `expiration_transition`
- `current_allowlist`
- `historical_allowlist`
- `current_hydration`
- `historical_hydration`
- `forgetting_lookup`
- `begin_forgetting`
- `acknowledge_forgetting`
- `recovery_inventory`
- `adopt_forgetting_vector`
- `embedding`
- `vector_add`
- `vector_search`
- `vector_remove`
- `generation_reconcile`

## Typed event field allowlist

Every immutable event has these required fields:

- `schema_version`: literal integer `1`;
- `event_name`: one approved event name;
- `occurred_at`: timezone-aware UTC `datetime`;
- `duration_ms`: non-boolean integer greater than or equal to zero;
- `outcome`: one approved outcome; and
- `reason_code`: one approved reason code.

The only optional fields are:

- `request_id`: exact trusted request identifier with non-whitespace content,
  preserving every already-valid value unchanged, including surrounding whitespace;
- `user_ref`: HMAC user pseudonym;
- `memory_id`: exact authoritative opaque memory identifier;
- `memory_ids`: ordered tuple of unique authoritative memory identifiers;
- `stage`: one approved internal stage;
- `retry_count`: integer `0` or `1`;
- `candidate_count`: non-negative integer;
- `returned_count`: non-negative integer;
- `token_budget`: non-negative integer;
- `tokens_used`: non-negative integer;
- `index_vector_count`: non-negative integer;
- `embedding_model`: exact non-empty configured model identifier;
- `vector_dimension`: positive non-boolean integer;
- `readiness`: `ready`, `degraded`, or `unavailable`;
- `rebuilt`: strict boolean;
- `orphan_vectors_removed`: non-negative integer;
- `pending_count`: non-negative integer;
- `failed_count`: non-negative integer; and
- `cleanup_pending_count`: non-negative integer.

No `extra`, arbitrary metadata mapping, free-text message, exception text, or
caller-provided field is permitted.

Admission, retrieval, and forgetting terminal events require `request_id` and
`user_ref`. A forgetting `not_found` event omits `memory_id`, preserving the
same opacity for nonexistent and cross-owner targets.

Retrieval terminal events require candidate, returned, budget, token-use, and
selected-memory-ID fields. Recovery and startup events require readiness,
index/model metadata, rebuild state, orphan count, and unresolved-state counts.
Storage and indexing internal events require `stage`; user-scoped stages also
require `request_id` and `user_ref`.

## Exact outcomes

The closed `outcome` vocabulary is:

- `accepted`
- `rejected`
- `failed`
- `memories_selected`
- `no_eligible_memory`
- `no_relevant_memory`
- `budget_excluded`
- `forgotten`
- `cleanup_pending`
- `not_found`
- `ready`
- `degraded`
- `unavailable`
- `succeeded`
- `retry_started`

## Exact reason codes

The closed general reason vocabulary is:

- `accepted_and_indexed`
- `sensitive_admission_rejected`
- `invalid_admission_request`
- `idempotency_key_conflict`
- `invalid_supersession_target`
- `indexing_failed`
- `indexing_acknowledgement_failed`
- `supersession_acknowledgement_failed`
- `storage_failure`
- `configuration_mismatch`
- `memories_selected`
- `no_eligible_memory`
- `no_relevant_memory`
- `budget_excluded`
- `memory_not_found`
- `forgotten`
- `already_forgotten`
- `physical_cleanup_pending`
- `physical_cleanup_identity_pending`
- `operation_completed`
- `operation_failed`
- `retry_started`

Recovery and startup may additionally use exactly the M8 reasons:

- `ready_existing_generation`
- `ready_rebuilt_generation`
- `degraded_excluded_work_pending`
- `unavailable_sqlite_integrity`
- `unavailable_schema_or_migration`
- `unavailable_authoritative_identity`
- `unavailable_embedding_configuration`
- `unavailable_rebuild`
- `unavailable_publication`
- `unavailable_post_publication_verification`

Existing `sensitive_credential` and `sensitive_check_unavailable` results both
map to `sensitive_admission_rejected`. No sensitive-rejection detail,
credential class, matched pattern, or scanner exception is emitted.

Unknown exception messages and infrastructure error strings map only to
`storage_failure`, `indexing_failed`, `configuration_mismatch`, or
`operation_failed`, as applicable. They never become event data.

## HMAC user pseudonymization

Raw `user_id` is never placed in an event or passed to an event sink.

The pseudonymizer requires an explicitly injected key of at least 32 bytes and
computes:

`"u_" + HMAC-SHA256(key, b"cmi-observability-user-v1\\0" + user_id.encode("utf-8")).hexdigest()`

The result is therefore `u_` followed by 64 lowercase hexadecimal characters.
The same key and exact user ID produce the same pseudonym; different users or
keys produce different pseudonyms. Key rotation deliberately changes
pseudonyms. M9 persists no pseudonym mapping.

The HMAC key is never emitted, serialized, logged, read from request data, or
given a built-in value.

## Timing

`TelemetryClockPort` supplies an aware UTC wall-clock value and a monotonic
nanosecond value. Each timed boundary reads monotonic time immediately before
and after the attempted operation.

`occurred_at` is the validated UTC wall-clock value read at event completion.
`duration_ms` is `(end_ns - start_ns) // 1_000_000`. Negative, boolean, naive,
non-UTC, or otherwise invalid telemetry-clock values make that event emission
fail safely without affecting the primary operation.

Caller event timestamps and the lifecycle clock are never used for telemetry
timing.

## Serialization

The local JSON-lines serializer:

- validates the immutable event before serialization;
- includes only the applicable allowlisted non-null fields;
- converts enums to their exact string values;
- serializes UTC time with six fractional digits and a trailing `Z`;
- uses UTF-8, sorted keys, separators `(",",":")`, and `ensure_ascii=False`;
- rejects NaN and infinity with `allow_nan=False`; and
- writes exactly one JSON object followed by one newline per event.

Serialization is deterministic. Sets, arbitrary objects, byte strings, raw
exceptions, tracebacks, and recursively supplied metadata are invalid.

## Sink-failure non-interference

Each event is offered to the injected sink at most once. An ordinary exception
from pseudonymization, timing, validation, serialization, or the sink is caught
at the observability boundary.

Such a failure:

- does not change the primary result or exception;
- does not roll back or repeat application work;
- does not change retry, readiness, lifecycle, or persistence behavior;
- does not invoke a fallback logger or recursively emit another event; and
- does not expose sensitive data through an error message.

M9 makes no durable-delivery guarantee. Process-control `BaseException`
subclasses are not converted into application success.

## Forbidden content and privacy scans

The following keys are forbidden:

- `user_id`
- `conversation_id`
- `turn_id`
- `idempotency_key`
- `request_fingerprint`
- `source_event_at`
- `content`
- `query`
- `subject`
- `value`
- `embedding`
- `embedding_values`
- `authorization`
- `authentication`
- `authorization_header`
- `cookie`
- `password`
- `passcode`
- `api_key`
- `secret_key`
- `access_token`
- `refresh_token`
- `retryable_error`
- `exception`
- `traceback`
- `stack_trace`
- `message`

Redaction is achieved by constructing events exclusively from typed,
allowlisted decision metadata. M9 does not serialize a broad object and then
attempt heuristic scrubbing.

Tests must inject unique sentinels into memory content, query, structured value,
subject, credential forms, raw user identity, HMAC key, and exception messages.
They recursively scan captured event objects and serialized UTF-8 bytes,
confirm every key belongs to the exact allowlist, and confirm no sentinel, raw
user ID, HMAC key, exception message, traceback, or forbidden key appears.

## Required operation coverage

Tests must cover:

- accepted, rejected, failed, replayed, and retried admission;
- storage and indexing success and failure;
- all four retrieval outcomes and exact token/count metadata;
- current and historical retrieval without query or memory disclosure;
- forgotten, cleanup-pending, repeated, nonexistent, and cross-owner forgetting;
- ready, rebuilt, degraded, and unavailable recovery/startup;
- configuration mismatch;
- deterministic per-invocation ordering and exactly one terminal event;
- concurrent event correlation by request identifier without cross-user data;
- sink, timer, pseudonymizer, validation, and serialization failure
  non-interference;
- sensitive-rejection reason collapse;
- exact JSON-lines serialization and complete privacy scans; and
- `demo-observability` using real local composition and a capture sink.

## Frozen exclusions

M9 adds no raw-content logging, query logging, embedding logging, authentication
logging, free-form errors, remote telemetry, production collector, monitoring
backend, durable audit database, alerting, sampling, distributed tracing,
compliance claim, workload evaluation, or M10 behavior.
