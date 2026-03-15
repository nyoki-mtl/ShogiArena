"""Detect Python import cycles under ``src``.

Only imports that resolve to the local ``shogiarena`` package are analyzed.

Usage:
    uv run python tools/detect_python_import_cycles.py
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

ROOT = Path("src")
PACKAGE = "shogiarena"


def build_module_index(root: Path) -> dict[str, Path]:
    module_to_file: dict[str, Path] = {}
    for file in root.rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(root)
        if rel.name == "__init__.py":
            module = f"{PACKAGE}.{'.'.join(rel.parents[0].parts)}" if rel.parents[0].parts else PACKAGE
        else:
            module = f"{PACKAGE}.{'.'.join(rel.with_suffix('').parts)}"
        module_to_file[module] = file
    return module_to_file


def resolve_import_module(current_module: str, node: ast.Import | ast.ImportFrom) -> Iterable[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
        return

    current_parts = current_module.split(".")
    package_parts = current_parts[:-1]

    if node.level and node.level > 0:
        climb = node.level - 1
        if climb > len(package_parts):
            return
        base_parts = package_parts[: len(package_parts) - climb]
    else:
        base_parts = []

    module_parts = node.module.split(".") if node.module else []
    qualified_parts = [*base_parts, *module_parts] if (node.level and node.level > 0) else module_parts

    seen: set[str] = set()
    ordered: list[str] = []

    def add_name(parts: list[str]) -> None:
        if not parts:
            return
        name = ".".join(parts)
        if name not in seen:
            seen.add(name)
            ordered.append(name)

    add_name(qualified_parts)

    import_parent_parts = qualified_parts if qualified_parts else base_parts
    for alias in node.names:
        if alias.name == "*":
            continue
        alias_parts = alias.name.split(".")
        if node.level and node.level > 0:
            add_name([*import_parent_parts, *alias_parts])
        elif node.module:
            add_name([*module_parts, *alias_parts])
        else:
            add_name(alias_parts)

    yield from ordered


def import_is_type_checking(node: ast.AST, mapping: dict[tuple[int, int], bool]) -> bool:
    return mapping.get((node.lineno, node.col_offset), False)


def map_name_to_module(name: str, module_to_file: dict[str, Path]) -> str | None:
    if not name.startswith(f"{PACKAGE}.") and name != PACKAGE:
        return None

    if name in module_to_file:
        return name

    # Accept package prefixes: e.g. ``shogiarena.utils`` imports ``__init__`` module.
    candidates = sorted(module_to_file, key=len, reverse=True)
    for candidate in candidates:
        if name == candidate or name.startswith(f"{candidate}."):
            return candidate
    return None


def detect_type_checking_blocks(tree: ast.AST) -> dict[tuple[int, int], bool]:
    flags: dict[tuple[int, int], bool] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for child in node.body:
                flags[(child.lineno, child.col_offset)] = True
        elif (
            isinstance(node.test, ast.Attribute)
            and isinstance(node.test.value, ast.Name)
            and node.test.value.id == "typing"
            and node.test.attr == "TYPE_CHECKING"
        ):
            for child in node.body:
                flags[(child.lineno, child.col_offset)] = True
        elif (
            isinstance(node.test, ast.Attribute)
            and isinstance(node.test.value, ast.Name)
            and node.test.value.id == "tp"
            and node.test.attr == "TYPE_CHECKING"
        ):
            for child in node.body:
                flags[(child.lineno, child.col_offset)] = True
    return flags


def build_graph(module_to_file: dict[str, Path]) -> tuple[dict[str, set[str]], set[str]]:
    graph: defaultdict[str, set[str]] = defaultdict(set)
    modules = set(module_to_file)

    for module, file in module_to_file.items():
        graph[module]  # initialize
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        type_checking = detect_type_checking_blocks(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            if (node.lineno, node.col_offset) in type_checking:
                continue
            for raw in resolve_import_module(module, node):
                target = map_name_to_module(raw, module_to_file)
                if target and target != module:
                    graph[module].add(target)
                elif raw in modules:
                    graph[module].add(raw)

    return graph, modules


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    next_index = 0
    cycles: list[list[str]] = []

    def strongconnect(node: str) -> None:
        nonlocal next_index
        index[node] = next_index
        low[node] = next_index
        next_index += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in graph[node]:
            if neighbor not in index:
                strongconnect(neighbor)
                low[node] = min(low[node], low[neighbor])
            elif neighbor in on_stack:
                low[node] = min(low[node], index[neighbor])

        if low[node] == index[node]:
            component: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                component.append(w)
                if w == node:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

    for module in graph:
        if module not in index:
            strongconnect(module)

    return cycles


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect import cycles in Python modules under src/")
    parser.add_argument("--root", default="src", help="Project source root")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    module_to_file = build_module_index(root)
    graph, _ = build_graph(module_to_file)
    cycles = find_cycles(graph)

    if cycles:
        print(f"Python import cycle count: {len(cycles)}")
        for idx, cycle in enumerate(cycles, 1):
            print(f"  - PYTHON-CYCLE-{idx}: " + " -> ".join(cycle))
        return 1

    print("Python import cycle count: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
