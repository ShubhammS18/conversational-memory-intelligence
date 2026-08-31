"""Durable copy-on-write FAISS vector-index adapter."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

import faiss
import numpy as np

from conversational_memory.application.contracts import Embedding
from conversational_memory.application.errors import (
    ConfigurationMismatchError,
    IndexingError,
    ServiceUnavailableError,
)

_FORMAT_VERSION = 1
_INDEX_KIND = "IndexIDMap2(IndexFlatIP)"
_FINAL_INDEX_NAME = "memory.faiss"
_FINAL_METADATA_NAME = "memory.faiss.meta.json"
_METADATA_KEYS = {
    "format_version",
    "generation_id",
    "embedding_model",
    "vector_dimension",
    "index_kind",
    "vector_count",
    "vector_ids_sha256",
    "index_sha256",
}
_LOWER_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_INT64 = 2**63 - 1


class FaissVectorIndex:
    """Persist stable vector IDs in verified FAISS generations."""

    def __init__(
        self,
        index_directory: str | Path,
        *,
        embedding_model: str,
        vector_dimension: int,
        create_if_missing: bool = False,
    ) -> None:
        if not embedding_model.strip():
            raise ConfigurationMismatchError("FAISS embedding model must not be empty")
        if (
            isinstance(vector_dimension, bool)
            or not isinstance(vector_dimension, int)
            or vector_dimension <= 0
        ):
            raise ConfigurationMismatchError("FAISS vector dimension must be a positive integer")

        self._directory = Path(index_directory)
        self._embedding_model = embedding_model
        self._vector_dimension = vector_dimension
        self._lock = RLock()
        self._final_index = self._directory / _FINAL_INDEX_NAME
        self._final_metadata = self._directory / _FINAL_METADATA_NAME

        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ServiceUnavailableError("FAISS index directory is unavailable") from error

        index_exists = self._final_index.is_file()
        metadata_exists = self._final_metadata.is_file()
        if index_exists != metadata_exists:
            raise ServiceUnavailableError("FAISS final index and metadata must both exist")
        if not index_exists:
            if not create_if_missing:
                raise ServiceUnavailableError("FAISS final index generation is missing")
            empty = _new_index(self._vector_dimension)
            try:
                self._index = self._persist_generation(empty, expected_vector_id=None)
            except IndexingError as error:
                raise ServiceUnavailableError("FAISS empty generation could not be created") from error
        else:
            self._index = self._load_startup_generation()

        self._cleanup_stale_temporaries()

    def add(self, *, vector_id: int, embedding: Embedding) -> None:
        """Replace one stable ID in a clone and durably publish that generation."""
        _validate_vector_id(vector_id)
        vector = self._validated_vector(embedding)

        with self._lock:
            try:
                candidate = faiss.clone_index(self._index)
                vector_ids = np.asarray([vector_id], dtype=np.int64)
                candidate.remove_ids(faiss.IDSelectorBatch(vector_ids))
                candidate.add_with_ids(vector, vector_ids)
            except RuntimeError as error:
                raise IndexingError("FAISS in-memory update failed") from error
            self._index = self._persist_generation(
                candidate,
                expected_vector_id=vector_id,
            )

    def _validated_vector(self, embedding: Embedding) -> np.ndarray[Any, np.dtype[np.float32]]:
        if embedding.model_id != self._embedding_model:
            raise IndexingError("Embedding model does not match the FAISS configuration")
        if embedding.dimension != self._vector_dimension:
            raise IndexingError("Embedding dimension does not match the FAISS configuration")
        if len(embedding.values) != self._vector_dimension:
            raise IndexingError("Embedding value count does not match the FAISS configuration")
        float32_limit = float(np.finfo(np.float32).max)
        if not all(math.isfinite(value) and abs(value) <= float32_limit for value in embedding.values):
            raise IndexingError("Embedding contains a non-finite float32 value")
        vector = np.asarray([embedding.values], dtype=np.float32)
        if not np.isfinite(vector).all():
            raise IndexingError("Embedding contains a non-finite float32 value")
        return vector

    def _persist_generation(
        self,
        candidate: Any,
        *,
        expected_vector_id: int | None,
    ) -> Any:
        generation_id = str(uuid4())
        index_temporary = self._directory / f"memory.faiss.{generation_id}.tmp"
        metadata_temporary = self._directory / f"memory.faiss.meta.{generation_id}.json.tmp"

        try:
            faiss.write_index(candidate, str(index_temporary))
            _file_sync(index_temporary)
            index_checksum = _file_sha256(index_temporary)
            vector_ids = _index_vector_ids(candidate)
            metadata = {
                "format_version": _FORMAT_VERSION,
                "generation_id": generation_id,
                "embedding_model": self._embedding_model,
                "vector_dimension": self._vector_dimension,
                "index_kind": _INDEX_KIND,
                "vector_count": len(vector_ids),
                "vector_ids_sha256": _vector_ids_sha256(vector_ids),
                "index_sha256": index_checksum,
            }
            _write_canonical_metadata(metadata_temporary, metadata)
            self._verify_pair(
                index_temporary,
                metadata_temporary,
                expected_generation_id=generation_id,
                expected_vector_id=expected_vector_id,
            )

            os.replace(index_temporary, self._final_index)
            os.replace(metadata_temporary, self._final_metadata)
            return self._verify_pair(
                self._final_index,
                self._final_metadata,
                expected_generation_id=generation_id,
                expected_vector_id=expected_vector_id,
            )
        except (ConfigurationMismatchError, ServiceUnavailableError, OSError, RuntimeError) as error:
            raise IndexingError("FAISS generation persistence failed") from error

    def _load_startup_generation(self) -> Any:
        return self._verify_pair(
            self._final_index,
            self._final_metadata,
            expected_generation_id=None,
            expected_vector_id=None,
        )

    def _verify_pair(
        self,
        index_path: Path,
        metadata_path: Path,
        *,
        expected_generation_id: str | None,
        expected_vector_id: int | None,
    ) -> Any:
        metadata = _read_metadata(metadata_path)
        _validate_metadata(
            metadata,
            embedding_model=self._embedding_model,
            vector_dimension=self._vector_dimension,
            expected_generation_id=expected_generation_id,
        )
        try:
            if _file_sha256(index_path) != metadata["index_sha256"]:
                raise ServiceUnavailableError("FAISS index checksum mismatch")
            index = faiss.read_index(str(index_path))
        except OSError as error:
            raise ServiceUnavailableError("FAISS index file is unavailable") from error
        except RuntimeError as error:
            raise ServiceUnavailableError("FAISS index file is invalid") from error

        _validate_index(index, self._vector_dimension)
        vector_ids = _index_vector_ids(index)
        if len(vector_ids) != metadata["vector_count"]:
            raise ServiceUnavailableError("FAISS vector count does not match metadata")
        if _vector_ids_sha256(vector_ids) != metadata["vector_ids_sha256"]:
            raise ServiceUnavailableError("FAISS vector-ID checksum mismatch")
        if expected_vector_id is not None and expected_vector_id not in vector_ids:
            raise ServiceUnavailableError("FAISS generation does not contain the expected vector ID")
        return index

    def _cleanup_stale_temporaries(self) -> None:
        patterns = ("memory.faiss.*.tmp", "memory.faiss.meta.*.json.tmp")
        for pattern in patterns:
            for path in self._directory.glob(pattern):
                try:
                    path.unlink()
                except OSError:
                    pass


def _new_index(vector_dimension: int) -> Any:
    return faiss.IndexIDMap2(faiss.IndexFlatIP(vector_dimension))


def _validate_vector_id(vector_id: int) -> None:
    if (
        isinstance(vector_id, bool)
        or not isinstance(vector_id, int)
        or vector_id <= 0
        or vector_id > _MAX_INT64
    ):
        raise IndexingError("FAISS vector ID must be a positive signed-int64 integer")


def _read_metadata(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ServiceUnavailableError("FAISS metadata is unavailable or malformed") from error
    if not isinstance(parsed, dict):
        raise ServiceUnavailableError("FAISS metadata must be a JSON object")
    metadata = {str(key): value for key, value in parsed.items()}
    try:
        canonical = _canonical_metadata_bytes(metadata)
    except (TypeError, ValueError) as error:
        raise ServiceUnavailableError("FAISS metadata is not canonical JSON") from error
    if raw != canonical:
        raise ServiceUnavailableError("FAISS metadata is not canonical JSON")
    return metadata


def _validate_metadata(
    metadata: dict[str, object],
    *,
    embedding_model: str,
    vector_dimension: int,
    expected_generation_id: str | None,
) -> None:
    if set(metadata) != _METADATA_KEYS:
        raise ServiceUnavailableError("FAISS metadata schema is invalid")
    if type(metadata["format_version"]) is not int or metadata["format_version"] != _FORMAT_VERSION:
        raise ConfigurationMismatchError("FAISS metadata format version does not match")
    generation_id = metadata["generation_id"]
    if not isinstance(generation_id, str) or not _is_lower_uuid4(generation_id):
        raise ServiceUnavailableError("FAISS generation ID is invalid")
    if expected_generation_id is not None and generation_id != expected_generation_id:
        raise ServiceUnavailableError("FAISS generation ID does not match")
    if metadata["embedding_model"] != embedding_model:
        raise ConfigurationMismatchError("FAISS embedding model does not match")
    if type(metadata["vector_dimension"]) is not int:
        raise ServiceUnavailableError("FAISS vector dimension metadata is invalid")
    if metadata["vector_dimension"] != vector_dimension:
        raise ConfigurationMismatchError("FAISS vector dimension does not match")
    if metadata["index_kind"] != _INDEX_KIND:
        raise ConfigurationMismatchError("FAISS index kind does not match")
    if type(metadata["vector_count"]) is not int or int(metadata["vector_count"]) < 0:
        raise ServiceUnavailableError("FAISS vector count metadata is invalid")
    for key in ("vector_ids_sha256", "index_sha256"):
        value = metadata[key]
        if not isinstance(value, str) or _LOWER_HEX_SHA256.fullmatch(value) is None:
            raise ServiceUnavailableError(f"FAISS {key} is invalid")


def _validate_index(index: Any, vector_dimension: int) -> None:
    if type(index).__name__ != "IndexIDMap2":
        raise ServiceUnavailableError("FAISS index is not IndexIDMap2")
    wrapped = faiss.downcast_index(index.index)
    if type(wrapped).__name__ != "IndexFlatIP" or index.metric_type != faiss.METRIC_INNER_PRODUCT:
        raise ServiceUnavailableError("FAISS index is not backed by IndexFlatIP")
    if index.d != vector_dimension:
        raise ConfigurationMismatchError("FAISS index dimension does not match")
    vector_ids = _index_vector_ids(index)
    if len(vector_ids) != index.ntotal or len(vector_ids) != len(set(vector_ids)):
        raise ServiceUnavailableError("FAISS index contains duplicate or inconsistent vector IDs")
    if any(vector_id <= 0 or vector_id > _MAX_INT64 for vector_id in vector_ids):
        raise ServiceUnavailableError("FAISS index contains an invalid vector ID")


def _index_vector_ids(index: Any) -> list[int]:
    try:
        return [int(value) for value in faiss.vector_to_array(index.id_map)]
    except (AttributeError, RuntimeError) as error:
        raise ServiceUnavailableError("FAISS vector IDs cannot be inspected") from error


def _vector_ids_sha256(vector_ids: list[int]) -> str:
    ordered = sorted(vector_ids)
    encoded = struct.pack(f"<{len(ordered)}q", *ordered)
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ServiceUnavailableError("FAISS index file cannot be checksummed") from error


def _write_canonical_metadata(path: Path, metadata: dict[str, object]) -> None:
    payload = _canonical_metadata_bytes(metadata)
    with path.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _canonical_metadata_bytes(metadata: dict[str, object]) -> bytes:
    return json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _file_sync(path: Path) -> None:
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _is_lower_uuid4(value: str) -> bool:
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value
