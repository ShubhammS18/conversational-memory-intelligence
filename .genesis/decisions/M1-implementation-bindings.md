# M1 implementation bindings

- **Decision ID:** M1-implementation-bindings
- **Status:** Accepted
- **Date:** 2026-08-29
- **Scope:** The integrated package built in M1 only
- **Historical evidence:** D4 prototypes remain characterization evidence. This record resolves later package-level choices and changes no prototype result.

## 1. Packaging and verified environment

M1 uses a `src` layout with `setuptools.build_meta`. `pyproject.toml` is authoritative for the integrated package and declares:

- Python: `>=3.12,<3.13`
- runtime: `pydantic>=2,<3`, `sentence-transformers>=5,<6`, `faiss-cpu>=1.15,<2`, `numpy>=1.26,<3`, and `tiktoken>=0.13,<1`
- `dev` extra: `pytest>=8,<10`, `ruff>=0.9,<1`, and `mypy>=1.10,<2`

The supported Python range is a deliberately narrow M1 verification policy because this repository currently has evidence for Python 3.12 only. It is not a claim that the package is incompatible with other Python versions, and it is not inferred merely from whatever happens to be installed. Broader support requires a reviewed declaration and passing tests on each added version.

Read-only preflight observed the active interpreter at `.venv/Scripts/python.exe` as CPython 3.12.10. It observed `sentence-transformers==5.7.0`, `faiss-cpu==1.15.0`, `numpy==2.5.1`, `tiktoken==0.13.0`, and `setuptools==83.0.0`. It did not find Pydantic, pytest, Ruff, or mypy. These are workstation observations, not compatibility proof or approved pins. The declared ranges become supported only when editable installation and the M1 lint, type, adapter, and real-component integration checks pass together. M1 does not claim bit-for-bit dependency reproducibility and adds no lock file.

The existing `requirements.txt` remains unchanged legacy/prototype history. It is not authoritative for the integrated package and must not override `pyproject.toml`.

### M1 SentenceTransformer binding

The canonical load name is `sentence-transformers/all-mpnet-base-v2` at immutable revision `e8c3b32edf5434bc2275fc9bab85f82640a19130`. The persisted model ID is `sentence-transformers/all-mpnet-base-v2@e8c3b32edf5434bc2275fc9bab85f82640a19130`; SQLite embedding rows, `Embedding.model_id`, configured FAISS metadata, and FAISS startup validation must use that exact string. The shorter existing name `all-mpnet-base-v2` remains descriptive project terminology and is not the persisted identity. New real-model stores use the revision-qualified identity. Existing stores with another identity must fail closed and must never be migrated or relabeled silently.

M1 loads the model on CPU only and only from the approved cache root supplied as configuration. For the current M1 workstation proof that root is `C:\Users\Asus\.cache\huggingface\hub`. Construct SentenceTransformers with the canonical load name, immutable revision, configured cache root, `device="cpu"`, and `local_files_only=True`. Missing or incomplete cached files must fail startup without downloading, selecting another revision, or using another model, cache, or device.

Encode one normalized input with `convert_to_numpy=True`, `normalize_embeddings=True`, `precision="float32"`, `device="cpu"`, and progress output disabled. Convert the result explicitly to float32 and require one finite vector with shape `(768,)` and nonzero L2 norm. The returned float32 vector must be L2-normalized; validate its norm with relative tolerance `1e-5` and absolute tolerance `1e-6`.

An incompatible loaded dimension raises `ConfigurationMismatchError`. An ordinary model/cache loading failure raises `ServiceUnavailableError`. An ordinary encoding failure or malformed, non-finite, incorrectly shaped, or non-normalized output raises `IndexingError`. Do not catch `KeyboardInterrupt` or `SystemExit`, and do not fall back to another model, revision, cache, device, or unnormalized output.

M1 completion explicitly requires `tests/integration/test_real_model_first_slice.py::test_real_model_restart_owner_scope_and_bounded_context`, marked `real_model`. It must use the cached immutable model, real SQLite, real persisted FAISS, and real `cl100k_base`; a missing cache is a failed prerequisite, not a skipped or mocked pass. Routine tests exclude the marker and the real-model gate runs it explicitly.

## 2. Trusted identity and idempotency

The public boundary is `admit(context, request)`. `RequestContext.user_id` is established by the caller and is the only authoritative identity. A `user_id` in content or request data is untrusted text. The body excludes `user_id`; SQLite nevertheless uses the trusted context value in the unique database key `(user_id, idempotency_key)` and in every owner-scoped operation.

### Key normalization

The idempotency key is a Unicode string. Normalize it to NFC, trim leading and trailing Unicode whitespace, preserve case and interior characters, and reject it if empty. The normalized value is the database key.

### Request fingerprint

Build a fixed-key object containing exactly:

