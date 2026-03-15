#!/usr/bin/env python
"""Detect dynamic import escapes that bypass architecture dependency checks."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from textwrap import shorten

PROJECT_ROOT_PACKAGE = "shogiarena"
INTERNAL_ROOT = "_core"
CONTEXTS_LAYER = "contexts"
RUNTIME_LAYER = "runtime"
INTERFACES_LAYER = "interfaces"

# Transitional allowlist: contexts.adapters modules that may still dynamically
# import from interfaces.  Each entry is a source module prefix.  Retire entries
# as their dependencies are migrated to ports/application.
DYNAMIC_CONTEXTS_INTERFACES_ALLOWLIST: tuple[str, ...] = ()


@dataclass(frozen=True)
class Violation:
    source_file: Path
    source_line: int
    source_module: str
    source_layer: str
    target_module: str
    expression: str


def _source_layer(path: Path, root: Path) -> str:
    """Return source logical layer (contexts/runtime) or ``unknown``."""

    try:
        parts = _normalize_module_parts(path, root)
    except ValueError:
        return "unknown"

    if not parts:
        return "unknown"

    if parts[0] == CONTEXTS_LAYER:
        return CONTEXTS_LAYER
    if parts[0] == RUNTIME_LAYER:
        return RUNTIME_LAYER
    return "other"


def _normalize_module_parts(source_file: Path, root: Path) -> tuple[str, ...]:
    """Return module-relative path parts under shogiarena package."""

    rel_parts = source_file.relative_to(root).parts
    if rel_parts and rel_parts[0] == PROJECT_ROOT_PACKAGE:
        rel_parts = rel_parts[1:]
    if rel_parts and rel_parts[0] == INTERNAL_ROOT:
        rel_parts = rel_parts[1:]
    return rel_parts


def _is_contexts_target(module: str) -> bool:
    return module.startswith(f"{PROJECT_ROOT_PACKAGE}.contexts") or module.startswith(
        f"{PROJECT_ROOT_PACKAGE}.{INTERNAL_ROOT}.contexts"
    )


def _is_interfaces_target(module: str) -> bool:
    return module.startswith(f"{PROJECT_ROOT_PACKAGE}.interfaces") or module.startswith(
        f"{PROJECT_ROOT_PACKAGE}.{INTERNAL_ROOT}.interfaces"
    )


def _is_runtime_target(module: str) -> bool:
    return module.startswith(f"{PROJECT_ROOT_PACKAGE}.{RUNTIME_LAYER}") or module.startswith(
        f"{PROJECT_ROOT_PACKAGE}.{INTERNAL_ROOT}.{RUNTIME_LAYER}"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect context/runtime dynamic import cheat usage.")
    parser.add_argument("--root", default="src", help="Root directory to scan")
    parser.add_argument(
        "--fail-on-violations",
        action="store_true",
        help="Return non-zero if at least one violation is found.",
    )
    return parser.parse_args()


def _iter_python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _is_importlib_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        return node.func.value.id == "importlib" and node.func.attr == "import_module"
    if isinstance(node.func, ast.Name) and node.func.id == "import_module":
        return True
    return False


def _is_dunder_import_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Name) and node.func.id == "__import__"


def _extract_dynamic_target(node: ast.Call) -> str | None:
    if not node.args:
        return None
    arg0 = node.args[0]
    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
        return arg0.value
    return None


def _collect_loader_functions(tree: ast.AST) -> set[str]:
    """Collect helper names that delegate to importlib/__import__."""

    loader_functions: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            continue
        call = node.body[0].value
        if not isinstance(call, ast.Call):
            continue
        if _is_importlib_call(call) or _is_dunder_import_call(call):
            loader_functions.add(node.name)
            continue

        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
            if call.func.value.id == "importlib" and call.func.attr == "import_module":
                loader_functions.add(node.name)
    return loader_functions


def _is_loader_call(node: ast.Call, loader_functions: set[str]) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in loader_functions
    if isinstance(node.func, ast.Attribute):
        return node.func.attr in loader_functions
    return False


def _is_dynamic_allowlisted(source_module: str) -> bool:
    """Check if *source_module* is in the transitional dynamic allowlist."""
    return any(source_module.startswith(prefix) for prefix in DYNAMIC_CONTEXTS_INTERFACES_ALLOWLIST)


def _violation_reason(source_layer: str, target_module: str) -> str:
    if source_layer == CONTEXTS_LAYER and _is_runtime_target(target_module):
        return "contexts -> runtime"
    if source_layer == CONTEXTS_LAYER and _is_interfaces_target(target_module):
        return "contexts -> interfaces"
    if source_layer == RUNTIME_LAYER and (_is_contexts_target(target_module) or _is_interfaces_target(target_module)):
        return "runtime -> contexts/interfaces"
    return ""


def _build_module_name(source_file: Path, root: Path) -> str:
    try:
        rel = source_file.relative_to(root)
    except ValueError:
        return ""
    if rel.suffix != ".py":
        return ""
    parts = _normalize_module_parts(source_file, root)
    if not parts:
        return ""
    if rel.name == "__init__.py":
        parts = parts[:-1]
        if not parts:
            return PROJECT_ROOT_PACKAGE
        return f"{PROJECT_ROOT_PACKAGE}.{'.'.join(parts)}" if parts else PROJECT_ROOT_PACKAGE
    return f"{PROJECT_ROOT_PACKAGE}.{'.'.join(parts)}" if parts else PROJECT_ROOT_PACKAGE


def scan_file(path: Path, root: Path) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    source_layer = _source_layer(path, root)
    if source_layer not in {CONTEXTS_LAYER, RUNTIME_LAYER}:
        return []

    loader_functions = _collect_loader_functions(tree)
    source_module = _build_module_name(path, root)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        target_module: str | None = None
        expression = ""
        if _is_importlib_call(node) or _is_dunder_import_call(node):
            target_module = _extract_dynamic_target(node)
            expression = ast.unparse(node.func)
        elif _is_loader_call(node, loader_functions):
            target_module = _extract_dynamic_target(node)
            expression = ast.unparse(node.func)

        if not target_module:
            continue

        reason = _violation_reason(source_layer, target_module)
        if not reason:
            continue
        if not target_module.startswith(f"{PROJECT_ROOT_PACKAGE}."):
            continue
        if reason == "contexts -> interfaces" and _is_dynamic_allowlisted(source_module):
            continue
        violations.append(
            Violation(
                source_file=path.relative_to(root),
                source_line=node.lineno,
                source_module=source_module,
                source_layer=source_layer,
                target_module=target_module,
                expression=expression,
            )
        )

    return violations


def _summarize_by_type(violations: Sequence[Violation]) -> Mapping[str, int]:
    summary: dict[str, int] = {
        "contexts_runtime": 0,
        "contexts_interfaces": 0,
        "runtime_contexts_interfaces": 0,
    }
    for violation in violations:
        if violation.source_layer == CONTEXTS_LAYER and _is_runtime_target(violation.target_module):
            summary["contexts_runtime"] += 1
        elif violation.source_layer == CONTEXTS_LAYER and _is_interfaces_target(violation.target_module):
            summary["contexts_interfaces"] += 1
        elif violation.source_layer == RUNTIME_LAYER and (
            _is_contexts_target(violation.target_module) or _is_interfaces_target(violation.target_module)
        ):
            summary["runtime_contexts_interfaces"] += 1
    return summary


def main() -> int:
    args = _parse_args()
    root = Path(args.root)
    violations: list[Violation] = []

    for path in _iter_python_files(root):
        violations.extend(scan_file(path, root))

    if not violations:
        print("Dynamic dependency cheat detection: no violations.")
        return 0

    summary = _summarize_by_type(violations)
    print("Dynamic dependency cheat detection:")
    print(
        "KPI: contexts -> runtime: "
        f"{summary['contexts_runtime']} / contexts -> interfaces: "
        f"{summary['contexts_interfaces']} / runtime -> contexts/interfaces: "
        f"{summary['runtime_contexts_interfaces']}"
    )
    print("Detected violations:")
    for violation in sorted(violations, key=lambda item: (str(item.source_file), item.source_line)):
        reason = _violation_reason(violation.source_layer, violation.target_module)
        line = (
            f"{violation.source_file}:{violation.source_line}: "
            f"{reason} | {violation.source_module} -> {violation.target_module} "
            f"via {shorten(violation.expression, width=80, placeholder='...')}"
        )
        print(line)

    if args.fail_on_violations:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
