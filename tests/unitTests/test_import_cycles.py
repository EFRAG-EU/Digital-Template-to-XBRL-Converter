import ast
import graphlib
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent.parent / "src"
_PACKAGE = "mireport"


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(_SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _type_checking_linenos(tree: ast.Module) -> frozenset[int]:
    """Return line numbers of imports inside `if TYPE_CHECKING:` blocks."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            if (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            ):
                for child in ast.walk(node):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        guarded.add(child.lineno)
    return frozenset(guarded)


def _resolve_relative(module: str | None, level: int, package: str) -> str | None:
    if level == 0:
        return module
    base_parts = package.split(".")[: -(level - 1) or None]
    base = ".".join(base_parts)
    return f"{base}.{module}" if module else base


def _internal_deps(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    guarded = _type_checking_linenos(tree)
    module = _module_name(path)
    package = module.rsplit(".", 1)[0] if "." in module else module

    deps: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if node.lineno in guarded:
                continue
            for alias in node.names:
                if alias.name == _PACKAGE or alias.name.startswith(_PACKAGE + "."):
                    deps.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.lineno in guarded:
                continue
            resolved = _resolve_relative(node.module, node.level, package)
            if resolved and (
                resolved == _PACKAGE or resolved.startswith(_PACKAGE + ".")
            ):
                deps.add(resolved)
    return deps


def test_no_import_cycles() -> None:
    """Fail immediately if any circular imports are introduced in mireport."""
    graph: dict[str, set[str]] = {
        _module_name(p): _internal_deps(p) for p in (_SRC / _PACKAGE).rglob("*.py")
    }
    try:
        list(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError as exc:
        cycle = " \u2192 ".join(exc.args[1])
        pytest.fail(f"Circular import cycle detected: {cycle}")
