from __future__ import annotations

import hashlib
import json
import os
import struct
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import faiss
import numpy as np
import pytest

from conversational_memory.application import (
    ConfigurationMismatchError,
    Embedding,
    IndexingError,
    ServiceUnavailableError,
)
from conversational_memory.infrastructure import FaissVectorIndex
from conversational_memory.infrastructure import faiss_index as faiss_module

MODEL = "test-model"
DIMENSION = 3
EMBEDDING = Embedding(values=(0.25, -0.5, 0.75), model_id=MODEL, dimension=DIMENSION)


def _create(directory: Path) -> FaissVectorIndex:
    return FaissVectorIndex(
        directory,
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )


def _restart(directory: Path) -> FaissVectorIndex:
    return FaissVectorIndex(
        directory,
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
    )


def _metadata(directory: Path) -> dict[str, object]:
    return json.loads((directory / "memory.faiss.meta.json").read_text(encoding="utf-8"))


def _ids(directory: Path) -> list[int]:
    index = faiss.read_index(str(directory / "memory.faiss"))
    return [int(value) for value in faiss.vector_to_array(index.id_map)]


def _write_metadata(directory: Path, metadata: dict[str, object]) -> None:
    (directory / "memory.faiss.meta.json").write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def test_add_persists_canonical_generation_and_restarts(tmp_path: Path) -> None:
    index = _create(tmp_path)
    index.add(vector_id=41, embedding=EMBEDDING)

    metadata_bytes = (tmp_path / "memory.faiss.meta.json").read_bytes()
    metadata = _metadata(tmp_path)
    restarted = _restart(tmp_path)

    assert {path.name for path in tmp_path.iterdir()} == {
        "memory.faiss",
        "memory.faiss.meta.json",
    }
    assert not metadata_bytes.endswith(b"\n")
    assert metadata["format_version"] == 1
    assert metadata["embedding_model"] == MODEL
    assert metadata["vector_dimension"] == DIMENSION
    assert metadata["index_kind"] == "IndexIDMap2(IndexFlatIP)"
    assert metadata["vector_count"] == 1
    assert metadata["index_sha256"] == hashlib.sha256(
        (tmp_path / "memory.faiss").read_bytes()
    ).hexdigest()
    assert _ids(tmp_path) == [41]
    restarted.add(vector_id=42, embedding=EMBEDDING)
    assert sorted(_ids(tmp_path)) == [41, 42]


def test_repeated_vector_id_replaces_without_duplicate(tmp_path: Path) -> None:
    index = _create(tmp_path)
    index.add(vector_id=41, embedding=EMBEDDING)
    replacement = Embedding(values=(0.5, 0.25, -0.75), model_id=MODEL, dimension=DIMENSION)

    index.add(vector_id=41, embedding=replacement)
    _restart(tmp_path)

    assert _ids(tmp_path) == [41]
    assert _metadata(tmp_path)["vector_count"] == 1


@pytest.mark.parametrize("vector_id", [True, 0, -1, 2**63, 1.5, "1"])
def test_invalid_vector_id_is_rejected_without_mutation(tmp_path: Path, vector_id: object) -> None:
    index = _create(tmp_path)
    before = (tmp_path / "memory.faiss").read_bytes()

    with pytest.raises(IndexingError, match="positive signed-int64"):
        index.add(vector_id=vector_id, embedding=EMBEDDING)  # type: ignore[arg-type]

    assert (tmp_path / "memory.faiss").read_bytes() == before


@pytest.mark.parametrize(
    "embedding",
    [
        Embedding(values=(0.25, -0.5, 0.75), model_id="other", dimension=DIMENSION),
        Embedding(values=(0.25, -0.5), model_id=MODEL, dimension=2),
        Embedding(values=(1e300, 0.0, 0.0), model_id=MODEL, dimension=DIMENSION),
    ],
)
def test_invalid_embedding_is_rejected_without_mutation(
    tmp_path: Path,
    embedding: Embedding,
) -> None:
    index = _create(tmp_path)
    before = (tmp_path / "memory.faiss").read_bytes()

    with pytest.raises(IndexingError):
        index.add(vector_id=41, embedding=embedding)

    assert (tmp_path / "memory.faiss").read_bytes() == before


@pytest.mark.parametrize("missing_name", ["memory.faiss", "memory.faiss.meta.json"])
def test_startup_rejects_missing_final_pair(tmp_path: Path, missing_name: str) -> None:
    _create(tmp_path)
    (tmp_path / missing_name).unlink()

    with pytest.raises(ServiceUnavailableError, match="must both exist"):
        _restart(tmp_path)