`conversation_id`, `turn_id`, `content`, `memory_type`, `subject`, `value`, `source_type`, `source_event_at`, `valid_from`, and `valid_until`.

Normalize caller values as follows:

1. Normalize every string and object key to Unicode NFC.
2. Convert CRLF and CR in strings to LF and trim leading and trailing Unicode whitespace. Do not case-fold or collapse interior whitespace.
3. Required strings that become empty are invalid. Absent optional values are JSON `null`.
4. Recursively normalize arrays and objects. Reject an object if two keys collide after normalization.
5. Reject unsupported JSON types, non-finite numbers, and naive timestamps.
6. Encode timestamps as timezone-aware UTC RFC 3339 with six fractional digits and `Z`.
7. Serialize with Python 3.12 `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)`.
8. Encode that JSON as UTF-8 and store the lowercase hex digest from `sha256(bytes)`.

The fingerprint excludes the normalized idempotency key itself, `RequestContext.user_id`, `RequestContext.request_id`, trusted-clock `created_at`, a defaulted `valid_from`, generated `memory_id` and `vector_id`, embeddings, scores, and derived lifecycle or indexing state. Caller timestamps are provenance/source-event information only; trusted lifecycle defaults use the UTC clock.

On an existing `(user_id, idempotency_key)`:

- equal fingerprint plus `indexed`: return the existing result;
- equal fingerprint plus `pending` or `failed`: retry only indexing from the stored embedding and stable vector ID;
- different fingerprint: raise `ValidationError` with reason `idempotency_key_conflict` before embedding or mutation;
- the same key under another trusted user scope is independent.

Tests must prove repeat determinism, newline/Unicode/timestamp normalization, field sensitivity, exclusions, cross-user independence, conflict rejection, and zero mutation on conflict.

## 3. Minimum deterministic credential policy

This is a conservative minimum credential policy, not general secret, PII, or compliance detection. It scans every normalized caller-supplied string that would be stored, including string leaves inside `value`, before embedding.

Using Python `re`, reject on any of these patterns:

```text
PRIVATE_KEY = r"-----BEGIN[ \t]+(?:[A-Z0-9]+[ \t]+)*PRIVATE[ \t]+KEY(?:[ \t]+BLOCK)?-----"
LABELED_CREDENTIAL = r"""(?ix)
(?<![\w-])
(?:password|passcode|api[ _-]*key|access[ _-]*token|refresh[ _-]*token|secret[ _-]*key)
[ \t]*(?:[:=]|\bis\b)[ \t]*
(?:"[^"\r\n]{4,}"|'[^'\r\n]{4,}'|[^\s,;]{4,})
"""
OPENAI_STYLE_KEY = r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9][A-Za-z0-9_-]{7,}(?![A-Za-z0-9_-])"
```

Apply `re.IGNORECASE` to `PRIVATE_KEY`; the labeled expression carries its flags. `OPENAI_STYLE_KEY` is case-sensitive.

Required positive cases include PEM headers for generic, RSA, EC, OpenSSH, and PGP private keys; `password=hunter2`; `passcode: 482913`; `My API key is sk-example-secret`; labeled access and refresh tokens; labeled secret keys; and an unlabeled `sk-example-secret`.

Required near-miss negatives include `BEGIN PUBLIC KEY`, `private key rotation`, `I use a password manager`, `rotate the API key`, `access token budgeting`, `secret key rotation policy`, bare `sk-`, and `sk-short`.

A match returns normal `AdmissionResult(decision="rejected", reason="sensitive_credential")`. If normalization, traversal, or detector initialization cannot complete, fail closed with `reason="sensitive_check_unavailable"`. Either rejection occurs before embedding and produces no SQLite row, vector mapping, temporary or final FAISS file, or in-memory FAISS mutation.

## 4. Stable identity and SQLite state

`memory_id` is an opaque stable string domain identifier. SQLite allocates a separate positive signed-int64 `vector_id` and enforces one-to-one uniqueness for both identifiers. FAISS stores only `vector_id`; adapters translate through owner-scoped SQLite mappings.

Indexing state is separate from lifecycle state:

- `pending`: the typed row, float32 embedding BLOB, model, dimension, and vector mapping committed, but durable FAISS success is not yet acknowledged;
- `indexed`: the final FAISS generation was durably written and verified, then SQLite acknowledged it; only this state is M1-retrievable;
- `failed`: an indexing attempt failed and recorded a retryable error; it is not retrievable.

## 5. FAISS generation protocol

The configured index directory contains exactly these selected final files:

- `memory.faiss`
- `memory.faiss.meta.json`

A save with lowercase UUIDv4 generation `G` uses:

- `memory.faiss.G.tmp`
- `memory.faiss.meta.G.json.tmp`

