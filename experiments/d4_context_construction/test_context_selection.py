import tiktoken

memories = [
    {
        "memory_id": 1,
        "content": "I use FastAPI.",
        "subject": "backend_framework",
        "value": "FastAPI"
    },
    {
        "memory_id": 2,
        "content": "I prefer FAISS for vector search.",
        "subject": "vector_search",
        "value": "FAISS"
    },
    {
        "memory_id": 3,
        "content": "I use Docker.",
        "subject": "deployment",
        "value": "Docker"
    },
    {
        "memory_id": 4,
        "content": "I migrated from vector_store.pkl to FAISS.",
        "subject": "vector_search",
        "value": "FAISS"
    },
    {
        "memory_id": 5,
        "content": "I currently prefer Qdrant for vector search.",
        "subject": "vector_search",
        "value": "Qdrant"
    }]


def select_relevant_memories(memories, subject):
    return [
        memory
        for memory in memories
        if memory["subject"] == subject]


tokenizer = tiktoken.get_encoding("cl100k_base")


def estimate_tokens(text):
    return len(tokenizer.encode(text))


def build_context(memories, max_tokens, reserved_response_tokens=0):
    available_memory_tokens = (max_tokens - reserved_response_tokens)

    if available_memory_tokens <= 0:
        return []

    ranked_memories = sorted(
        memories,
        key=lambda memory: memory["score"],
        reverse=True)

    selected_memories = []
    tokens_used = 0

    for memory in ranked_memories:
        memory_tokens = estimate_tokens(memory["content"])

        if (tokens_used + memory_tokens <= available_memory_tokens):
            selected_memories.append(memory)
            tokens_used += memory_tokens

    return selected_memories


query = "What vector database do I currently prefer?"

relevant_memories = [
    {
        "memory_id": 2,
        "content": "I prefer FAISS for vector search.",
        "score": 0.60,
    },
    {
        "memory_id": 4,
        "content": "I migrated from vector_store.pkl to FAISS.",
        "score": 0.90,
    },
    {
        "memory_id": 5,
        "content": "I currently prefer Qdrant for vector search.",
        "score": 0.70,
    }]

context = build_context(relevant_memories,
                        max_tokens=30,
                        reserved_response_tokens=10)


print("Query:")
print(query)

print("\nContext passed to the model:")

for memory in context:
    print(f"- memory {memory['memory_id']}: "
          f"{memory['content']}")

print("\nSelected memory IDs:")

for memory in context:
    print(memory["memory_id"])

total_memory_tokens = sum(
    estimate_tokens(memory["content"])
    for memory in context)

print(
    f"\nMemory tokens used: "f"{total_memory_tokens}")

print(
    f"Memory token budget: "f"{30 - 10}")



# Case 3 evaluation:
# Selected memories must never exceed the available
# memory-token budget.

memory_tokens_used = sum(estimate_tokens(memory["content"])
                         for memory in context)

available_memory_tokens = (30 - 10)

assert memory_tokens_used <= available_memory_tokens

print("\nCase 3 evaluation: PASS")


# Case 3 evaluation:
# The memory representing the current preference
# must be preserved in the selected context.

selected_memory_ids = [
    memory["memory_id"]
    for memory in context]

assert 5 in selected_memory_ids

print("Case 3 relevance evaluation: PASS")


# Case 3 evaluation:
# Reserved response tokens must remain available.

total_budget = 30
reserved_response_tokens = 10

assert (memory_tokens_used + reserved_response_tokens <= total_budget)

print("Case 3 response reservation evaluation: PASS")