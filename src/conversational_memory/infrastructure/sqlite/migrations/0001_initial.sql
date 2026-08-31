CREATE TABLE memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK (memory_type IN ('preference', 'fact', 'decision', 'constraint')),
    provenance_authority TEXT NOT NULL CHECK (provenance_authority IN ('explicit_user', 'inferred')),
    source_type TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    source_event_at TEXT,
    created_at TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('active', 'superseded', 'expired')),
    indexing_state TEXT NOT NULL CHECK (indexing_state IN ('pending', 'indexed', 'failed')),
    indexing_error TEXT,
    subject TEXT,
    value_json TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    supersedes_json TEXT NOT NULL,
    superseded_by TEXT,
    UNIQUE (user_id, memory_id)
);

CREATE TABLE admission_idempotency (
    user_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    memory_id TEXT NOT NULL UNIQUE,
    PRIMARY KEY (user_id, idempotency_key),
    FOREIGN KEY (user_id, memory_id) REFERENCES memories(user_id, memory_id) ON DELETE CASCADE
);

CREATE TABLE memory_embeddings (
    memory_id TEXT PRIMARY KEY,
    embedding_blob BLOB NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL CHECK (embedding_dimension > 0),
    FOREIGN KEY (memory_id) REFERENCES memories(memory_id) ON DELETE CASCADE
);

CREATE TABLE memory_vector_mappings (
    vector_id INTEGER PRIMARY KEY AUTOINCREMENT CHECK (vector_id > 0),
    memory_id TEXT NOT NULL UNIQUE,
    FOREIGN KEY (memory_id) REFERENCES memories(memory_id) ON DELETE CASCADE
);
