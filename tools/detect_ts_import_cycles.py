"""Detect TypeScript relative import cycles in frontend source tree."""

from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict
from pathlib import Path

IMPORT_PATTERNS = (
    re.compile(r"(?m)^\s*import(?:\s+type)?\s+[^;]*?\s+from\s+['\"](?P<path>\.[^'\"]+)['\"]\s*;?\s*$"),
    re.compile(r"(?m)^\s*export(?:\s+type)?\s+[^;]*?\s+from\s+['\"](?P<path>\.[^'\"]+)['\"]\s*;?\s*$"),
    re.compile(r"(?m)^\s*import\s+['\"](?P<path>\.[^'\"]+)['\"]\s*;?\s*$"),
    re.compile(r"import\(\s*['\"](?P<path>\.[^'\"]+)['\"]\s*\)"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect import cycles in frontend TypeScript modules")
    parser.add_argument(
        "--root",
        default="src/shogiarena/_core/interfaces/dashboard/frontend/src",
        help="Frontend source root",
    )
    return parser.parse_args()


def discover_modules(root: Path) -> list[Path]:
    modules: dict[Path, None] = {}
    for pattern in ("*.ts", "*.tsx"):
        for path in root.rglob(pattern):
            if path.name.endswith(".d.ts"):
                continue
            modules[_normalize_path(path.absolute())] = None
    return sorted(modules)


def _normalize_path(path: Path) -> Path:
    return Path(os.path.normpath(os.fspath(path)))


def resolve_target(module_file: Path, spec: str, module_paths: frozenset[Path]) -> Path | None:
    base = _normalize_path(module_file.parent / spec)
    base_suffix = base.suffix.lower()
    explicit_ts_suffixes = {".ts", ".tsx", ".mts", ".cts"}
    js_suffixes = {".js", ".jsx", ".mjs", ".cjs"}

    extension_candidates: list[Path] = []
    if base_suffix in explicit_ts_suffixes:
        extension_candidates.extend([base])
    elif base_suffix in js_suffixes:
        extension_candidates.extend(
            [
                base.with_suffix(".ts"),
                base.with_suffix(".tsx"),
            ]
        )
    else:
        extension_candidates.extend(
            [
                base,
                base.with_name(f"{base.name}.ts"),
                base.with_name(f"{base.name}.tsx"),
            ]
        )

    candidates = [
        *extension_candidates,
        base / "index.ts",
        base / "index.tsx",
    ]

    for candidate in candidates:
        resolved = _normalize_path(candidate)
        if resolved in module_paths:
            return resolved
    return None


def build_graph(root: Path, modules: list[Path]) -> tuple[dict[str, set[str]], int]:
    root_resolved = root.resolve()
    module_key = {module: module.relative_to(root_resolved).with_suffix("").as_posix() for module in modules}
    module_paths = frozenset(modules)
    graph: dict[str, set[str]] = defaultdict(set)

    for source in modules:
        text = source.read_text(encoding="utf-8")
        source_key = module_key[source]
        graph[source_key]
        for pattern in IMPORT_PATTERNS:
            for match in pattern.finditer(text):
                spec = match.group("path")
                if not spec.startswith("."):
                    continue
                target_path = resolve_target(source, spec, module_paths)
                if target_path is None:
                    continue
                target_key = module_key.get(target_path)
                if target_key is None:
                    continue
                if target_key != source_key:
                    graph[source_key].add(target_key)

    return graph, len(module_key)


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    next_index = 0
    cycles: list[list[str]] = []

    def strong_connect(node: str) -> None:
        nonlocal next_index
        index[node] = low[node] = next_index
        next_index += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in graph[node]:
            if neighbor not in index:
                strong_connect(neighbor)
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

    for node in graph:
        if node not in index:
            strong_connect(node)

    return cycles


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    modules = discover_modules(root)
    graph, _ = build_graph(root, modules)
    cycles = find_cycles(graph)

    if cycles:
        print(f"TypeScript import cycle count: {len(cycles)}")
        for idx, cycle in enumerate(cycles, 1):
            print(f"  - TS-CYCLE-{idx}: " + " -> ".join(cycle))
        return 1

    print("TypeScript import cycle count: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
