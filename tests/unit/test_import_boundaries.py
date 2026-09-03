"""Layering contracts (18_REPOSITORY_STRUCTURE.md section 1).

These are the rules that keep "the Planner must not know API URLs" from
decaying into a comment. An import-linter configuration should express them at
build time; until then they are asserted here, because the boundary is the
architecture.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2] / "backend" / "orca"


def _modules(package: str):
    return sorted((ROOT / package).rglob("*.py"))


def _imports(path: pathlib.Path) -> set[str]:
    """Every ORCA module this file imports, as a dotted suffix path."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # Relative imports: resolve against the file's package depth.
            if node.level:
                parts = list(path.relative_to(ROOT).parts[:-1])
                up = node.level - 1
                base = parts[:len(parts) - up] if up else parts
                found.add(".".join([*base, *(node.module or "").split(".")]))
            elif node.module:
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
    return found


def _touches(imports: set[str], package: str) -> bool:
    return any(i == package or i.startswith(f"{package}.")
               or f".{package}." in i or i.endswith(f".{package}")
               for i in imports)


class TestAgentsNeverReachAdapters:
    @pytest.mark.parametrize("path", _modules("agents"), ids=lambda p: p.name)
    def test_no_adapter_import(self, path):
        assert not _touches(_imports(path), "adapters"), \
            f"{path.name} imports adapters/; the registry is the seam"


class TestGraphNeverReachesAdapters:
    @pytest.mark.parametrize("path", _modules("graph"), ids=lambda p: p.name)
    def test_no_adapter_import(self, path):
        assert not _touches(_imports(path), "adapters"), \
            f"{path.name} imports adapters/"


class TestKernelsNeverImportAnLLM:
    """A model must not be able to change a computed number or a verdict."""

    @pytest.mark.parametrize("path",
                             _modules("geospatial") + _modules("assessment"),
                             ids=lambda p: p.name)
    def test_no_llm_import(self, path):
        assert not _touches(_imports(path), "llm"), \
            f"{path.name} imports llm/; kernels must stay deterministic"


class TestSchemasImportNothingFromOrca:
    @pytest.mark.parametrize("path", _modules("schemas"), ids=lambda p: p.name)
    def test_schemas_are_a_leaf(self, path):
        for package in ("adapters", "tools", "agents", "graph", "assessment",
                        "geospatial", "llm"):
            assert not _touches(_imports(path), package), \
                f"{path.name} imports {package}/; schemas must be a leaf"


class TestNoCredentialsOrUrlsOutsideAdapters:
    @pytest.mark.parametrize(
        "path",
        _modules("agents") + _modules("graph") + _modules("assessment")
        + _modules("geospatial"),
        ids=lambda p: p.name)
    def test_no_url_literals(self, path):
        text = path.read_text()
        for marker in ("http://", "https://"):
            for line in text.splitlines():
                if marker in line and not line.lstrip().startswith("#"):
                    pytest.fail(f"{path.name} contains a URL literal: {line.strip()}")
