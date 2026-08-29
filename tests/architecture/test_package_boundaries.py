from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "src" / "conversational_memory"
LAYERS = {"domain", "application", "infrastructure", "composition", "entrypoints"}
ALLOWED_INTERNAL_DEPENDENCIES = {
    "domain": {"domain"},
    "application": {"application", "domain"},
    "infrastructure": {"infrastructure", "application", "domain"},
    "composition": {"composition", "infrastructure", "application", "domain"},
    "entrypoints": {"entrypoints", "composition", "application"},
}
RUNTIME_DEPENDENCIES = {
    "faiss-cpu>=1.15,<2",
    "numpy>=1.26,<3",
    "pydantic>=2,<3",
    "sentence-transformers>=5,<6",
    "tiktoken>=0.13,<1",
}
DEV_DEPENDENCIES = {"mypy>=1.10,<2", "pytest>=8,<10", "ruff>=0.9,<1"}


def _current_package_parts(path: Path) -> list[str]:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = ["conversational_memory", *relative.parts]
    if parts[-1] != "__init__":
        parts.pop()
    else:
        parts.pop()
    return parts


def _imported_module_parts(node: ast.ImportFrom, source: Path) -> list[str]:
    module_parts = node.module.split(".") if node.module else []
    if node.level == 0:
        return module_parts

    current_package = _current_package_parts(source)
    parent_count = node.level - 1
    if parent_count > len(current_package):
        return []
    base = current_package[: len(current_package) - parent_count]
    return [*base, *module_parts]


def _internal_layer(module_parts: list[str]) -> str | None:
    if len(module_parts) < 2 or module_parts[0] != "conversational_memory":
        return None
    return module_parts[1]


def test_approved_package_layers_exist() -> None:
    package_directories = {
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert package_directories == LAYERS
    for layer in LAYERS:
        assert (PACKAGE_ROOT / layer / "__init__.py").is_file()


def test_internal_imports_follow_the_approved_dependency_direction() -> None:
    violations: list[str] = []

    for source in PACKAGE_ROOT.rglob("*.py"):
        source_layer = source.relative_to(PACKAGE_ROOT).parts[0]
        if source_layer not in LAYERS:
            continue

        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        imported_layers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    layer = _internal_layer(alias.name.split("."))
                    if layer is not None:
                        imported_layers.add(layer)
            elif isinstance(node, ast.ImportFrom):
                layer = _internal_layer(_imported_module_parts(node, source))
                if layer is not None:
                    imported_layers.add(layer)

        disallowed = imported_layers - ALLOWED_INTERNAL_DEPENDENCIES[source_layer]
        for dependency in sorted(disallowed):
            violations.append(
                f"{source.relative_to(ROOT)}: {source_layer} must not import {dependency}"
            )

    assert not violations, "\n".join(violations)


def test_pyproject_is_the_integrated_dependency_authority() -> None:
    pyproject_path = ROOT / "pyproject.toml"
    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    configuration = tomllib.loads(pyproject_text)

    assert configuration["build-system"]["build-backend"] == "setuptools.build_meta"
    assert configuration["project"]["requires-python"] == ">=3.12,<3.13"
    assert set(configuration["project"]["dependencies"]) == RUNTIME_DEPENDENCIES
    assert set(configuration["project"]["optional-dependencies"]["dev"]) == DEV_DEPENDENCIES
    assert configuration["tool"]["setuptools"]["package-dir"] == {"": "src"}
    assert configuration["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    assert "requirements.txt" not in pyproject_text

    assert (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() == [
        "sentence-transformers>=5.0.0",
        "numpy>=1.26.0",
    ]


def test_cli_help_resolves_from_the_installed_package() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "conversational_memory.entrypoints.cli", "--help"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Local conversational memory layer" in result.stdout
