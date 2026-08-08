import numpy as np


class MemoryStore:
    """
    Simple flat memory store for the naive baseline.

    Every memory is stored unconditionally.
    No admission filtering, deduplication, or user isolation
    is applied.
    """

    def __init__(self, model):
        self.model = model
        self.memories = []
        self.embeddings = []

    def add(self, memories):
        """
        Add every candidate memory to the store.
        """
        if not memories:
            return

        new_embeddings = self.model.encode(
            memories,
            convert_to_numpy=True,
            normalize_embeddings=True
        )

        self.memories.extend(memories)
        self.embeddings.extend(new_embeddings)

    def get_all(self):
        """
        Return all stored memories and their embeddings.
        """
        if not self.embeddings:
            return [], np.empty((0, 768))

        return self.memories, np.vstack(self.embeddings)