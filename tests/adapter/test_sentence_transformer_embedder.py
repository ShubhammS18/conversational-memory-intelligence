from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from conversational_memory.application import (
    ConfigurationMismatchError,
    IndexingError,
    ServiceUnavailableError,
)
from conversational_memory.infrastructure import (
    ALL_MPNET_BASE_V2_DIMENSION,
    ALL_MPNET_BASE_V2_LOAD_NAME,
    ALL_MPNET_BASE_V2_MODEL_ID,
    ALL_MPNET_BASE_V2_REVISION,
    SentenceTransformerEmbedder,
)
from conversational_memory.infrastructure import sentence_transformer_embedder as module


class FakeModel:
    def __init__(
        self,
        *,
        dimension: int = ALL_MPNET_BASE_V2_DIMENSION,
        output: object | None = None,
        encode_error: Exception | None = None,
    ) -> None:
        self.dimension = dimension
        self.output = (
            np.concatenate(
                (
                    np.asarray([1.0], dtype=np.float64),
                    np.zeros(ALL_MPNET_BASE_V2_DIMENSION - 1, dtype=np.float64),
                )
            )
            if output is None
            else output
        )
        self.encode_error = encode_error
        self.dimension_calls = 0
        self.encode_calls: list[tuple[str, dict[str, object]]] = []

    def get_embedding_dimension(self) -> int:
        self.dimension_calls += 1
        return self.dimension

    def encode(self, content: str, **kwargs: object) -> object:
        self.encode_calls.append((content, kwargs))
        if self.encode_error is not None:
            raise self.encode_error
        return self.output


class LegacyDimensionModel:
    def __init__(self) -> None:
        self.dimension_calls = 0

    def get_sentence_embedding_dimension(self) -> int:
        self.dimension_calls += 1
        return ALL_MPNET_BASE_V2_DIMENSION


class FailingModernDimensionModel:
    def __init__(self) -> None:
        self.modern_calls = 0
        self.legacy_calls = 0

    def get_embedding_dimension(self) -> int:
        self.modern_calls += 1
        raise RuntimeError("modern accessor failed")

    def get_sentence_embedding_dimension(self) -> int:
        self.legacy_calls += 1
        return ALL_MPNET_BASE_V2_DIMENSION


def _install_model(
    monkeypatch: pytest.MonkeyPatch,
    model: object,
) -> list[tuple[str, dict[str, object]]]:
    construction_calls: list[tuple[str, dict[str, object]]] = []

    def construct(name: str, **kwargs: object) -> object:
        construction_calls.append((name, kwargs))
        return model

    monkeypatch.setattr(module, "SentenceTransformer", construct)
    return construction_calls


def test_loads_immutable_model_from_configured_cache_on_cpu_and_returns_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel()
    construction_calls = _install_model(monkeypatch, model)
    embedder = SentenceTransformerEmbedder(cache_directory=tmp_path)

    result = embedder.embed("normalized content")

    assert construction_calls == [
        (
            ALL_MPNET_BASE_V2_LOAD_NAME,
            {
                "revision": ALL_MPNET_BASE_V2_REVISION,
                "cache_folder": str(tmp_path),
                "device": "cpu",
                "local_files_only": True,
            },
        )
    ]
    assert model.dimension_calls == 1
    assert model.encode_calls == [
        (
            "normalized content",
            {
                "convert_to_numpy": True,
                "normalize_embeddings": True,
                "precision": "float32",
                "device": "cpu",
                "show_progress_bar": False,
            },
        )
    ]
    assert result.model_id == ALL_MPNET_BASE_V2_MODEL_ID
    assert result.dimension == ALL_MPNET_BASE_V2_DIMENSION
    assert len(result.values) == ALL_MPNET_BASE_V2_DIMENSION
    assert np.asarray(result.values, dtype=np.float32).dtype == np.float32
    assert np.isclose(np.linalg.norm(result.values), 1.0, rtol=1e-5, atol=1e-6)


def test_uses_legacy_dimension_accessor_when_modern_accessor_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = LegacyDimensionModel()
    _install_model(monkeypatch, model)

    SentenceTransformerEmbedder(cache_directory=tmp_path)

    assert model.dimension_calls == 1


def test_selected_modern_dimension_accessor_failure_does_not_retry_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FailingModernDimensionModel()
    _install_model(monkeypatch, model)

    with pytest.raises(ServiceUnavailableError, match="could not be loaded") as captured:
        SentenceTransformerEmbedder(cache_directory=tmp_path)

    assert model.modern_calls == 1
    assert model.legacy_calls == 0
    assert isinstance(captured.value.__cause__, RuntimeError)


def test_missing_cache_fails_without_attempting_model_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel()
    construction_calls = _install_model(monkeypatch, model)

    with pytest.raises(ServiceUnavailableError, match="cache directory is unavailable"):
        SentenceTransformerEmbedder(cache_directory=tmp_path / "missing")

    assert construction_calls == []


def test_model_load_failure_is_service_unavailable_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fail_load(name: str, **kwargs: object) -> None:
        calls.append(name)
        raise OSError("cached model is incomplete")

    monkeypatch.setattr(module, "SentenceTransformer", fail_load)

    with pytest.raises(ServiceUnavailableError, match="could not be loaded") as captured:
        SentenceTransformerEmbedder(cache_directory=tmp_path)

    assert calls == [ALL_MPNET_BASE_V2_LOAD_NAME]
    assert isinstance(captured.value.__cause__, OSError)


def test_loaded_dimension_mismatch_is_configuration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_model(monkeypatch, FakeModel(dimension=384))

    with pytest.raises(ConfigurationMismatchError, match="dimension does not match"):
        SentenceTransformerEmbedder(cache_directory=tmp_path)


def test_encoding_failure_is_indexing_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_model(monkeypatch, FakeModel(encode_error=RuntimeError("encode failed")))
    embedder = SentenceTransformerEmbedder(cache_directory=tmp_path)

    with pytest.raises(IndexingError, match="encoding failed") as captured:
        embedder.embed("content")

    assert isinstance(captured.value.__cause__, RuntimeError)


@pytest.mark.parametrize(
    ("output", "reason"),
    [
        (np.zeros(384, dtype=np.float32), "shape is invalid"),
        (
            np.full(ALL_MPNET_BASE_V2_DIMENSION, np.nan, dtype=np.float32),
            "non-finite",
        ),
        (
            np.ones(ALL_MPNET_BASE_V2_DIMENSION, dtype=np.float32),
            "not L2-normalized",
        ),
        (object(), "not a float32 vector"),
    ],
)
def test_malformed_model_output_is_indexing_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: object,
    reason: str,
) -> None:
    _install_model(monkeypatch, FakeModel(output=output))
    embedder = SentenceTransformerEmbedder(cache_directory=tmp_path)

    with pytest.raises(IndexingError, match=reason):
        embedder.embed("content")
