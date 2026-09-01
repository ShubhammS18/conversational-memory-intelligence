"""Cache-only CPU SentenceTransformer adapter for the immutable M1 model."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

from conversational_memory.application.contracts import Embedding
from conversational_memory.application.errors import (
    ConfigurationMismatchError,
    IndexingError,
    ServiceUnavailableError,
)

ALL_MPNET_BASE_V2_LOAD_NAME = "sentence-transformers/all-mpnet-base-v2"
ALL_MPNET_BASE_V2_REVISION = "e8c3b32edf5434bc2275fc9bab85f82640a19130"
ALL_MPNET_BASE_V2_MODEL_ID = (
    f"{ALL_MPNET_BASE_V2_LOAD_NAME}@{ALL_MPNET_BASE_V2_REVISION}"
)
ALL_MPNET_BASE_V2_DIMENSION = 768


class SentenceTransformerEmbedder:
    """Generate normalized float32 embeddings from one immutable cached model."""

    def __init__(self, *, cache_directory: str | Path) -> None:
        self._cache_directory = Path(cache_directory)
        if not self._cache_directory.is_dir():
            raise ServiceUnavailableError("SentenceTransformer cache directory is unavailable")
        try:
            self._model: Any = SentenceTransformer(
                ALL_MPNET_BASE_V2_LOAD_NAME,
                revision=ALL_MPNET_BASE_V2_REVISION,
                cache_folder=str(self._cache_directory),
                device="cpu",
                local_files_only=True,
            )
            dimension_accessor = getattr(self._model, "get_embedding_dimension", None)
            if not callable(dimension_accessor):
                dimension_accessor = getattr(
                    self._model,
                    "get_sentence_embedding_dimension",
                    None,
                )
            if not callable(dimension_accessor):
                raise TypeError("SentenceTransformer dimension accessor is unavailable")
            loaded_dimension = dimension_accessor()
        except Exception as error:
            raise ServiceUnavailableError(
                "SentenceTransformer model could not be loaded from the configured cache"
            ) from error
        if loaded_dimension != ALL_MPNET_BASE_V2_DIMENSION:
            raise ConfigurationMismatchError(
                "SentenceTransformer dimension does not match the approved configuration"
            )

    def embed(self, content: str) -> Embedding:
        """Encode one normalized string as a finite unit-length float32 vector."""
        try:
            encoded = self._model.encode(
                content,
                convert_to_numpy=True,
                normalize_embeddings=True,
                precision="float32",
                device="cpu",
                show_progress_bar=False,
            )
        except Exception as error:
            raise IndexingError("SentenceTransformer encoding failed") from error

        try:
            vector = np.asarray(encoded, dtype=np.float32)
        except Exception as error:
            raise IndexingError("SentenceTransformer output is not a float32 vector") from error
        if vector.shape != (ALL_MPNET_BASE_V2_DIMENSION,):
            raise IndexingError("SentenceTransformer output shape is invalid")
        if not np.isfinite(vector).all():
            raise IndexingError("SentenceTransformer output contains a non-finite value")
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm == 0.0 or not math.isclose(
            norm,
            1.0,
            rel_tol=1e-5,
            abs_tol=1e-6,
        ):
            raise IndexingError("SentenceTransformer output is not L2-normalized")
        return Embedding(
            values=tuple(float(value) for value in vector),
            model_id=ALL_MPNET_BASE_V2_MODEL_ID,
            dimension=ALL_MPNET_BASE_V2_DIMENSION,
        )


__all__ = [
    "ALL_MPNET_BASE_V2_DIMENSION",
    "ALL_MPNET_BASE_V2_LOAD_NAME",
    "ALL_MPNET_BASE_V2_MODEL_ID",
    "ALL_MPNET_BASE_V2_REVISION",
    "SentenceTransformerEmbedder",
]