def test_startup_rejects_two_missing_finals_without_explicit_empty_creation(
    tmp_path: Path,
) -> None:
    with pytest.raises(ServiceUnavailableError, match="generation is missing"):
        _restart(tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("generation_id", "00000000-0000-0000-0000-000000000000", ServiceUnavailableError),
        ("embedding_model", "other-model", ConfigurationMismatchError),
        ("vector_dimension", DIMENSION + 1, ConfigurationMismatchError),
    ],
)
def test_startup_rejects_generation_or_configuration_mismatch(
    tmp_path: Path,
    field: str,
    value: object,
    error_type: type[Exception],
) -> None:
    _create(tmp_path)
    metadata = _metadata(tmp_path)
    metadata[field] = value
    _write_metadata(tmp_path, metadata)

    with pytest.raises(error_type):
        _restart(tmp_path)


def test_startup_rejects_index_checksum_mismatch(tmp_path: Path) -> None:
    _create(tmp_path)
    with (tmp_path / "memory.faiss").open("ab") as stream:
        stream.write(b"corruption")

    with pytest.raises(ServiceUnavailableError, match="checksum mismatch"):
        _restart(tmp_path)


def test_startup_rejects_malformed_metadata(tmp_path: Path) -> None:
    _create(tmp_path)
    (tmp_path / "memory.faiss.meta.json").write_text("{", encoding="utf-8")

    with pytest.raises(ServiceUnavailableError, match="malformed"):
        _restart(tmp_path)


