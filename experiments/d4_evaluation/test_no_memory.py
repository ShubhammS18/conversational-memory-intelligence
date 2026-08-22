# D4 Phase 9 — Case 6: Cold-start / no relevant memory
#
# This tests whether retrieval can return "no memory"
# instead of always returning the top-k candidate.

memories = [
    {
        "memory_id": 1,
        "content": "I prefer FAISS for vector search.",
        "score": 0.31
    },
    {
        "memory_id": 2,
        "content": "I use FastAPI for my backend.",
        "score": 0.28
    },
    {
        "memory_id": 3,
        "content": "I use Docker for deployment.",
        "score": 0.25 }]


MIN_RELEVANCE_SCORE = 0.50


def retrieve_with_no_memory_option(memories, min_score):
    relevant_memories = [memory for memory in memories if memory["score"] >= min_score]

    return relevant_memories


query = "What is my favorite programming language?"

results = retrieve_with_no_memory_option(memories, MIN_RELEVANCE_SCORE)


print("Query:")
print(query)

print("\nRetrieved memories:")

if results:
    for memory in results:
        print(
            f"- memory {memory['memory_id']}: "
            f"{memory['content']} "
            f"(score={memory['score']})")
else:
    print("No relevant memory")


# Case 6 evaluation:
# When no candidate reaches the relevance threshold,
# retrieval must be able to return no memory.

assert results == []

print("\nCase 6 — no relevant memory: PASS")