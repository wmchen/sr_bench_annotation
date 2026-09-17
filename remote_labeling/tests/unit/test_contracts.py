"""Shared protocol fixtures must stay synchronized with the actual API."""

import ast
import json
from pathlib import Path

from remote_labeling.backend.main import create_app


def test_openapi_matches_committed_contract(settings) -> None:
    """An API type change requires regenerating its frontend source contract."""
    path = Path(__file__).parents[2] / "openapi.json"
    assert json.loads(path.read_text()) == create_app(settings).openapi()


def test_runtime_imports_never_reach_desktop_packages() -> None:
    """Scan every backend source, including lazy imports in worker functions."""
    root = Path(__file__).parents[2] / "backend"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [name.name for name in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any(
                name.split(".")[0] in ("anylabeling", "PyQt6", "PySide6")
                for name in modules
            ), path
