import faiss
import numpy as np


DIMENSION = 4
TOP_K = 5



# 1. Create a small shared FAISS index

index = faiss.IndexFlatIP(DIMENSION)
index = faiss.IndexIDMap2(index)



# 2. Create toy normalized embeddings

vectors = np.array(
    [
        [0.80, 0.20, 0.0, 0.0],  # memory 101 -> user A
        [0.70, 0.30, 0.0, 0.0],  # memory 102 -> user A
        [0.99, 0.01, 0.0, 0.0],  # memory 103 -> user B
        [0.95, 0.05, 0.0, 0.0],  # memory 104 -> user B
        [0.60, 0.40, 0.0, 0.0],  # memory 105 -> user A
    ],
    dtype="float32")

memory_ids = np.array(
    [101, 102, 103, 104, 105],
    dtype="int64")



# 3. Metadata mapping

memory_metadata = {
    101: {"user_id": "A", "content": "User A uses FastAPI."},
    102: {"user_id": "A", "content": "User A prefers FAISS."},
    103: {"user_id": "B", "content": "User B uses Flask."},
    104: {"user_id": "B", "content": "User B prefers Qdrant."},
    105: {"user_id": "A", "content": "User A uses Docker."}}



# 4. Add vectors to the shared index

index.add_with_ids(vectors, memory_ids)


# 5. Query

query = np.array(
    [[1.0, 0.0, 0.0, 0.0]],
    dtype="float32")



# 6. Build the authorized ID set for user A

user_id = "A"

authorized_ids = [
    memory_id
    for memory_id, metadata in memory_metadata.items()
    if metadata["user_id"] == user_id]


print("Authorized IDs:", authorized_ids)


# 7. Search only authorized IDs

selector = faiss.IDSelectorBatch(np.array(authorized_ids, dtype="int64"))

params = faiss.SearchParameters()
params.sel = selector


scores, ids = index.search(query, TOP_K, params=params)


# 8. Display results

print("\nRetrieved memories:")

for score, memory_id in zip(scores[0], ids[0]):

    if memory_id == -1:
        continue

    metadata = memory_metadata[int(memory_id)]

    print(f"id={memory_id} | "
        f"user={metadata['user_id']} | "
        f"score={score:.4f} | "
        f"{metadata['content']}")



# 9. Isolation assertion

retrieved_ids = [int(memory_id)
    for memory_id in ids[0]
    if memory_id != -1]


retrieved_users = {memory_metadata[memory_id]["user_id"]
                   for memory_id in retrieved_ids}


assert retrieved_users == {user_id}, (
    f"Isolation failure: retrieved users = {retrieved_users}")


print("\nPASS: only authorized user's memories were retrieved.")