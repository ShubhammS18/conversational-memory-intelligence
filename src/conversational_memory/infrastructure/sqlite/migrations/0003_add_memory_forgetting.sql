CREATE TABLE memory_forgetting (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    vector_id INTEGER,
    cleanup_state TEXT NOT NULL CHECK (cleanup_state IN ('cleanup_pending', 'complete')),
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK (vector_id IS NULL OR vector_id > 0),
    CHECK (
        (cleanup_state = 'cleanup_pending' AND completed_at IS NULL)
        OR (cleanup_state = 'complete' AND vector_id IS NOT NULL AND completed_at IS NOT NULL)
    ),
    UNIQUE (vector_id),
    FOREIGN KEY (user_id, memory_id)
        REFERENCES memories(user_id, memory_id) ON DELETE CASCADE
);

INSERT INTO memory_forgetting(
    memory_id, user_id, vector_id, cleanup_state, requested_at, completed_at
)
SELECT m.memory_id, m.user_id, v.vector_id, 'cleanup_pending', m.deleted_at, NULL
FROM memories AS m
LEFT JOIN memory_vector_mappings AS v ON v.memory_id = m.memory_id
WHERE m.deleted_at IS NOT NULL;
