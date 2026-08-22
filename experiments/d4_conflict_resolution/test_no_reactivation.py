from datetime import date


memories = [
    {
        "memory_id": 1,
        "content": "I prefer FAISS for vector search.",
        "subject": "vector_search",
        "value": "FAISS",
        "valid_from": date(2026, 8, 1),
        "status": "active",
        "superseded_by": None,
    },
    {
        "memory_id": 2,
        "content": "I currently prefer Qdrant for vector search.",
        "subject": "vector_search",
        "value": "Qdrant",
        "valid_from": date(2026, 8, 10),
        "status": "active",
        "superseded_by": None,
    },
    {
        "memory_id": 3,
        "content": "I switched back to FAISS for vector search.",
        "subject": "vector_search",
        "value": "FAISS",
        "valid_from": date(2026, 8, 12),
        "status": "active",
        "superseded_by": None,
    },
]


def supersede_memory(memories, old_id, new_id):
    old_memory = next(
        memory for memory in memories
        if memory["memory_id"] == old_id
    )

    new_memory = next(
        memory for memory in memories
        if memory["memory_id"] == new_id
    )

    old_memory["status"] = "superseded"
    old_memory["superseded_by"] = new_id

    new_memory["status"] = "active"


# Qdrant replaces FAISS.
supersede_memory(
    memories,
    old_id=1,
    new_id=2,
)

# FAISS becomes current again.
#
# IMPORTANT:
# We do NOT reactivate memory 1.
# Instead, memory 3 is the new representation
# of the current state.
supersede_memory(
    memories,
    old_id=2,
    new_id=3,
)


print("Memory lifecycle:")

for memory in memories:
    print(
        f"memory {memory['memory_id']} | "
        f"value={memory['value']} | "
        f"status={memory['status']} | "
        f"superseded_by={memory['superseded_by']}"
    )


# Validation

memory_1 = next(
    memory for memory in memories
    if memory["memory_id"] == 1
)

memory_2 = next(
    memory for memory in memories
    if memory["memory_id"] == 2
)

memory_3 = next(
    memory for memory in memories
    if memory["memory_id"] == 3
)


# Old FAISS memory remains historical.
assert memory_1["status"] == "superseded"
assert memory_1["superseded_by"] == 2

# Qdrant was current temporarily, then became superseded.
assert memory_2["status"] == "superseded"
assert memory_2["superseded_by"] == 3

# The new FAISS memory is the only current representation.
assert memory_3["status"] == "active"

# The original FAISS memory was NOT reactivated.
assert memory_1["status"] != "active"


active_memories = [
    memory
    for memory in memories
    if memory["status"] == "active"
]

assert [memory["memory_id"] for memory in active_memories] == [3]


print("\nPASS: old memories are not automatically reactivated.")