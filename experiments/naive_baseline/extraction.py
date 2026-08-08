def extract_memories(conversation):
    """
    Extract candidate memories from a conversation.

    Baseline rule:
    Every user message becomes a candidate memory.

    No filtering, importance scoring, deduplication,
    sensitivity detection, or other admission logic is applied.
    """

    memories = []

    for turn in conversation:
        if turn["role"] == "user":
            memories.append(turn["content"])

    return memories