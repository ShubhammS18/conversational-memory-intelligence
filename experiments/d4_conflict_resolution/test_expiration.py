from datetime import date


memories = [
    {
        "memory_id": 1,
        "content": "I am working on the summer project.",
        "subject": "project",
        "value": "summer_project",
        "valid_from": date(2026, 8, 1),
        "valid_until": date(2026, 8, 15),
        "status": "active",
    },
    {
        "memory_id": 2,
        "content": "I prefer FAISS for vector search.",
        "subject": "vector_search",
        "value": "FAISS",
        "valid_from": date(2026, 8, 1),
        "valid_until": None,
        "status": "active",
    }]


def update_expired_memories(memories, current_date):
    for memory in memories:
        valid_until = memory.get("valid_until")

        if (
            memory["status"] == "active"
            and valid_until is not None
            and current_date > valid_until
        ):
            memory["status"] = "expired"


# Simulate the system running after the validity period.
current_date = date(2026, 8, 16)

update_expired_memories(
    memories,
    current_date)


print("Memory lifecycle:")

for memory in memories:
    print(
        f"memory {memory['memory_id']} | "
        f"value={memory['value']} | "
        f"status={memory['status']} | "
        f"valid_until={memory['valid_until']}")


# Validation

project_memory = next(
    memory for memory in memories
    if memory["memory_id"] == 1)

vector_memory = next(
    memory for memory in memories
    if memory["memory_id"] == 2)


# 1. Memory whose validity period has ended must expire.
assert project_memory["status"] == "expired"


# 2. Memory without an expiration date must remain active.
assert vector_memory["status"] == "active"


print("\nPASS: validity periods are respected.")