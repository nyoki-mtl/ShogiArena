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
    "Fn",
)

# Dashboard frontend TypeScript source files live under this path (relative to the
# scan root's parent package). Only `*.ts` source files are checked for kebab-case;
# `*.test.ts` and `*.d.ts` are structural-suffix exceptions.
TS_FRONTEND_RELATIVE = Path("shogiarena/_core/interfaces/dashboard/frontend/src")
KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TS_TEST_SUFFIX = ".test.ts"
TS_DECL_SUFFIX = ".d.ts"

# Project-owned dashboard wire keys must be snake_case (see
# agent-docs/tasks/0008-project-wide-renaming). The boolean key `summaryReady` is the
# one non-mechanical migration target (-> is_summary_ready); everything else converts
# mechanically. This map is the canonical list for the TypeScript-side N010 check and
# also documents the historical migration targets.
LEGACY_WIRE_KEY_REPLACEMENTS: dict[str, str] = {
    "tournamentType": "tournament_type",
    "summaryReady": "is_summary_ready",
    "enginesMeta": "engines_meta",
    "engineTimeControls": "engine_time_controls",
    "defaultTimeControl": "default_time_control",
    "engineInstances": "engine_instances",
    "engineStats": "engine_stats",
    "engineMeta": "engine_meta",
    "pairResults": "pair_results",
    "snapshotMeta": "snapshot_meta",
    "estimatedTimeRemaining": "estimated_time_remaining",
    "updatedAt": "updated_at",
    "inProgress": "in_progress",
    "completionRate": "completion_rate",
    "liveView": "live_view",
    "liveViewMode": "live_view_mode",
    "liveViewProgressState": "live_view_progress_state",
    "tournamentFinished": "tournament_finished",
    "tournamentConfig": "tournament_config",
    "triggerPerHour": "trigger_per_hour",
    "failureRate": "failure_rate",
    "retentionMinutes": "retention_minutes",
    "intervalSeconds": "interval_seconds",
    "flipPolicy": "flip_policy",
    "autoSnapshot": "auto_snapshot",
    "runDir": "run_dir",
    "outputDir": "output_dir",
    "runStatus": "run_status",
    "runMode": "run_mode",
    "summarySource": "summary_source",
    "spsaConfig": "spsa_config",
    "recordFormat": "record_format",
    "recordsSummary": "records_summary",
    "recordsOutput": "records_output",
    "generateConfig": "generate_config",
    "experimentName": "experiment_name",
    "filePrefix": "file_prefix",
    "fileCount": "file_count",
    "totalBytes": "total_bytes",
    "totalGames": "total_games",
    "totalPositions": "total_positions",
    "initialPositions": "initial_positions",
    "numEngines": "num_engines",
    "unitLabel": "unit_label",
    "isFinal": "is_final",
    "isResumable": "is_resumable",
    "latencyMs": "latency_ms",
    "payloadKb": "payload_kb",
    "detailPayloadKbSlim": "detail_payload_kb_slim",
    "detailPayloadKbFull": "detail_payload_kb_full",
    "detailIncludeCount": "detail_include_count",
    "includeCount": "include_count",
    "includeAnalysis": "include_analysis",
    "repetitionOccurrencesToDraw": "repetition_occurrences_to_draw",
    "gameId": "game_id",
    "gameIndex": "game_index",
    "ratingInitial": "rating_initial",
    "winRate": "win_rate",
    "winRateCi95": "win_rate_ci95",
    "eloEstimate": "elo_estimate",
    "eloCi95": "elo_ci95",
    "minGames": "min_games",
    "maxGames": "max_games",
    "pendingPairs": "pending_pairs",
    "pendingGames": "pending_games",
    "skippedNonDecisive": "skipped_non_decisive",
    "receivedAt": "received_at",
    "fromSeq": "from_seq",
}
LEGACY_WIRE_KEY_RE = re.compile(r"\b(" + "|".join(LEGACY_WIRE_KEY_REPLACEMENTS) + r")\b")

# Generic camelCase string-literal detector for Python. Python has no idiomatic
# camelCase, so any camelCase quoted string inside a wire-producing/parsing path is
# treated as a stray wire key. This is broader and more future-proof than the explicit
# list above (which is still used for the TypeScript side, where camelCase identifiers
# are legitimate outside the wire layer).
PY_CAMEL_STRING_RE = re.compile(r"""['"]([a-z][a-z0-9]*[A-Z][A-Za-z0-9]*)['"]""")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def camel_to_snake(name: str) -> str:
    return _CAMEL_BOUNDARY_RE.sub("_", name).lower()


# Python paths whose project-owned string keys must be snake_case. OpenBench endpoint
# names are an external protocol contract and are intentionally excluded.
PY_WIRE_PATH_FRAGMENTS: tuple[str, ...] = (
    "interfaces/dashboard/",
    "interfaces/boundaries/",
    "contexts/dashboard/",
    "contexts/spsa/",
    "contexts/game_session/application/summary/",
    "contexts/game_session/application/completion/",
    "platform/records/",
)
PY_WIRE_EXCLUDE_FRAGMENTS: tuple[str, ...] = ("adapters/openbench/", "/openbench/")

