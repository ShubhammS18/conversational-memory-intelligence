import faiss
import numpy as np


DIMENSION = 4
TOP_K = 5



# 1. Shared FAISS index


index = faiss.IndexFlatIP(DIMENSION)
index = faiss.IndexIDMap2(index)


vectors = np.array(
    [
        [0.80, 0.20, 0.0, 0.0],  # 101 -> user A
        [0.70, 0.30, 0.0, 0.0],  # 102 -> user A
        [0.99, 0.01, 0.0, 0.0],  # 103 -> user B
    ],
    dtype="float32")

memory_ids = np.array(
    [101, 102, 103],
    dtype="int64")


memory_metadata = {
    101: {
        "user_id": "A",
        "content": "User A prefers FAISS.",
        "deleted": False,
    },
    102: {
        "user_id": "A",
        "content": "User A uses FastAPI.",
        "deleted": False,
    },
    103: {
        "user_id": "B",
        "content": "User B prefers Qdrant.",
        "deleted": False,
    }}


index.add_with_ids(vectors, memory_ids)


# 2. Resolve eligible IDs

def get_eligible_ids(metadata, user_id):
    return [
        memory_id
        for memory_id, memory in metadata.items()
        if memory["user_id"] == user_id
        and not memory["deleted"]]



# 3. Retrieval

def retrieve(index, metadata, user_id, query):
    eligible_ids = get_eligible_ids(metadata, user_id)

    if not eligible_ids:
        return []

    selector = faiss.IDSelectorBatch(
        np.array(eligible_ids, dtype="int64"))

    params = faiss.SearchParameters()
    params.sel = selector

    scores, ids = index.search(
        query,
        TOP_K,
        params=params)

    results = []

    for score, memory_id in zip(scores[0], ids[0]):
        if memory_id == -1:
            continue

        results.append({
            "memory_id": int(memory_id),
            "score": float(score),
        })

    return results



# 4. Forget operation


def forget_memory(index, metadata, memory_id, requesting_user_id):

    memory = metadata.get(memory_id)

    if memory is None:
        return False

    # Authorization
    if memory["user_id"] != requesting_user_id:
        return False

    # Idempotency
    if memory["deleted"]:
        return True

    # Logical exclusion happens first.
    memory["deleted"] = True

    # Physical FAISS deletion.
    selector = faiss.IDSelectorBatch(
        np.array([memory_id], dtype="int64"))

    removed = index.remove_ids(selector)

    if removed != 1:
        return False

    return True



# 5. Test setup


query = np.array(
    [[1.0, 0.0, 0.0, 0.0]],
    dtype="float32")


# Case 1 — Authorized deletion


before = retrieve(
    index,
    memory_metadata,
    "A",
    query)

assert 101 in [r["memory_id"] for r in before]

result = forget_memory(
    index,
    memory_metadata,
    101,
    "A")

assert result is True
assert memory_metadata[101]["deleted"] is True

after = retrieve(
    index,
    memory_metadata,
    "A",
    query)

assert 101 not in [r["memory_id"] for r in after]

print("Case 1 — authorized deletion: PASS")



# Case 2 — Unauthorized deletion

result = forget_memory(
    index,
    memory_metadata,
    103,
    "A")

assert result is False
assert memory_metadata[103]["deleted"] is False

print("Case 2 — unauthorized deletion: PASS")



# Case 3 — Idempotent deletion


result = forget_memory(
    index,
    memory_metadata,
    101,
    "A")

assert result is True
assert memory_metadata[101]["deleted"] is True

print("Case 3 — idempotent deletion: PASS")



# Case 4 — Other user's memory remains available


user_b_results = retrieve(
    index,
    memory_metadata,
    "B",
    query)

assert 103 in [r["memory_id"] for r in user_b_results]

print("Case 4 — other user's memory unaffected: PASS")



# Case 5 — FAISS deletion failure

# Create a fresh index for this failure-path test.

failure_index = faiss.IndexFlatIP(DIMENSION)
failure_index = faiss.IndexIDMap2(failure_index)

failure_index.add_with_ids(
    np.array(
        [[0.80, 0.20, 0.0, 0.0]],
        dtype="float32",
    ),
    np.array([201], dtype="int64"))

failure_metadata = {
    201: {
        "user_id": "A",
        "content": "User A prefers FAISS.",
        "deleted": False}}


# Save the original FAISS deletion method.
original_remove_ids = failure_index.remove_ids


# Simulate physical FAISS deletion failure.
def failing_remove_ids(selector):
    raise RuntimeError("Simulated FAISS deletion failure")


failure_index.remove_ids = failing_remove_ids


# Attempt deletion.
try:
    result = forget_memory(
        failure_index,
        failure_metadata,
        201,
        "A")
except RuntimeError:
    result = False


# Logical deletion must still have happened.
assert failure_metadata[201]["deleted"] is True

# The memory must therefore remain excluded
# from the retrieval candidate set.
eligible_ids = get_eligible_ids(
    failure_metadata,
    "A")

assert 201 not in eligible_ids

# Restore the original method.
failure_index.remove_ids = original_remove_ids

print("Case 5 — FAISS deletion failure: PASS")



# Case 6 — Forgetting a newer memory must not
# reactivate an older superseded memory

lifecycle_memories = [
    {
        "memory_id": 301,
        "user_id": "A",
        "content": "I prefer FAISS.",
        "status": "superseded",
        "deleted": False,
    },
    {
        "memory_id": 302,
        "user_id": "A",
        "content": "I prefer Qdrant.",
        "status": "active",
        "deleted": False,
    }]

lifecycle_index = faiss.IndexFlatIP(DIMENSION)
lifecycle_index = faiss.IndexIDMap2(lifecycle_index)

lifecycle_index.add_with_ids(
    np.array(
        [
            [0.80, 0.20, 0.0, 0.0],
            [0.90, 0.10, 0.0, 0.0],
        ],
        dtype="float32",
    ),
    np.array([301, 302], dtype="int64"))

lifecycle_metadata = {
    memory["memory_id"]: memory
    for memory in lifecycle_memories}


result = forget_memory(
    lifecycle_index,
    lifecycle_metadata,
    302,
    "A")

assert result is True

assert lifecycle_metadata[302]["deleted"] is True
assert lifecycle_metadata[302]["status"] == "active"

# Older memory must remain superseded.
assert lifecycle_metadata[301]["status"] == "superseded"
assert lifecycle_metadata[301]["deleted"] is False

print("Case 6 — forgetting newer memory does not reactivate "
      "older memory: PASS")