import gc
import time

import faiss
import numpy as np


DIMENSION = 768
TOP_K = 5
RANDOM_SEED = 42

SCALE_POINTS = [
    100_000,
    1_000_000,
    5_000_000]

AUTHORIZED_FRACTIONS = [
    0.001,
    0.01,
    0.10]

NUM_QUERIES = 100
WARMUP_QUERIES = 10


def build_index(total_memories):
    rng = np.random.default_rng(RANDOM_SEED)

    vectors = rng.random(
        (total_memories, DIMENSION),
        dtype=np.float32)

    faiss.normalize_L2(vectors)

    memory_ids = np.arange(1,
                           total_memories + 1,
                           dtype=np.int64)

    index = faiss.IndexIDMap2(faiss.IndexFlatIP(DIMENSION))

    start = time.perf_counter()

    index.add_with_ids(vectors, memory_ids)

    build_seconds = time.perf_counter() - start

    del vectors
    del memory_ids
    gc.collect()

    return index, build_seconds


def run_scoped_search(
    index,
    total_memories,
    authorized_fraction,
):
    authorized_count = int(
        total_memories * authorized_fraction)

    authorized_ids = np.arange(1, authorized_count + 1, dtype=np.int64)

    selector = faiss.IDSelectorBatch(authorized_ids)

    params = faiss.SearchParameters()
    params.sel = selector

    rng = np.random.default_rng(RANDOM_SEED
                                + total_memories
                                + int(authorized_fraction * 10_000))

    queries = rng.random(
        (NUM_QUERIES + WARMUP_QUERIES, DIMENSION), dtype=np.float32)

    faiss.normalize_L2(queries)

    for query in queries[:WARMUP_QUERIES]:
        index.search(query.reshape(1, -1),
                     TOP_K,
                     params=params)

    latencies_ms = []
    first_retrieved_ids = None

    for query in queries[WARMUP_QUERIES:]:

        start = time.perf_counter()

        scores, ids = index.search(query.reshape(1, -1), TOP_K, params=params)

        elapsed_ms = (time.perf_counter() - start) * 1000

        latencies_ms.append(elapsed_ms)

        retrieved_ids = [int(memory_id) for memory_id in ids[0]
                         if memory_id != -1]

        if first_retrieved_ids is None:
            first_retrieved_ids = retrieved_ids

    authorized_set = set(int(memory_id) for memory_id in authorized_ids)

    assert all(memory_id in authorized_set
               for memory_id in first_retrieved_ids)

    latencies_ms = np.array(latencies_ms)

    return (authorized_count,
            np.percentile(latencies_ms, 50),
            np.percentile(latencies_ms, 95),
            np.mean(latencies_ms),
            first_retrieved_ids)


print("D4 Phase 10 — FAISS scale experiment")

print("=" * 60)

print(f"Dimension: {DIMENSION}")

print(f"Top-k: {TOP_K}")

print("Authorized fractions: " "0.1%, 1%, 10%")

print(f"Measured queries per test: "
      f"{NUM_QUERIES}")

print(f"Warm-up queries: "
      f"{WARMUP_QUERIES}")

print()


for total_memories in SCALE_POINTS:

    print(f"Scale: "
          f"{total_memories:,} memories")

    raw_vector_gb = (total_memories * DIMENSION * 4 / (1024 ** 3))

    print(f"Raw vector storage: "
          f"{raw_vector_gb:.3f} GB")

    start = time.perf_counter()

    index, build_seconds = build_index(total_memories)

    print(f"Index build time: "
          f"{build_seconds:.3f} s")

    print()

    for authorized_fraction in AUTHORIZED_FRACTIONS:

        (authorized_count, p50_ms, p95_ms, mean_ms,
            retrieved_ids) = run_scoped_search(index,
                                               total_memories,
                                               authorized_fraction)

        print(f"Authorized fraction: "
              f"{authorized_fraction:.1%}")

        print(f"Authorized IDs: "
              f"{authorized_count:,}")

        print(f"Search p50: "
              f"{p50_ms:.3f} ms")

        print(f"Search p95: "
              f"{p95_ms:.3f} ms")

        print(f"Search mean: "
              f"{mean_ms:.3f} ms")

        print(f"Retrieved IDs: "
              f"{retrieved_ids}")

        print("Isolation check: PASS")

        print()

    del index
    gc.collect()

    print(f"Completed scale: "
          f"{total_memories:,}")

    print()


print("Scale experiment completed.")