Metadata is canonical UTF-8 JSON with no trailing newline, using sorted keys, compact separators, and `allow_nan=False`. It contains exactly `format_version`, `generation_id`, `embedding_model`, `vector_dimension`, `index_kind`, `vector_count`, `vector_ids_sha256`, and `index_sha256`. M1 uses format version 1 and `IndexIDMap2(IndexFlatIP)`. `vector_ids_sha256` hashes the concatenation of sorted vector IDs encoded as little-endian signed int64; `index_sha256` hashes the exact FAISS temporary-file bytes.

For every add or retry:

1. Clone the verified in-memory index.
2. Remove the stable `vector_id` from the clone, then add it once with `add_with_ids`.
3. Write and file-sync the FAISS temporary file; compute its checksum.
4. Write and file-sync canonical temporary metadata.
5. Reopen both temporaries; verify schema, generation, model, dimension, kind, count, ID checksum, index checksum, loadability, and the expected ID.
6. In the same directory, `os.replace` the FAISS final first and metadata final second.
7. Reopen and verify both finals again before swapping the process's in-memory index.
8. Only then acknowledge `indexed` in SQLite.

This is a two-file replacement protocol, not an atomic cross-file transaction. File sync and same-directory replacement provide process-restart persistence evidence; M1 makes no unsupported power-loss guarantee. An interruption before either replacement leaves the previous finals selectable. An interruption between replacements creates a checksum/generation mismatch and startup fails closed. An interruption after both replacements but before SQLite acknowledgement leaves the row non-retrievable; retry uses the same vector ID and copy-on-write replacement, so it cannot add a duplicate.

Startup selects only the two fixed final names. It ignores temporary names. After valid finals are verified, stale temporary files may be deleted; cleanup failure is non-fatal. If finals are absent for a new empty SQLite database, startup may create and verify an empty generation. Model, dimension, checksum, index kind, and mapping mismatches raise `ConfigurationMismatchError` or `ServiceUnavailableError`. M1 does not rebuild, choose a temporary generation, remove orphans, or reconcile arbitrary mismatches; those are M8. A valid extra vector mapped to a SQLite `pending` or `failed` row is tolerated but remains excluded by the authorized-ID selector.

## 6. Admission transaction and result boundaries

Admissions and retries use the approved process write lock.

1. Validate and canonicalize; resolve idempotency before expensive work.
2. Apply deterministic admission and credential rules.
3. Generate the embedding before any SQLite insert.
4. In SQLite transaction A, insert the typed record, embedding metadata/BLOB, stable mapping, fingerprint, and `pending` state.
5. Execute the copy-on-write FAISS generation protocol.
6. In SQLite transaction B, change that row to `indexed`.

If embedding or transaction A fails, no durable memory or FAISS mutation exists. If FAISS fails before verified final replacement, record `failed` in SQLite when possible; if that error update fails, the committed row remains `pending`. Return the memory ID, `retrievable=false`, a retryable indexing error, and never full success. If failure occurs after verified FAISS finals but before transaction B commits, leave the row `pending`, return the same partial result, and exclude it from retrieval. A retry changes `failed` to `pending`, reuses the stored embedding and vector ID, and ends in exactly one `indexed` row/vector or another non-retrievable failure.

Full success means transaction B committed and returns `indexing_state="indexed"` and `retrievable=true`. Retrieval obtains eligible vector IDs only from owner-scoped SQLite rows marked `indexed`; similarity cannot expand that set.

## 7. Exact M1 context serialization

M1 uses `tiktoken.get_encoding("cl100k_base")`. The caller supplies only the memory-context allowance.

Serialize one memory exactly as:

```text
Memory <memory_id>:
<content>
```

Use the literal ASCII label, one LF after the colon, normalized content unchanged, two LFs between blocks, and no leading or trailing separator. The empty context is `""` and uses zero tokens.

Process the deterministically ranked list in order. For each candidate, construct the entire prospective context and count `len(encoding.encode(prospective_context, disallowed_special=()))`, thereby treating special-token-looking memory text as ordinary text while counting labels, separators, formatting, and cross-boundary tokenization. Include the complete block only when the prospective count is at most the allowance. Otherwise record `budget_exceeded` and continue to later smaller candidates. Never truncate memory content. Report tokenizer `cl100k_base`, allowance, exact final count, included IDs, and exclusions. Missing or incompatible token configuration is a `ValidationError` with no fallback tokenizer.

## 8. Required proof

Unit and adapter tests cover canonicalization, conflict/no-mutation, every credential positive and negative, state transitions, stable ID translation, duplicate prevention, serializer determinism, exact boundary budgets, file metadata/checksums, replacement interruptions, and fail-closed startup.

The real M1 integration proof uses on-disk SQLite, persisted FAISS, `all-mpnet-base-v2`, and `cl100k_base`; proves success, another-user exclusion, identical retry, failure state, ordinary successful restart, stable mapping, and bounded complete-memory output. It does not implement M2-M10 lifecycle, threshold, forgetting, general reconciliation, observability, or evaluation behavior.
