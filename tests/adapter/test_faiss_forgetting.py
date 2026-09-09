from __future__ import annotations

import json
from pathlib import Path

import pytest

from conversational_memory.application import (
    Embedding,
    IndexingError,
    ServiceUnavailableError,
)
from conversational_memory.infrastructure import FaissVectorIndex

EMBEDDING_ONE = Embedding(values=(1.0, 0.0), model_id="test-model", dimension=2)
EMBEDDING_TWO = Embedding(values=(0.0, 1.0), model_id="test-model", dimension=2)


def _index(directory: Path, *, create: bool = False) -> FaissVectorIndex:
    return FaissVectorIndex(
        directory,
        embedding_model="test-model",
        vector_dimension=2,
        create_if_missing=create,
    )


def _generation(directory: Path) -> str:
    metadata = json.loads((directory / "memory.faiss.meta.json").read_text())
    return str(metadata["generation_id"])


def test_targeted_remove_publishes_verified_generation_and_preserves_other_id(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "index"
    index = _index(directory, create=True)
    index.add(vector_id=11, embedding=EMBEDDING_ONE)
    index.add(vector_id=22, embedding=EMBEDDING_TWO)
    before = _generation(directory)

    index.remove(vector_id=11)

    assert _generation(directory) != before
    restarted = _index(directory)
    assert restarted.search(
        embedding=EMBEDDING_TWO, allowed_vector_ids=(22,), limit=1
    )[0].vector_id == 22
    with pytest.raises(ServiceUnavailableError, match="missing an authorized vector ID"):
        restarted.search(
            embedding=EMBEDDING_ONE, allowed_vector_ids=(11,), limit=1
        )


def test_remove_of_already_absent_id_does_not_republish(tmp_path: Path) -> None:
    directory = tmp_path / "index"
    index = _index(directory, create=True)
    index.add(vector_id=22, embedding=EMBEDDING_TWO)
    metadata_before = (directory / "memory.faiss.meta.json").read_bytes()
    index_before = (directory / "memory.faiss").read_bytes()

    index.remove(vector_id=11)

    assert (directory / "memory.faiss.meta.json").read_bytes() == metadata_before
    assert (directory / "memory.faiss").read_bytes() == index_before


def test_remove_publication_failure_keeps_original_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "index"
    index = _index(directory, create=True)
    index.add(vector_id=11, embedding=EMBEDDING_ONE)
    index.add(vector_id=22, embedding=EMBEDDING_TWO)
    before = _generation(directory)

    def fail_publish(*args: object, **kwargs: object) -> object:
        raise IndexingError("forced removal failure")

    monkeypatch.setattr(index, "_persist_generation", fail_publish)
    with pytest.raises(IndexingError, match="forced removal failure"):
        index.remove(vector_id=11)

    assert _generation(directory) == before
    restarted = _index(directory)
    hits = restarted.search(
        embedding=EMBEDDING_ONE, allowed_vector_ids=(11, 22), limit=2
    )
    assert {hit.vector_id for hit in hits} == {11, 22}


def test_remove_fails_closed_when_both_durable_generation_files_are_missing(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "index"
    index = _index(directory, create=True)
    (directory / "memory.faiss").unlink()
    (directory / "memory.faiss.meta.json").unlink()

    with pytest.raises(IndexingError, match="current generation is missing"):
        index.remove(vector_id=11)


def test_remove_fails_closed_for_partial_durable_generation(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "index"
    index = _index(directory, create=True)
    index.add(vector_id=11, embedding=EMBEDDING_ONE)
    (directory / "memory.faiss.meta.json").unlink()

    with pytest.raises(IndexingError, match="current generation is incomplete"):
        index.remove(vector_id=11)
