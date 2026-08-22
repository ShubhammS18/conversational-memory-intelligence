from datetime import date

SOURCE_PRIORITY = {
    "explicit": 2,
    "inferred": 1}

memories = [
    {
    "memory_id": 1,
    "content": "I use vector_store.pkl for vector storage.",
    "subject": "vector_search",
    "value": "vector_store.pkl",
    "valid_from": date(2026, 7, 1),
    "relationship": None,
    "source": "explicit",
    "status": "active",
    "superseded_by": None
},
{
    "memory_id": 2,
    "content": "I migrated the project from vector_store.pkl to FAISS.",
    "subject": "vector_search",
    "value": "FAISS",
    "valid_from": date(2026, 8, 1),
    "relationship": "supersedes",
    "source": "explicit",
    "status": "active",
    "supersedes": [1]
},
{
    "memory_id": 3,
    "content": "I currently prefer FAISS for vector search.",
    "subject": "vector_search",
    "value": "FAISS",
    "valid_from": date(2026, 8, 10),
    "relationship": "reinforces",
    "source": "explicit",
    "status": "active",
    "supersedes": []
},
{
    "memory_id": 4,
    "content": "The user probably prefers Qdrant.",
    "subject": "vector_search",
    "value": "Qdrant",
    "valid_from": date(2026, 8, 11),
    "relationship": "supersedes",
    "source": "inferred",
    "status": "active",
    "supersedes": []
},
{
    "memory_id": 5,
    "content": "I've switched from FAISS to Qdrant for vector search.",
    "subject": "vector_search",
    "value": "Qdrant",
    "valid_from": date(2026, 8, 12),
    "relationship": "supersedes",
    "source": "explicit",
    "status": "active",
    "supersedes": [3]
},
{
    "memory_id": 6,
    "content": "I prefer FAISS for vector search.",
    "subject": "vector_search",
    "value": "FAISS",
    "valid_from": date(2026, 8, 10),
    "relationship": None,
    "source": "explicit",
    "status": "active",
    "supersedes": []
},
{
    "memory_id": 7,
    "content": "I prefer Qdrant for vector search.",
    "subject": "vector_search",
    "value": "Qdrant",
    "valid_from": date(2026, 8, 10),
    "relationship": None,
    "source": "explicit",
    "status": "active",
    "supersedes": []
}]


def find_supersession_candidates(memories):
    candidates = []

    for older in memories:
        for newer in memories:

            if older["memory_id"] == newer["memory_id"]:
                continue

            same_subject = older["subject"] == newer["subject"]
            different_value = older["value"] != newer["value"]
            newer_date = newer["valid_from"] > older["valid_from"]

            older_priority = SOURCE_PRIORITY[older["source"]]
            newer_priority = SOURCE_PRIORITY[newer["source"]]

            if (same_subject and different_value
                and newer_date and newer["relationship"] == "supersedes"
                and older["memory_id"] in newer.get("supersedes", [])
                and newer_priority >= older_priority):
                candidates.append(
                    {"older_memory": older["memory_id"],
                    "newer_memory": newer["memory_id"]})

    return candidates

def retrieve_current_memories(memories, subject):
    return [
        memory
        for memory in memories
        if memory["subject"] == subject
        and memory["status"] == "active"]


def resolve_explicit_conflict(memories, subject):
    candidates = [
        memory
        for memory in memories
        if memory["subject"] == subject
        and memory["status"] == "active"
        and memory["source"] == "explicit"]

    values = set(memory["value"] for memory in candidates)

    if len(values) <= 1:
        return candidates

    return sorted(
        candidates,
        key=lambda memory: memory["valid_from"],
        reverse=True)


def resolve_authority_conflict(memories, subject):
    candidates = [
        memory
        for memory in memories
        if memory["subject"] == subject
        and memory["status"] == "active"]

    if not candidates:
        return []

    highest_priority = max(
        SOURCE_PRIORITY[memory["source"]]
        for memory in candidates)

    candidates = [
        memory
        for memory in candidates
        if SOURCE_PRIORITY[memory["source"]] == highest_priority]

    return sorted(
        candidates,
        key=lambda memory: memory["valid_from"],
        reverse=True)


candidates = find_supersession_candidates(memories)

for candidate in candidates:
    older_id = candidate["older_memory"]
    newer_id = candidate["newer_memory"]

    for memory in memories:
        if memory["memory_id"] == older_id:
            memory["status"] = "superseded"
            memory["superseded_by"] = newer_id

print("Supersession candidates:")

for candidate in candidates:
    print(
        f"memory {candidate['older_memory']} "
        f"→ memory {candidate['newer_memory']}")


print("\nMemory lifecycle:")

for memory in memories:
    print(
        f"memory {memory['memory_id']} | "
        f"status={memory['status']} | "
        f"superseded_by={memory.get('superseded_by')}")


current_memories = retrieve_current_memories(
    memories,
    "vector_search")


print("\nCurrent memories:")

for memory in current_memories:
    print(
        f"memory {memory['memory_id']} | "
        f"value={memory['value']} | "
        f"status={memory['status']}")

resolved = resolve_explicit_conflict(
    memories,
    "vector_search")

print("\nExplicit conflict resolution:")

for memory in resolved:
    print(
        f"memory {memory['memory_id']} | "
        f"value={memory['value']} | "
        f"date={memory['valid_from']}")

authority_resolved = resolve_authority_conflict(
    memories,
    "vector_search")

print("\nAuthority-first conflict resolution:")

for memory in authority_resolved:
    print(
        f"memory {memory['memory_id']} | "
        f"value={memory['value']} | "
        f"source={memory['source']} | "
        f"date={memory['valid_from']}")

# Case 1 evaluation:
# A memory explicitly superseded by a newer memory
# must not remain active.

memory_1 = next(memory
                for memory in memories
                if memory["memory_id"] == 1)

assert memory_1["status"] == "superseded"
assert memory_1["superseded_by"] == 2

print("\nCase 1 evaluation: PASS")


# Case 2 evaluation:
# A superseded preference must not appear
# in the current active memories.

current_memory_ids = [memory["memory_id"]
                      for memory in current_memories]

assert 1 not in current_memory_ids
assert 2 in current_memory_ids

print("Case 2 evaluation: PASS")