import json
import time
from pathlib import Path

from sentence_transformers import SentenceTransformer

from extraction import extract_memories
from storage import MemoryStore
from retrieval import retrieve


MODEL_NAME = "sentence-transformers/all-mpnet-base-v2"

# Load the embedding model once.
MODEL = SentenceTransformer(MODEL_NAME)


def load_case(case_path):
    """Load a workload case from JSON."""
    with open(case_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_memory_pool(case):
    """
    Build the memory pool for one workload case.

    Normal conversation memories go through extraction.
    Filler memories are already-formed memories and therefore
    bypass extraction, as defined in the baseline protocol.
    """
    memories = []

    if "conversation" in case:
        memories.extend(
            extract_memories(case["conversation"]))

    memories.extend(case.get("filler_memories", []))

    return memories


def evaluate_retrieval(case, retrieved_memories):
    """
    Evaluate whether the expected failure for a case
    was actually observed.

    This logic belongs to the evaluation harness, not to
    the naive baseline itself.
    """
    case_id = case["case_id"]

    retrieved_texts = [
        item["memory"]
        for item in retrieved_memories]

    evaluation = {"case_id": case_id,
                  "checks": []}

    if case_id == "case1":
        apartment_memory = (
            "I recently switched to a new apartment because my office moved closer.")

        old_memory = (
            "Initially, we decided to store embeddings and chunks "
            "together inside vector_store.pkl.")

        new_memory = (
            "We later migrated to FAISS and completely replaced "
            "the vector_store.pkl approach.")

        apartment_found = apartment_memory in retrieved_texts
        old_found = old_memory in retrieved_texts
        new_found = new_memory in retrieved_texts

        evaluation["checks"].extend(
            [
                {
                    "failure": "irrelevant_memory_retrieved",
                    "observed": apartment_found,
                    "details": {
                        "irrelevant_apartment_memory_retrieved": apartment_found
                    }
                },
                {
                    "failure": "contradictory_memories_retrieved",
                    "observed": old_found and new_found,
                    "details": {
                        "old_storage_decision_retrieved": old_found,
                        "new_storage_decision_retrieved": new_found
                    }}])

    elif case_id == "case2":
        old_memory = (
            "For now, let's keep the document chunks and embeddings "
            "together inside a single vector_store.pkl file. It will "
            "be easier while building the first version.")

        new_memory = (
            "We have completely migrated to FAISS now. The old "
            "vector_store.pkl approach is no longer part of the project.")

        old_found = old_memory in retrieved_texts
        new_found = new_memory in retrieved_texts

        evaluation["checks"].append(
            {
                "failure": "old_and_new_decisions_retrieved",
                "observed": old_found and new_found,
                "details": {
                    "old_decision_retrieved": old_found,
                    "new_decision_retrieved": new_found}
            })

    elif case_id == "case3":
        signal_positions = case.get(
            "workload_metadata",
            {}
        ).get("signal_memory_positions", [])

        signal_memories = []

        conversation = case.get("conversation", [])

        for position in signal_positions:
            if position < len(conversation):
                signal_memories.append(
                    conversation[position]["content"])

        retrieved_signal_memories = [
            memory
            for memory in signal_memories
            if memory in retrieved_texts]

        evaluation["checks"].append(
            {
                "failure": "multiple_historical_architectural_decisions_retrieved",
                "observed": len(retrieved_signal_memories) >= 2,
                "details": {
                    "signal_memory_positions": signal_positions,
                    "signal_memories_retrieved": len(retrieved_signal_memories),
                    "retrieved_signal_memories": retrieved_signal_memories}
                })

    elif case_id == "case4":
        flask_memory = (
            "I use Flask for deploying my machine learning applications.")

        fastapi_memory = (
            "I use FastAPI for deploying my machine learning applications.")

        flask_found = flask_memory in retrieved_texts
        fastapi_found = fastapi_memory in retrieved_texts

        evaluation["checks"].append(
            {
                "failure": "different_users_memory_retrieved",
                "observed": flask_found,
                "details": {
                    "active_user": case["evaluation_query"]["user_id"],
                    "expected_user_memory_retrieved": fastapi_found,
                    "other_user_memory_retrieved": flask_found}
            })

    elif case_id == "case5":
        api_key_memory = (
            "My temporary API key for testing is sk-test-123456789. "
            "I'll only use it for today's debugging session.")

        forget_request = (
            "The debugging session is over now. That API key should "
            "not be remembered or used again.")

        api_key_found = api_key_memory in retrieved_texts
        forget_request_found = forget_request in retrieved_texts

        evaluation["checks"].append(
            {
                "failure": "sensitive_memory_remains_retrievable_after_forget_request",
                "observed": api_key_found,
                "details": {
                    "api_key_memory_retrieved": api_key_found,
                    "forget_request_retrieved": forget_request_found}
            })

    elif case_id == "case6":
        relevant_terms = (
            "Japan",
            "Tokyo",
            "Kyoto",
            "Osaka",
            "itinerary",
            "travel",
            "visit",
            "hotel")

        unrelated_memories = [
            memory 
            for memory in retrieved_texts
            if not any(
                term.lower() in memory.lower()
                for term in relevant_terms)]

        evaluation["checks"].append(
               {"failure": "unrelated_memories_retrieved_for_cold_start",
                "observed": len(unrelated_memories) > 0,
                "details": {"unrelated_memories_retrieved": len(unrelated_memories),
                            "retrieved_count": len(retrieved_texts)}})

    return evaluation


def run_case(case_path):
    """
    Run one workload case through the naive baseline.
    """
    case = load_case(case_path)

    # Build the memory pool first.
    memories = build_memory_pool(case)

    # Store and embed every memory.
    store = MemoryStore(MODEL)
    store.add(memories)

    evaluation_query = case["evaluation_query"]
    query = evaluation_query["query"]

    # Per-query latency measurement starts here.
    start_time = time.perf_counter()

    query_embedding = MODEL.encode(
        query,
        convert_to_numpy=True,
        normalize_embeddings=True)

    stored_memories, memory_embeddings = store.get_all()

    retrieved = retrieve(
        query_embedding,
        stored_memories,
        memory_embeddings)

    # Context assembly.
    context_lines = ["[Retrieved Memories]"]

    for item in retrieved:
        context_lines.append(
            f"{item['rank']}. "
            f"{item['memory']} "
            f"(score: {item['score']:.4f})")

    context_lines.append("")
    context_lines.append("[Current Conversation]")

    context_lines.append(f"user: {evaluation_query['query']}")

    context = "\n".join(context_lines)

    end_time = time.perf_counter()

    latency_ms = (end_time - start_time) * 1000

    # Evaluation happens after the measured retrieval path.
    evaluation = evaluate_retrieval(case, retrieved)

    return {
        "case_id": case["case_id"],
        "memory_count": len(memories),
        "retrieved_count": len(retrieved),
        "retrieved_memories": retrieved,
        "context": context,
        "latency_ms": latency_ms,
        "evaluation": evaluation}