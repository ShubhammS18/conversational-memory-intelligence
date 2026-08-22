from datetime import date


memories = [
    {
        "memory_id": 1,
        "content": "I prefer FAISS for vector search.",
        "subject": "vector_search",
        "value": "FAISS",
        "valid_from": date(2026, 8, 1),
        "status": "superseded",
        "superseded_by": 2,
    },
    {
        "memory_id": 2,
        "content": "I currently prefer Qdrant for vector search.",
        "subject": "vector_search",
        "value": "Qdrant",
        "valid_from": date(2026, 8, 12),
        "status": "active",
        "superseded_by": None,
    }]


def select_memories_for_query(memories, query_intent):
    if query_intent == "current":
        return [
            memory
            for memory in memories
            if memory["status"] == "active"]

    if query_intent == "historical":
        return [
            memory
            for memory in memories
            if memory["status"] in {
                "active",
                "superseded"}]

    return []



# Query 1: Current-state question


current_query = "What vector database do I currently prefer?"

current_results = select_memories_for_query(
    memories,
    query_intent="current")

print("Current query:")
print(current_query)

print("\nContext:")

for memory in current_results:
    print(
        f"- memory {memory['memory_id']}: "
        f"{memory['content']}")


# Current query must select only the active memory.
assert [memory["memory_id"] for memory in current_results] == [2]

print("\nCurrent query evaluation: PASS")



# Query 2: Historical question


historical_query = "What vector database did I use before Qdrant?"

historical_results = select_memories_for_query(
    memories,
    query_intent="historical")

print("\nHistorical query:")
print(historical_query)

print("\nContext:")

for memory in historical_results:
    print(
        f"- memory {memory['memory_id']}: "
        f"{memory['content']}")


# Historical query must be allowed to access
# the superseded FAISS memory.
historical_ids = [
    memory["memory_id"]
    for memory in historical_results]

assert 1 in historical_ids
assert 2 in historical_ids

print("\nHistorical query evaluation: PASS")