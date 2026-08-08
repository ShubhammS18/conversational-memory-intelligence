"""
Generate the workload for Case 3:
Long conversations with a constrained context budget.

This script is intended to be run once. The generated JSON should be
committed to the repository and used for all experiments.
"""

import json
import random
from pathlib import Path


# Configuration

RANDOM_SEED = 10
TOTAL_MEMORIES = 50
SIGNAL_MEMORY_POSITIONS = [10, 25, 40]

OUTPUT_FILE = (
    Path(__file__).parent.parent
    / "workload"
    / "case3_long_context.json"
)

random.seed(RANDOM_SEED)


# Real architectural memories

SIGNAL_MEMORIES = [
    "Initially, we decided to store embeddings and document chunks together inside vector_store.pkl.",
    "As the project grew, we migrated embeddings to a FAISS index while storing document chunks separately.",
    "The migration is complete. vector_store.pkl has been removed and FAISS is now the active storage architecture."
]


# Templates for filler memories

FILLER_TEMPLATES = [
    "I usually drink tea while working.",
    "I prefer dark mode in most applications.",
    "I recently bought a new keyboard.",
    "I normally exercise in the evening.",
    "I enjoy listening to instrumental music while coding.",
    "I use VS Code for most development work.",
    "I usually write documentation after finishing a feature.",
    "I enjoy reading technical blogs.",
    "I prefer Python for rapid prototyping.",
    "I schedule meetings on Wednesday mornings.",
    "I like working in quiet environments.",
    "I recently upgraded my laptop RAM.",
    "I enjoy hiking during holidays.",
    "I normally take handwritten notes.",
    "I prefer coffee before starting work.",
    "I usually plan my tasks the night before.",
    "I keep my development projects organized by topic.",
    "I prefer short meetings over long meetings.",
    "I usually listen to podcasts while travelling.",
    "I like keeping my workspace clean.",
    "I prefer using keyboard shortcuts when possible.",
    "I normally review my notes before starting a new task.",
    "I enjoy trying different vegetarian recipes.",
    "I usually check my calendar in the morning.",
    "I prefer working on one major task at a time.",
    "I keep important project files backed up.",
    "I like reading books about technology.",
    "I normally take a short break after several hours of work.",
    "I prefer simple interfaces without unnecessary options.",
    "I usually organize files into separate project folders.",
    "I enjoy watching documentaries during weekends.",
    "I prefer writing down ideas before implementing them.",
    "I normally finish small tasks before starting larger ones.",
    "I like learning new software tools through small experiments.",
    "I usually keep track of completed tasks in a checklist.",
]


# Generate conversation

conversation = []

for i in range(TOTAL_MEMORIES):
    if i in SIGNAL_MEMORY_POSITIONS:
        idx = SIGNAL_MEMORY_POSITIONS.index(i)
        memory = SIGNAL_MEMORIES[idx]
    else:
        memory = random.choice(FILLER_TEMPLATES)

    conversation.append(
        {
            "role": "user",
            "content": memory
        }
    )


# Build JSON

case = {
    "case_id": "case3",
    "description": "Long conversations with constrained context budget",

    "expected_failure": (
        "The baseline retrieves multiple historical architectural decisions "
        "while consuming a large portion of the available context because it "
        "performs no context budgeting or temporal reasoning."
    ),

    "expected_improvement": (
        "The improved system should prioritize the current architectural "
        "decision while constructing context within a fixed token budget."
    ),

    "conversation": conversation,

    "filler_memories": [],

    "workload_metadata": {
        "random_seed": RANDOM_SEED,
        "total_memories": TOTAL_MEMORIES,
        "signal_memory_positions": SIGNAL_MEMORY_POSITIONS
    },

    "evaluation_query": {
        "query": "What is the current storage architecture used by the project?",
        "user_id": None
    },

    "expected_retrieval_issue": [
        "The baseline retrieves multiple historical architectural decisions because it has no temporal reasoning or conflict resolution.",
        "Retrieved memories consume a significant portion of the available context because the baseline performs no context budgeting or summarization."
    ]
}


# Write generated workload to disk

OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(case, f, indent=2)

print(f"Generated {OUTPUT_FILE}")