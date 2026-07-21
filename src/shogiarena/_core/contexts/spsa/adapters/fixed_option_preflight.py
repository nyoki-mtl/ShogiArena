"""SPSA 実行前の YaneuraOu fixed option 検査。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.yaneuraou_option_files import (
    YaneuraOuOptionEntry,
    parse_yaneuraou_option_lines,
)

FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION = "shogiarena.spsa.fixed_option_preflight.v1"
FIXED_OPTION_PREFLIGHT_FILENAME = "fixed_option_preflight.json"

FixedOptionFileStatus: TypeAlias = Literal["missing", "checked", "conflict"]
FixedOptionEngineStatus: TypeAlias = Literal["checked", "skipped", "conflict"]


class _EngineConfigPort(Protocol):
    name: str | None
    engine_path: Path | None
    artifact: str | None


@dataclass(frozen=True, slots=True)
class FixedOptionFileScan:
    """YaneuraOu option file の検査結果。"""

    path: Path
    status: FixedOptionFileStatus
    option_names: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    def to_json(self) -> JsonObject:
        """JSON artifact 用の payload に変換する。"""

        return {
            "path": str(self.path),
            "status": self.status,
            "option_names": list(self.option_names),
            "conflicts": list(self.conflicts),
        }


@dataclass(frozen=True, slots=True)
class FixedOptionEngineScan:
    """SPSA 対象エンジン単位の fixed option 検査結果。"""

    engine_name: str
    status: FixedOptionEngineStatus
    reason: str | None
    engine_config_path: Path | None
    engine_path: str | None
    engine_directory: Path | None
    option_files: tuple[FixedOptionFileScan, ...] = ()

    @property
    def conflicts(self) -> tuple[FixedOptionFileScan, ...]:
        """競合が見つかった option file の検査結果を返す。"""

        return tuple(scan for scan in self.option_files if scan.conflicts)

    def to_json(self) -> JsonObject:
        """JSON artifact 用の payload に変換する。"""

        payload: JsonObject = {
            "engine_name": self.engine_name,
            "status": self.status,
            "reason": self.reason,
            "engine_config_path": str(self.engine_config_path) if self.engine_config_path is not None else None,
            "engine_path": self.engine_path,
            "engine_directory": str(self.engine_directory) if self.engine_directory is not None else None,
            "option_files": [scan.to_json() for scan in self.option_files],
        }
        return payload


@dataclass(frozen=True, slots=True)
class FixedOptionPreflightReport:
    """SPSA fixed option preflight 全体の検査結果。"""

    target_option_names: tuple[str, ...]
    engines: tuple[FixedOptionEngineScan, ...]

    @property
    def has_conflicts(self) -> bool:
        """いずれかの対象エンジンで fixed option 競合があれば ``True`` を返す。"""

        return any(engine.conflicts for engine in self.engines)

    def to_json(self) -> JsonObject:
        """JSON artifact 用の payload に変換する。"""

        return {
            "schema_version": FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION,
            "target_option_names": list(self.target_option_names),
            "engines": [engine.to_json() for engine in self.engines],
        }


def run_yaneuraou_fixed_option_preflight(
    *,
    engines: Sequence[_EngineConfigPort],
    target_option_names: Iterable[str],
    run_dir: Path,
    output_dir: Path,
    engine_dir: Path,
) -> FixedOptionPreflightReport:
    """SPSA 対象 option が YaneuraOu fixed option に含まれないことを検査する。"""

    normalized_targets = _normalize_target_option_names(target_option_names)
    engine_scans = tuple(
        _scan_engine(engine, target_option_names=normalized_targets, output_dir=output_dir, engine_dir=engine_dir)
        for engine in engines
    )
    report = FixedOptionPreflightReport(target_option_names=normalized_targets, engines=engine_scans)
    write_json_atomic(run_dir / "spsa" / FIXED_OPTION_PREFLIGHT_FILENAME, report.to_json())
    if report.has_conflicts:
        raise ValueError(_format_conflict_message(report))
    return report


def _normalize_target_option_names(target_option_names: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for option_name in target_option_names:
        stripped = str(option_name).strip()
        if not stripped:
            continue
        folded = stripped.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        normalized.append(stripped)
    return tuple(normalized)


def _scan_engine(
    engine: _EngineConfigPort,
    *,
    target_option_names: tuple[str, ...],
    output_dir: Path,
    engine_dir: Path,
) -> FixedOptionEngineScan:
    engine_name = str(engine.name or engine.artifact or "engine")
    engine_config_path = engine.engine_path
    if engine_config_path is None:
        return _skipped_engine_scan(engine_name, "engine_config_path_unavailable", None, None)
    if _looks_remote_path(str(engine_config_path)):
        return _skipped_engine_scan(engine_name, "remote_engine_config_path", engine_config_path, None)
    if not engine_config_path.exists():
        return _skipped_engine_scan(engine_name, "engine_config_path_missing", engine_config_path, None)

    usi_config = UsiEngineConfig.from_file(
        engine_config_path,
        output_dir=output_dir,
        engine_dir=engine_dir,
    )
    resolved_engine_path = usi_config.engine_path
    if resolved_engine_path is None or not resolved_engine_path.strip():
        return _skipped_engine_scan(engine_name, "engine_path_unavailable", engine_config_path, None)
    if _looks_remote_path(resolved_engine_path):
        return _skipped_engine_scan(engine_name, "remote_engine_path", engine_config_path, resolved_engine_path)

    engine_directory = Path(resolved_engine_path).expanduser().resolve().parent
    option_files = _scan_engine_directory(engine_directory, target_option_names=target_option_names)
    status: FixedOptionEngineStatus = "conflict" if any(scan.conflicts for scan in option_files) else "checked"
    return FixedOptionEngineScan(
        engine_name=engine_name,
        status=status,
        reason=None,
        engine_config_path=engine_config_path,
        engine_path=resolved_engine_path,
        engine_directory=engine_directory,
        option_files=option_files,
    )


def _skipped_engine_scan(
    engine_name: str,
    reason: str,
    engine_config_path: Path | None,
    engine_path: str | None,
) -> FixedOptionEngineScan:
    return FixedOptionEngineScan(
        engine_name=engine_name,
        status="skipped",
        reason=reason,
        engine_config_path=engine_config_path,
        engine_path=engine_path,
        engine_directory=None,
        option_files=(),
    )


def _scan_engine_directory(
    engine_directory: Path,
    *,
    target_option_names: tuple[str, ...],
) -> tuple[FixedOptionFileScan, ...]:
    engine_options_path = engine_directory / "engine_options.txt"
    engine_scan, engine_entries = _scan_option_file(engine_options_path, target_option_names=target_option_names)
    eval_dir = _find_option_value(engine_entries, "EvalDir")
    if eval_dir is not None and eval_dir.strip():
        eval_path = Path(eval_dir).expanduser()
        if not eval_path.is_absolute():
            eval_path = engine_directory / eval_path
        eval_options_path = eval_path / "eval_options.txt"
    else:
        eval_options_path = engine_directory / "eval" / "eval_options.txt"
    eval_scan, _eval_entries = _scan_option_file(eval_options_path, target_option_names=target_option_names)
    return (engine_scan, eval_scan)


def _scan_option_file(
    path: Path,
    *,
    target_option_names: tuple[str, ...],
) -> tuple[FixedOptionFileScan, tuple[YaneuraOuOptionEntry, ...]]:
    if not path.exists():
        return FixedOptionFileScan(path=path, status="missing"), ()
    entries = parse_yaneuraou_option_lines(path.read_text(encoding="utf-8").splitlines())
    option_names = tuple(entry.name for entry in entries)
    conflicts = _find_conflicting_option_names(target_option_names, option_names)
    status: FixedOptionFileStatus = "conflict" if conflicts else "checked"
    return (
        FixedOptionFileScan(
            path=path,
            status=status,
            option_names=option_names,
            conflicts=conflicts,
        ),
        entries,
    )


def _find_option_value(entries: Sequence[YaneuraOuOptionEntry], option_name: str) -> str | None:
    folded = option_name.casefold()
    for entry in entries:
        if entry.name.casefold() == folded:
            return entry.value
    return None


def _find_conflicting_option_names(
    target_option_names: tuple[str, ...],
    fixed_option_names: tuple[str, ...],
) -> tuple[str, ...]:
    targets_by_folded = {name.casefold(): name for name in target_option_names}
    fixed_folded = {name.casefold() for name in fixed_option_names}
    return tuple(targets_by_folded[key] for key in sorted(targets_by_folded.keys() & fixed_folded))


def _looks_remote_path(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.startswith(("ssh://", "ssh:"))


def _format_conflict_message(report: FixedOptionPreflightReport) -> str:
    lines = [
        "SPSA target option(s) are fixed by YaneuraOu option files and cannot be changed by setoption.",
        "Remove these entries from engine_options.txt/eval_options.txt or exclude them from the SPSA space:",
    ]
    for engine in report.engines:
        for scan in engine.conflicts:
            lines.append(f"  {engine.engine_name}: {scan.path}: {', '.join(scan.conflicts)}")
    return "\n".join(lines)


__all__ = [
    "FIXED_OPTION_PREFLIGHT_FILENAME",
    "FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION",
    "FixedOptionEngineScan",
    "FixedOptionFileScan",
    "FixedOptionPreflightReport",
    "run_yaneuraou_fixed_option_preflight",
]
