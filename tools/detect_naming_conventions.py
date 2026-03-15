#!/usr/bin/env python
"""Detect naming convention violations."""

from __future__ import annotations

import argparse
import ast
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT_PACKAGE = "shogiarena"

SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PASCAL_CASE_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
UPPER_SNAKE_CASE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

DUNDER_FILE_RE = re.compile(r"^__[a-z][a-z0-9_]*__$")

FORBIDDEN_MODULE_BASENAMES = {"helpers", "utils", "common", "misc", "temp"}
FORBIDDEN_PREFIX_CLASS = "Legacy"
FORBIDDEN_PREFIX_NAME = "legacy_"

LEGACY_NAME_ALLOWLIST_MODULE_PREFIXES: tuple[str, ...] = (f"{PROJECT_ROOT_PACKAGE}.testing.",)

PORT_PROTOCOL_ALLOWED_SUFFIXES: tuple[str, ...] = (
    "Port",
    "Factory",
    "Repository",
    "Policy",
    "Like",
    "ServiceLike",
    "Fn",
)


@dataclass(frozen=True)
class Violation:
    source_file: Path
    line: int
    rule_id: str
    symbol: str
    message: str
    hint: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect naming convention violations.")
    parser.add_argument("--root", default="src", help="Root directory to scan")
    parser.add_argument(
        "--fail-on-violations",
        action="store_true",
        help="Return non-zero if at least one violation is found.",
    )
    parser.add_argument(
        "--non-blocking-rule-id",
        action="append",
        default=[],
        help=(
            "Rule ID treated as non-blocking for --fail-on-violations. Repeatable (e.g. --non-blocking-rule-id N008)."
        ),
    )
    return parser.parse_args()


def _scan_root(root: Path) -> Path:
    package_root = root / PROJECT_ROOT_PACKAGE
    return package_root if package_root.is_dir() else root


def _iter_python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _build_module_name(source_file: Path, scan_root: Path) -> str:
    rel = source_file.relative_to(scan_root)
    if rel.name == "__init__.py":
        parts = rel.parent.parts
        return PROJECT_ROOT_PACKAGE if not parts else f"{PROJECT_ROOT_PACKAGE}.{'.'.join(parts)}"
    return f"{PROJECT_ROOT_PACKAGE}.{'.'.join(rel.with_suffix('').parts)}"


def _is_legacy_name_allowlisted(module_name: str) -> bool:
    return any(module_name.startswith(prefix) for prefix in LEGACY_NAME_ALLOWLIST_MODULE_PREFIXES)


def _is_protocol_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Protocol":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Protocol":
            return True
    return False


def _strip_leading_underscores(name: str) -> str:
    return name.lstrip("_")


def _add_file_violations(path: Path, root: Path, module_name: str, violations: list[Violation]) -> None:
    stem = path.stem
    rel = path.relative_to(root)
    parent_name = path.parent.name

    bare_stem = _strip_leading_underscores(stem)
    if DUNDER_FILE_RE.match(stem):
        pass  # __init__, __main__ etc. are Python standard
    elif bare_stem and not SNAKE_CASE_RE.match(bare_stem):
        violations.append(
            Violation(
                source_file=rel,
                line=1,
                rule_id="N001",
                symbol=stem,
                message="module filename must be snake_case",
                hint="rename file to snake_case",
            )
        )

    if stem in FORBIDDEN_MODULE_BASENAMES:
        violations.append(
            Violation(
                source_file=rel,
                line=1,
                rule_id="N005",
                symbol=stem,
                message="forbidden generic module basename",
                hint="rename to role-specific module name",
            )
        )

    if stem.startswith(FORBIDDEN_PREFIX_NAME) and not _is_legacy_name_allowlisted(module_name):
        violations.append(
            Violation(
                source_file=rel,
                line=1,
                rule_id="N006",
                symbol=stem,
                message="forbidden legacy prefix in module name",
                hint="use role/context based naming instead of legacy_*",
            )
        )

    if bare_stem and bare_stem.startswith(f"{parent_name}_"):
        suggested = bare_stem[len(parent_name) + 1 :]
        hint = (
            f"rename to {suggested}.py if context remains clear"
            if suggested
            else "remove parent directory prefix from module filename"
        )
        violations.append(
            Violation(
                source_file=rel,
                line=1,
                rule_id="N008",
                symbol=stem,
                message="module filename redundantly repeats parent directory prefix",
                hint=hint,
            )
        )


