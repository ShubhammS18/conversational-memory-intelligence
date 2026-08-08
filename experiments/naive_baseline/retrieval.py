import numpy as np


TOP_K = 10


def retrieve(query_embedding, memories, memory_embeddings, top_k=TOP_K):
    """
    Retrieve the top-k most similar memories.

    This is intentionally naive:
    - similarity is the only ranking signal
    - no similarity threshold
    - no recency
    - no importance
    - no conflict resolution
    - no user filtering
    """

    if not memories:
        return []

    query_embedding = np.asarray(query_embedding)

    memory_embeddings = np.asarray(memory_embeddings)

    # Embeddings are normalized during storage, so dot product
    # is equivalent to cosine similarity.

    scores = memory_embeddings @ query_embedding

    ranked_indices = np.argsort(scores)[::-1]

    results = []

    for rank, index in enumerate(ranked_indices[:top_k], start=1):
        results.append(
               {"rank": rank,
                "memory": memories[index],
                "score": float(scores[index])})

    return results