def test_startup_rejects_valid_json_with_noncanonical_byte_encoding(tmp_path: Path) -> None:
    _create(tmp_path)
    metadata = _metadata(tmp_path)
    (tmp_path / "memory.faiss.meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(ServiceUnavailableError, match="not canonical JSON"):
        _restart(tmp_path)


@pytest.mark.parametrize("schema_change", ["extra", "missing"])
def test_startup_rejects_metadata_with_inexact_schema(
    tmp_path: Path,
    schema_change: str,
) -> None:
    _create(tmp_path)
    metadata = _metadata(tmp_path)
    if schema_change == "extra":
        metadata["unexpected"] = True
    else:
        metadata.pop("vector_count")
    _write_metadata(tmp_path, metadata)

    with pytest.raises(ServiceUnavailableError, match="schema is invalid"):
        _restart(tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("format_version", 2, "format version does not match"),
        ("index_kind", "IndexIDMap2(IndexFlatL2)", "index kind does not match"),
    ],
)
def test_startup_rejects_unsupported_format_or_index_kind(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    _create(tmp_path)
    metadata = _metadata(tmp_path)
    metadata[field] = value
    _write_metadata(tmp_path, metadata)

    with pytest.raises(ConfigurationMismatchError, match=message):
        _restart(tmp_path)


def test_startup_rejects_incorrect_syntactically_valid_vector_id_checksum(
    tmp_path: Path,
) -> None:
    _create(tmp_path)
    metadata = _metadata(tmp_path)
    metadata["vector_ids_sha256"] = "0" * 64
    _write_metadata(tmp_path, metadata)

    with pytest.raises(ServiceUnavailableError, match="vector-ID checksum mismatch"):
        _restart(tmp_path)


def test_invalid_finals_do_not_trigger_stale_temporary_cleanup(tmp_path: Path) -> None:
    _create(tmp_path)
    stale_index = tmp_path / "memory.faiss.00000000-0000-4000-8000-000000000000.tmp"
    stale_metadata = tmp_path / "memory.faiss.meta.00000000-0000-4000-8000-000000000000.json.tmp"
    stale_index.write_bytes(b"stale index")
    stale_metadata.write_bytes(b"stale metadata")
    with (tmp_path / "memory.faiss").open("ab") as stream:
        stream.write(b"corruption")

    with pytest.raises(ServiceUnavailableError, match="checksum mismatch"):
        _restart(tmp_path)

    assert stale_index.exists()
    assert stale_metadata.exists()


def test_startup_rejects_invalid_index_contents_with_matching_checksum(tmp_path: Path) -> None:
    _create(tmp_path)
    invalid = faiss.IndexIDMap2(faiss.IndexFlatL2(DIMENSION))
    invalid.add_with_ids(
        np.asarray([[0.1, 0.2, 0.3]], dtype=np.float32),
        np.asarray([41], dtype=np.int64),
    )
    faiss.write_index(invalid, str(tmp_path / "memory.faiss"))
    metadata = _metadata(tmp_path)
    metadata["index_sha256"] = hashlib.sha256(
        (tmp_path / "memory.faiss").read_bytes()
    ).hexdigest()
    metadata["vector_count"] = 1
    metadata["vector_ids_sha256"] = hashlib.sha256((41).to_bytes(8, "little", signed=True)).hexdigest()
    _write_metadata(tmp_path, metadata)

    with pytest.raises(ServiceUnavailableError, match="IndexFlatIP"):
        _restart(tmp_path)


def test_startup_rejects_duplicate_vector_ids_with_matching_checksums(tmp_path: Path) -> None:
    _create(tmp_path)
    duplicate = faiss.IndexIDMap2(faiss.IndexFlatIP(DIMENSION))
    duplicate.add_with_ids(
        np.asarray([[0.1, 0.2, 0.3], [0.3, 0.2, 0.1]], dtype=np.float32),
        np.asarray([41, 41], dtype=np.int64),
    )
    faiss.write_index(duplicate, str(tmp_path / "memory.faiss"))
    metadata = _metadata(tmp_path)
    metadata["index_sha256"] = hashlib.sha256(
        (tmp_path / "memory.faiss").read_bytes()
    ).hexdigest()
    metadata["vector_count"] = 2
    metadata["vector_ids_sha256"] = hashlib.sha256(struct.pack("<2q", 41, 41)).hexdigest()
    _write_metadata(tmp_path, metadata)

    with pytest.raises(ServiceUnavailableError, match="duplicate"):
        _restart(tmp_path)


def test_generation_mismatch_fails_before_replacing_finals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _create(tmp_path)
    previous_index = (tmp_path / "memory.faiss").read_bytes()
    previous_metadata = (tmp_path / "memory.faiss.meta.json").read_bytes()
    real_write_metadata = faiss_module._write_canonical_metadata

    def write_mismatched_generation(path: Path, metadata: dict[str, object]) -> None:
        mismatched = dict(metadata)
        mismatched["generation_id"] = str(uuid4())
        real_write_metadata(path, mismatched)

    monkeypatch.setattr(
        faiss_module,
        "_write_canonical_metadata",
        write_mismatched_generation,
    )

    with pytest.raises(IndexingError):
        index.add(vector_id=41, embedding=EMBEDDING)

    assert (tmp_path / "memory.faiss").read_bytes() == previous_index
    assert (tmp_path / "memory.faiss.meta.json").read_bytes() == previous_metadata


def _interrupting_replace(
    real_replace: Callable[[str | bytes | os.PathLike[str] | os.PathLike[bytes], str | bytes | os.PathLike[str] | os.PathLike[bytes]], None],
    *,
    fail_on_call: int,
    fail_after_replace: bool = False,
) -> Callable[[str | bytes | os.PathLike[str] | os.PathLike[bytes], str | bytes | os.PathLike[str] | os.PathLike[bytes]], None]:
    calls = 0

    def replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_on_call and not fail_after_replace:
            raise OSError("simulated interruption")
        real_replace(source, destination)
        if calls == fail_on_call:
            raise OSError("simulated interruption")

    return replace


def test_interruption_before_either_replacement_preserves_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _create(tmp_path)
    index.add(vector_id=41, embedding=EMBEDDING)
    monkeypatch.setattr(
        faiss_module.os,
        "replace",
        _interrupting_replace(os.replace, fail_on_call=1),
    )

    with pytest.raises(IndexingError):
        index.add(vector_id=42, embedding=EMBEDDING)

    _restart(tmp_path)
    assert _ids(tmp_path) == [41]


def test_interruption_between_replacements_fails_closed_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _create(tmp_path)
    index.add(vector_id=41, embedding=EMBEDDING)
    monkeypatch.setattr(
        faiss_module.os,
        "replace",
        _interrupting_replace(os.replace, fail_on_call=2),
    )

    with pytest.raises(IndexingError):
        index.add(vector_id=42, embedding=EMBEDDING)

    with pytest.raises(ServiceUnavailableError, match="checksum mismatch"):
        _restart(tmp_path)


def test_interruption_after_both_replacements_leaves_complete_new_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _create(tmp_path)
    index.add(vector_id=41, embedding=EMBEDDING)
    monkeypatch.setattr(
        faiss_module.os,
        "replace",
        _interrupting_replace(os.replace, fail_on_call=2, fail_after_replace=True),
    )

    with pytest.raises(IndexingError):
        index.add(vector_id=42, embedding=EMBEDDING)

    _restart(tmp_path)
    assert sorted(_ids(tmp_path)) == [41, 42]


def test_startup_ignores_and_removes_stale_temporary_files(tmp_path: Path) -> None:
    _create(tmp_path)
    stale_index = tmp_path / "memory.faiss.00000000-0000-4000-8000-000000000000.tmp"
    stale_metadata = tmp_path / "memory.faiss.meta.00000000-0000-4000-8000-000000000000.json.tmp"
    stale_index.write_bytes(b"not an index")
    stale_metadata.write_bytes(b"not metadata")

    _restart(tmp_path)

    assert not stale_index.exists()
    assert not stale_metadata.exists()


def test_stale_temporary_cleanup_failure_is_nonfatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(tmp_path)
    stale = tmp_path / "memory.faiss.00000000-0000-4000-8000-000000000000.tmp"
    stale.write_bytes(b"stale")
    real_unlink = Path.unlink

    def fail_stale_unlink(path: Path, missing_ok: bool = False) -> None:
        if path == stale:
            raise OSError("simulated cleanup failure")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_stale_unlink)

    _restart(tmp_path)
    assert stale.exists()
