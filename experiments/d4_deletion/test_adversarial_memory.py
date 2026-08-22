# Adversarial memory / retrieval

import faiss
import numpy as np


DIMENSION = 4
TOP_K = 5


index = faiss.IndexFlatIP(DIMENSION)
index = faiss.IndexIDMap2(index)


# User A has a legitimate memory.
# User B has a deliberately more similar memory.

vectors = np.array(
    [
        [0.80, 0.20, 0.0, 0.0],  # 401 -> A
        [0.99, 0.01, 0.0, 0.0],  # 402 -> B
    ],
    dtype="float32")

memory_ids = np.array(
    [401, 402],
    dtype="int64")

metadata = {
    401: {
        "user_id": "A",
        "content": "User A prefers FAISS.",
    },
    402: {
        "user_id": "B",
        "content": "User B prefers Qdrant.",
    }
}

index.add_with_ids(vectors, memory_ids)


query = np.array(
    [[1.0, 0.0, 0.0, 0.0]],
    dtype="float32")


# User A's authorized IDs only.
authorized_ids = [
    memory_id
    for memory_id, memory in metadata.items()
    if memory["user_id"] == "A"
    ]


selector = faiss.IDSelectorBatch(
    np.array(authorized_ids, dtype="int64"))

params = faiss.SearchParameters()
params.sel = selector


scores, ids = index.search(
    query,
    TOP_K,
    params=params)


retrieved_ids = [
    int(memory_id)
    for memory_id in ids[0]
    if memory_id != -1
    ]


retrieved_users = {
    metadata[memory_id]["user_id"]
    for memory_id in retrieved_ids}


assert retrieved_users == {"A"}

assert 402 not in retrieved_ids

print("Adversarial cross-user retrieval: PASS")