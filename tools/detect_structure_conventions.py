#!/usr/bin/env python
"""Detect structure convention violations."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT_PACKAGE = "shogiarena"
INTERNAL_ROOT = "_core"

REQUIRED_TOP_LEVEL_DIRS = {INTERNAL_ROOT}
FORBIDDEN_TOP_LEVEL_DIRS = {"arena", "db", "records", "utils", "typing", "runtime"}
ALLOWED_TOP_LEVEL_DIRS = {
    INTERNAL_ROOT,
    "testing",
    "__pycache__",
}

CONTEXT_REQUIRED_SUBDIRS = {"domain", "application", "ports", "adapters"}
CONTEXT_LAYOUT_EXCEPTIONS: dict[str, set[str]] = {
    "dashboard": {"application"},
    "instances": {"application", "ports", "adapters"},
    "match": {"domain", "application", "ports"},
    "spsa": {"application", "ports", "adapters"},
}

# No _bootstrap.py modules are allowed; wiring lives in _core/interfaces/composition_root/*.py
ALLOWED_BOOTSTRAP_MODULES: set[str] = set()


@dataclass(frozen=True)
class Violation:
    source_file: Path
    line: int
    rule_id: str
    message: str
    hint: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect structure convention violations.")
    parser.add_argument("--root", default="src", help="Root directory to scan")
    parser.add_argument(
        "--fail-on-violations",
        action="store_true",
        help="Return non-zero if at least one violation is found.",
    )
    return parser.parse_args()


def _scan_root(root: Path) -> Path:
    package_root = root / PROJECT_ROOT_PACKAGE
    return package_root if package_root.is_dir() else root


def _iter_bootstrap_files(scan_root: Path) -> Iterable[Path]:
    for path in scan_root.rglob("*_bootstrap.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _module_name(path: Path, scan_root: Path) -> str:
    rel = path.relative_to(scan_root)
    return f"{PROJECT_ROOT_PACKAGE}.{'.'.join(rel.with_suffix('').parts)}"


def _summarize(violations: Sequence[Violation]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for violation in violations:
        summary[violation.rule_id] = summary.get(violation.rule_id, 0) + 1
    return summary


def _check_top_level_dirs(scan_root: Path, violations: list[Violation]) -> None:
    top_dirs = {path.name for path in scan_root.iterdir() if path.is_dir()}

    for name in sorted(REQUIRED_TOP_LEVEL_DIRS - top_dirs):
        violations.append(
            Violation(
                source_file=Path(PROJECT_ROOT_PACKAGE),
                line=1,
                rule_id="S011",
                message=f"required top-level directory missing: {name}",
                hint=f"create {PROJECT_ROOT_PACKAGE}/{name} or adjust structure policy with approval",
            )
        )

    for name in sorted(FORBIDDEN_TOP_LEVEL_DIRS & top_dirs):
        violations.append(
            Violation(
                source_file=Path(PROJECT_ROOT_PACKAGE) / name,
                line=1,
                rule_id="S010",
                message=f"forbidden legacy top-level directory exists: {name}",
                hint=(
                    "migrate implementation modules into "
                    "_core/{contexts,platform,interfaces,shared} and remove legacy root"
                ),
            )
        )

    for name in sorted(top_dirs - ALLOWED_TOP_LEVEL_DIRS):
        violations.append(
            Violation(
                source_file=Path(PROJECT_ROOT_PACKAGE) / name,
                line=1,
                rule_id="S013",
                message=f"unauthorized top-level directory: {name}",
                hint="new top-level package requires explicit task approval",
            )
        )


def _check_context_layout(scan_root: Path, violations: list[Violation]) -> None:
    contexts_root = scan_root / INTERNAL_ROOT / "contexts"
    if not contexts_root.is_dir():
        return

    for context_dir in sorted(path for path in contexts_root.iterdir() if path.is_dir()):
        if context_dir.name == "__pycache__":
            continue

        subdirs = {path.name for path in context_dir.iterdir() if path.is_dir()}
        required = CONTEXT_LAYOUT_EXCEPTIONS.get(context_dir.name, CONTEXT_REQUIRED_SUBDIRS)
        missing = sorted(required - subdirs)
        if missing:
            violations.append(
                Violation(
                    source_file=context_dir.relative_to(scan_root.parent),
                    line=1,
                    rule_id="S001",
                    message=f"context layout missing required dirs: {', '.join(missing)}",
                    hint="ensure context has required layers (domain/application/ports/adapters)",
                )
            )

        if context_dir.name.startswith("legacy_"):
            violations.append(
                Violation(
                    source_file=context_dir.relative_to(scan_root.parent),
                    line=1,
                    rule_id="S012",
                    message="context directory uses forbidden legacy_ prefix",
                    hint="rename context to domain-aligned name",
                )
            )


def _check_bootstrap_locations(scan_root: Path, violations: list[Violation]) -> None:
    for path in _iter_bootstrap_files(scan_root):
        module = _module_name(path, scan_root)
        if module in ALLOWED_BOOTSTRAP_MODULES:
            continue
        violations.append(
            Violation(
                source_file=path.relative_to(scan_root.parent),
                line=1,
                rule_id="S005",
                message=f"unauthorized bootstrap module location: {module}",
                hint="register wiring only in approved composition-root bootstrap modules",
            )
        )


def main() -> int:
    args = _parse_args()
    root = Path(args.root)
    scan_root = _scan_root(root)
    violations: list[Violation] = []

    _check_top_level_dirs(scan_root, violations)
    _check_context_layout(scan_root, violations)
    _check_bootstrap_locations(scan_root, violations)

    if not violations:
        print("Structure conventions check: no violations.")
        return 0

    summary = _summarize(violations)
    summary_parts = ", ".join(f"{key}={value}" for key, value in sorted(summary.items()))
    print(f"Structure conventions check: {len(violations)} violation(s) ({summary_parts})")
    print("Detected violations:")
    for violation in sorted(violations, key=lambda item: (str(item.source_file), item.line, item.rule_id)):
        print(
            f"{violation.source_file}:{violation.line}: "
            f"{violation.rule_id} | {violation.message} | hint: {violation.hint}"
        )

    if args.fail_on_violations:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
