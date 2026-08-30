from __future__ import annotations

import ast
import sys
from pathlib import Path

DOMAIN_ROOT = Path(__file__).resolve().parents[3] / "src" / "conversational_memory" / "domain"


def test_domain_has_only_standard_library_dependencies() -> None:
    third_party_imports: set[str] = set()

    for source in DOMAIN_ROOT.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module.split(".")[0]]
            else:
                continue
            third_party_imports.update(
                module
                for module in modules
                if module != "__future__" and module not in sys.stdlib_module_names
            )

    assert not third_party_imports