# TypeScript wire-layer files: only PURE-wire locations are scanned. `contracts/` holds
# generated wire types + zod parsers; `services/schemas.ts` and
# `services/updates/schema.ts` are zod schema definitions. These contain wire keys only.
#
# Files like `types/public.ts` / `types.ts` / `store/types.ts` are intentionally NOT
# scanned: they mix raw wire payload types with API interfaces (camelCase method params)
# and ViewModel types (idiomatic camelCase), so a token scan there yields false
# positives. The authoritative gate for those keys is the Python-side generic check on
# the producers (source of truth), which has no idiomatic camelCase to confuse it.
TS_WIRE_PATH_FRAGMENTS: tuple[str, ...] = ("dashboard/frontend/src/contracts/",)
TS_WIRE_FILE_SUFFIXES: tuple[str, ...] = (
    "/services/schemas.ts",
    "/services/updates/schema.ts",
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
                            message="ports Protocol should use role suffix (Port/Factory/Repository/Policy/Fn)",
                            hint="rename protocol with one of Port/Factory/Repository/Policy/Fn",
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


def _iter_ts_files(frontend_root: Path) -> Iterable[Path]:
    for path in frontend_root.rglob("*.ts"):
        if "node_modules" in path.parts:
            continue
        yield path


def _ts_subject_stem(path: Path) -> str | None:
    """Return the kebab-checkable stem, or None for structural-suffix exceptions."""
    name = path.name
    if name.endswith(TS_TEST_SUFFIX) or name.endswith(TS_DECL_SUFFIX):
        return None
    return path.stem


def _check_ts_file_names(frontend_root: Path, root: Path, violations: list[Violation]) -> None:
    for path in _iter_ts_files(frontend_root):
        stem = _ts_subject_stem(path)
        if stem is None:
            continue
        if not KEBAB_CASE_RE.match(stem):
            violations.append(
                Violation(
                    source_file=path.relative_to(root),
                    line=1,
                    rule_id="N009",
                    symbol=path.name,
                    message="TypeScript source file name must be kebab-case",
                    hint="rename file to kebab-case.ts (exceptions: *.test.ts, *.d.ts)",
                )
            )


def _is_py_wire_path(path: Path) -> bool:
    posix = path.as_posix()
    if any(fragment in posix for fragment in PY_WIRE_EXCLUDE_FRAGMENTS):
        return False
    return any(fragment in posix for fragment in PY_WIRE_PATH_FRAGMENTS)


def _is_ts_wire_path(path: Path) -> bool:
    posix = path.as_posix()
    if any(fragment in posix for fragment in TS_WIRE_PATH_FRAGMENTS):
        return True
    return any(posix.endswith(suffix) for suffix in TS_WIRE_FILE_SUFFIXES)


def _check_py_wire_keys(path: Path, root: Path, violations: list[Violation]) -> None:
    """Flag any camelCase quoted string in Python wire-producing paths (N010)."""
    if not _is_py_wire_path(path):
        return
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for lineno, line in enumerate(source.splitlines(), start=1):
        for match in PY_CAMEL_STRING_RE.finditer(line):
            legacy = match.group(1)
            replacement = LEGACY_WIRE_KEY_REPLACEMENTS.get(legacy, camel_to_snake(legacy))
            violations.append(
                Violation(
                    source_file=path.relative_to(root),
                    line=lineno,
                    rule_id="N010",
                    symbol=legacy,
                    message="project-owned wire key uses camelCase string literal",
                    hint=f"rename wire key to {replacement}",
                )
            )


def _check_ts_wire_keys(path: Path, root: Path, violations: list[Violation]) -> None:
    """Flag known legacy camelCase wire keys in TypeScript wire-layer files (N010)."""
    if not _is_ts_wire_path(path):
        return
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for lineno, line in enumerate(source.splitlines(), start=1):
        for match in LEGACY_WIRE_KEY_RE.finditer(line):
            legacy = match.group(1)
            violations.append(
                Violation(
                    source_file=path.relative_to(root),
                    line=lineno,
                    rule_id="N010",
                    symbol=legacy,
                    message="project-owned dashboard wire key uses legacy camelCase",
                    hint=f"rename wire key to {LEGACY_WIRE_KEY_REPLACEMENTS[legacy]}",
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
        _check_py_wire_keys(path, root, violations)

    frontend_root = root / TS_FRONTEND_RELATIVE
    if frontend_root.is_dir():
        _check_ts_file_names(frontend_root, root, violations)
        for ts_path in _iter_ts_files(frontend_root):
            _check_ts_wire_keys(ts_path, root, violations)

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