def _check_python_ast(path: Path, root: Path, module_name: str, violations: list[Violation]) -> None:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return

    rel = path.relative_to(root)
    in_ports = "ports" in path.parts

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            bare_class_name = _strip_leading_underscores(class_name)
            if bare_class_name and not PASCAL_CASE_RE.match(bare_class_name):
                violations.append(
                    Violation(
                        source_file=rel,
                        line=node.lineno,
                        rule_id="N002",
                        symbol=class_name,
                        message="class name must be PascalCase",
                        hint="rename class to PascalCase",
                    )
                )

            if class_name.startswith(FORBIDDEN_PREFIX_CLASS) and not _is_legacy_name_allowlisted(module_name):
                violations.append(
                    Violation(
                        source_file=rel,
                        line=node.lineno,
                        rule_id="N006",
                        symbol=class_name,
                        message="forbidden legacy prefix in class name",
                        hint="rename class without Legacy* prefix",
                    )
                )

            if in_ports and _is_protocol_class(node):
                if not class_name.endswith(PORT_PROTOCOL_ALLOWED_SUFFIXES):
                    violations.append(
                        Violation(
                            source_file=rel,
                            line=node.lineno,
                            rule_id="N007",
                            symbol=class_name,
                            message="ports Protocol should use role suffix (Port/Factory/Repository/Policy/Like)",
                            hint="rename protocol with appropriate suffix",
                        )
                    )

        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            func_name = node.name
            if _is_dunder(func_name):
                continue
            bare_func_name = _strip_leading_underscores(func_name)
            if bare_func_name and not SNAKE_CASE_RE.match(bare_func_name):
                violations.append(
                    Violation(
                        source_file=rel,
                        line=node.lineno,
                        rule_id="N003",
                        symbol=func_name,
                        message="function/method name must be snake_case",
                        hint="rename function to snake_case",
                    )
                )
            if func_name.startswith(FORBIDDEN_PREFIX_NAME) and not _is_legacy_name_allowlisted(module_name):
                violations.append(
                    Violation(
                        source_file=rel,
                        line=node.lineno,
                        rule_id="N006",
                        symbol=func_name,
                        message="forbidden legacy prefix in function name",
                        hint="rename function without legacy_* prefix",
                    )
                )

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if name.startswith("_"):
                continue
            if PASCAL_CASE_RE.match(name):
                continue
            if SNAKE_CASE_RE.match(name):
                continue
            if UPPER_SNAKE_CASE_RE.match(name):
                continue
            violations.append(
                Violation(
                    source_file=rel,
                    line=node.lineno,
                    rule_id="N004",
                    symbol=name,
                    message="module-level assignment name should be snake_case or UPPER_SNAKE_CASE",
                    hint="rename symbol to snake_case/UPPER_SNAKE_CASE",
                )
            )


def _summarize(violations: Sequence[Violation]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for violation in violations:
        summary[violation.rule_id] = summary.get(violation.rule_id, 0) + 1
    return summary


def _normalize_rule_id(rule_id: str) -> str:
    return rule_id.strip().upper()


def _resolve_non_blocking_rule_ids(raw_rule_ids: Sequence[str]) -> frozenset[str]:
    return frozenset(_normalize_rule_id(rule_id) for rule_id in raw_rule_ids if rule_id.strip())


def _count_blocking_violations(violations: Sequence[Violation], non_blocking_rule_ids: frozenset[str]) -> int:
    return sum(1 for violation in violations if violation.rule_id not in non_blocking_rule_ids)


def main() -> int:
    args = _parse_args()
    root = Path(args.root)
    scan_root = _scan_root(root)
    non_blocking_rule_ids = _resolve_non_blocking_rule_ids(args.non_blocking_rule_id)
    violations: list[Violation] = []

    for path in _iter_python_files(scan_root):
        module_name = _build_module_name(path, scan_root)
        _add_file_violations(path, root, module_name, violations)
        _check_python_ast(path, root, module_name, violations)

    if not violations:
        print("Naming conventions check: no violations.")
        return 0

    summary = _summarize(violations)
    summary_parts = ", ".join(f"{key}={value}" for key, value in sorted(summary.items()))
    print(f"Naming conventions check: {len(violations)} violation(s) ({summary_parts})")
    blocking_count = _count_blocking_violations(violations, non_blocking_rule_ids)
    non_blocking_count = len(violations) - blocking_count
    if non_blocking_count > 0 and non_blocking_rule_ids:
        print(
            f"Note: {non_blocking_count} violation(s) are non-blocking "
            f"({', '.join(sorted(non_blocking_rule_ids))}) by configuration."
        )
    print("Detected violations:")
    for violation in sorted(violations, key=lambda item: (str(item.source_file), item.line, item.rule_id)):
        print(
            f"{violation.source_file}:{violation.line}: "
            f"{violation.rule_id} | {violation.symbol} | {violation.message} | hint: {violation.hint}"
        )

    if args.fail_on_violations and blocking_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
