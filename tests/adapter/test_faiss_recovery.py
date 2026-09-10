from __future__ import annotations

import json
from pathlib import Path

import pytest

from conversational_memory.application import Embedding, IndexingError
from conversational_memory.application.recovery import (
    RecoveryIndexError,
    RecoveryInventory,
    RecoveryReadiness,
    RecoveryVector,
)
from conversational_memory.infrastructure import FaissVectorIndex

ONE = Embedding(values=(1.0, 0.0), model_id="test-model", dimension=2)
TWO = Embedding(values=(0.0, 1.0), model_id="test-model", dimension=2)


def _inventory(*items: tuple[str, int, Embedding]) -> RecoveryInventory:
    return RecoveryInventory(
        readiness=RecoveryReadiness.READY,
        reason="ready_existing_generation",
        rebuild_items=tuple(
            RecoveryVector(
                memory_id=memory_id,
                user_id="user-1",
                vector_id=vector_id,
                embedding=embedding,
            )
            for memory_id, vector_id, embedding in items
        ),
        pending_count=0,
        failed_count=0,
        cleanup_pending_count=0,
    )


def _recover(directory: Path, inventory: RecoveryInventory):
    return FaissVectorIndex.reconcile_from_inventory(
        directory,
        embedding_model="test-model",
        vector_dimension=2,
        inventory=inventory,
    )


def _metadata(directory: Path) -> bytes:
    return (directory / "memory.faiss.meta.json").read_bytes()


def _rewrite_metadata(directory: Path, **changes: object) -> None:
    path = directory / "memory.faiss.meta.json"
    metadata = json.loads(path.read_text())
    metadata.update(changes)
    path.write_text(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def test_recovery_builds_exact_stable_id_generation(tmp_path: Path) -> None:
    directory = tmp_path / "index"
    result = _recover(directory, _inventory(("m1", 11, ONE), ("m2", 22, TWO)))

    assert result.rebuilt is True
    assert result.vector_count == 2
    index = FaissVectorIndex(
        directory, embedding_model="test-model", vector_dimension=2
    )
    assert {
        hit.vector_id
        for hit in index.search(embedding=ONE, allowed_vector_ids=(11, 22), limit=2)
    } == {11, 22}


def test_recovery_removes_orphans_by_omission(tmp_path: Path) -> None:
    directory = tmp_path / "index"
    _recover(directory, _inventory(("m1", 11, ONE), ("orphan", 99, TWO)))

    result = _recover(directory, _inventory(("m1", 11, ONE)))

    assert result.rebuilt is True
    assert result.orphan_vectors_removed == 1
    metadata = json.loads(_metadata(directory))
    assert metadata["vector_count"] == 1


def test_exact_verified_generation_is_reused_without_publication(tmp_path: Path) -> None:
    directory = tmp_path / "index"
    inventory = _inventory(("m1", 11, ONE))
    _recover(directory, inventory)
    index_before = (directory / "memory.faiss").read_bytes()
    metadata_before = _metadata(directory)

    result = _recover(directory, inventory)

    assert result.rebuilt is False
    assert (directory / "memory.faiss").read_bytes() == index_before
    assert _metadata(directory) == metadata_before


def test_same_ids_with_stale_vectors_are_rebuilt_from_inventory(tmp_path: Path) -> None:
    directory = tmp_path / "index"
    _recover(directory, _inventory(("m1", 11, ONE)))

    result = _recover(directory, _inventory(("m1", 11, TWO)))

    assert result.rebuilt is True
    restarted = FaissVectorIndex(
        directory, embedding_model="test-model", vector_dimension=2
    )
    assert restarted.search(embedding=TWO, allowed_vector_ids=(11,), limit=1)[0].score == 1.0


def test_recovery_publishes_verified_empty_generation(tmp_path: Path) -> None:
    directory = tmp_path / "index"
    result = _recover(directory, _inventory())

    assert result.rebuilt is True
    assert result.vector_count == 0
    assert json.loads(_metadata(directory))["vector_count"] == 0


@pytest.mark.parametrize(
    "corruption",
    ("incomplete", "damaged", "stale-model", "wrong-dimension", "bad-checksum"),
)
def test_real_invalid_durable_generation_is_rebuilt_from_authority(
    tmp_path: Path,
    corruption: str,
) -> None:
    directory = tmp_path / corruption
    inventory = _inventory(("m1", 11, ONE))
    _recover(directory, inventory)
    if corruption == "incomplete":
        (directory / "memory.faiss.meta.json").unlink()
    elif corruption == "damaged":
        (directory / "memory.faiss").write_bytes(b"damaged-index")
    elif corruption == "stale-model":
        _rewrite_metadata(directory, embedding_model="stale-model")
    elif corruption == "wrong-dimension":
        _rewrite_metadata(directory, vector_dimension=3)
    else:
        _rewrite_metadata(directory, index_sha256="0" * 64)

    result = _recover(directory, inventory)

    assert result.rebuilt is True
    restarted = FaissVectorIndex(
        directory, embedding_model="test-model", vector_dimension=2
    )
    assert restarted.search(
        embedding=ONE, allowed_vector_ids=(11,), limit=1
    )[0].vector_id == 11


def test_publication_failure_preserves_previous_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "index"
    _recover(directory, _inventory(("m1", 11, ONE)))
    index_before = (directory / "memory.faiss").read_bytes()
    metadata_before = _metadata(directory)

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("forced publication failure")

    monkeypatch.setattr("conversational_memory.infrastructure.faiss_index.os.replace", fail_replace)
    with pytest.raises(RecoveryIndexError) as caught:
        _recover(directory, _inventory(("m2", 22, TWO)))
    assert caught.value.reason == "unavailable_publication"

    assert (directory / "memory.faiss").read_bytes() == index_before
    assert _metadata(directory) == metadata_before


def test_post_publication_verification_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "index"
    original = FaissVectorIndex._verify_pair
    calls = 0

    def fail_final_verification(self: FaissVectorIndex, *args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise IndexingError("forced final verification failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(FaissVectorIndex, "_verify_pair", fail_final_verification)
    with pytest.raises(RecoveryIndexError) as caught:
        _recover(directory, _inventory(("m1", 11, ONE)))
    assert caught.value.reason == "unavailable_post_publication_verification"


def test_real_candidate_build_failure_has_exact_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "conversational_memory.infrastructure.faiss_index.faiss.write_index",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("forced candidate failure")),
    )

    with pytest.raises(RecoveryIndexError) as caught:
        _recover(tmp_path / "index", _inventory(("m1", 11, ONE)))

    assert caught.value.reason == "unavailable_rebuild"


def test_recovery_fsyncs_directory_before_reporting_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(
        "conversational_memory.infrastructure.faiss_index._directory_sync",
        lambda path: calls.append(path),
    )

    _recover(tmp_path / "index", _inventory(("m1", 11, ONE)))

    assert calls == [tmp_path / "index"]


def test_directory_fsync_failure_is_classified_as_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_sync(path: Path) -> None:
        raise OSError("forced directory fsync failure")

    monkeypatch.setattr(
        "conversational_memory.infrastructure.faiss_index._directory_sync", fail_sync
    )

    with pytest.raises(IndexingError, match="unavailable_publication"):
        _recover(tmp_path / "index", _inventory(("m1", 11, ONE)))
