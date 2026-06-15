#!/usr/bin/env python
"""Collect lightweight type-safety drift signals from Python/TypeScript sources."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    ".ruff_cache",
    ".ty",
    ".pytest_cache",
    ".mypy_cache",
    ".sarif",
    ".sandbox",
    ".serena",
    ".claude",
    "_refs",
    "node_modules",
    "dist",
    "build",
    ".next",
}


PATTERNS: dict[str, dict[str, tuple[str, re.Pattern[str]]]] = {
    "python": {
        "python.cast": (
            "cast()",
            re.compile(r"\bcast\s*\(", re.IGNORECASE),
        ),
        "python.JsonValue": (
            "JsonValue",
            re.compile(r"\bJsonValue\b"),
        ),
        "python.mapping_jsonvalue": (
            "Mapping[str, JsonValue] / dict[str, JsonValue]",
            re.compile(
                r"\b(?:Mapping|dict)\s*\[\s*str\s*,\s*JsonValue\s*\]",
                re.IGNORECASE,
            ),
        ),
    },
    "typescript": {
        "typescript.as_unknown_as": (
            "as unknown as",
            re.compile(r"\bas\s+unknown\s+as\b"),
        ),
        "typescript.z_any": (
            "z.any()",
            re.compile(r"\bz\.any\s*\("),
        ),
        "typescript.record_unknown": (
            "Record<string, unknown>",
            re.compile(r"\bRecord\s*<\s*string\s*,\s*unknown\s*>"),
        ),
    },
}


EXTENSIONS = {
    "python": {".py"},
    "typescript": {".ts", ".tsx"},
}


@dataclass(frozen=True)
class Match:
    file: str
    line: int
    text: str


def _iter_files(roots: Iterable[Path], extensions: set[str]) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in extensions:
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            yield path


def _collect(root_paths: list[Path]) -> tuple[Counter[str], dict[str, list[Match]]]:
    totals = Counter()
    matches: dict[str, list[Match]] = defaultdict(list)
    repo_root = Path.cwd()
    for language, pattern_map in PATTERNS.items():
        for path in _iter_files(root_paths, EXTENSIONS[language]):
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                for pattern_key, (_unused, pattern) in pattern_map.items():
                    for _hit in pattern.finditer(line):
                        totals[pattern_key] += 1
                        matches[pattern_key].append(
                            Match(
                                file=str(path.relative_to(repo_root)),
                                line=line_no,
                                text=line.strip(),
                            )
                        )
    return totals, matches


def _build_markdown(
    totals: Counter[str],
    matches: dict[str, list[Match]],
    baseline: dict[str, int] | None,
    timestamp: str,
    roots: Iterable[Path],
) -> str:
    lines: list[str] = []
    lines.append("# Type Safety Audit")
    lines.append("")
    lines.append(f"- Generated: {timestamp}")
    lines.append("- Roots:")
    for root in roots:
        lines.append(f"  - `{root}`")
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append("| Pattern | Hits | Baseline | Delta |")
    lines.append("| --- | ---: | ---: | ---: |")
    for pattern_key in sorted(totals):
        count = totals[pattern_key]
        base = baseline[pattern_key] if baseline and pattern_key in baseline else 0
        delta = count - base if baseline else 0
        lines.append(f"| `{pattern_key}` | {count} | {base} | {delta:+d} |")
    lines.append("")

    for pattern_key in sorted(matches):
        hits = matches[pattern_key]
        lines.append(f"## {pattern_key}")
        lines.append("")
        if not hits:
            lines.append("No matches.")
            lines.append("")
            continue
        lines.append("| File | Line | Snippet |")
        lines.append("| --- | ---: | --- |")
        for hit in hits:
            text = hit.text.replace("|", "\\|")
            lines.append(f"| `{hit.file}` | {hit.line} | {text} |")
        lines.append("")
    return "\n".join(lines)


def _build_summary(totals: Counter[str], baseline: dict[str, int] | None) -> str:
    lines = ["Type Safety Audit totals:"]
    for pattern_key in sorted(totals):
        count = totals[pattern_key]
        base = baseline[pattern_key] if baseline and pattern_key in baseline else 0
        delta = count - base if baseline else 0
        lines.append(f"- {pattern_key}: {count} (baseline {base}, delta {delta:+d})")
    return "\n".join(lines)


def _load_baseline(path: Path | None) -> dict[str, int] | None:
    if path is None or not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("totals")
    if isinstance(data, dict):
        return {key: int(value) for key, value in data.items()}
    return None


def _write_baseline(path: Path, totals: Counter[str]) -> None:
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "totals": dict(totals),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect type-safety erosion patterns in Python/TypeScript boundary code."
    )
    parser.add_argument(
        "--root",
        action="append",
        default=None,
        help="Root directories to scan (repeatable).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write markdown report path.",
    )
    parser.add_argument(
        "--baseline-json",
        type=Path,
        help="Baseline JSON file to compare against.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite baseline JSON with current totals.",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit non-zero when any pattern exceeds baseline.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print only totals instead of the full markdown report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    roots = args.root if args.root is not None else [str(Path("src"))]
    root_paths = [Path(root).resolve() for root in roots]
    timestamp = datetime.now(UTC).isoformat()
    baseline_totals = _load_baseline(args.baseline_json)
    totals, matches = _collect(root_paths)

    report = _build_markdown(
        totals=totals,
        matches=matches,
        baseline=baseline_totals,
        timestamp=timestamp,
        roots=root_paths,
    )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    elif args.summary:
        print(_build_summary(totals, baseline_totals))
    else:
        print(report)

    if args.update_baseline:
        baseline_path = args.baseline_json or Path("agent-docs/rules/typing-audit-baseline.json")
        _write_baseline(baseline_path, totals)

    if not args.fail_on_regression or baseline_totals is None:
        return 0

    regressions = [key for key, count in totals.items() if count > baseline_totals.get(key, 0)]
    if not regressions:
        return 0

    print("Type-safety regression detected for patterns:")
    for key in regressions:
        print(f"- {key}: {totals[key]} (baseline {baseline_totals.get(key, 0)})")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
