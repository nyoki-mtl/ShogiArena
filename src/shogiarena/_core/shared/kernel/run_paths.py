"""Helpers for generating and locating run directories."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

_RUNS_DIRNAME = "runs"


def _validate_run_name(name: str) -> str:
    safe = name.strip()
    if not safe:
        raise ValueError("run name must not be empty")
    if Path(safe).name != safe or "/" in safe or "\\" in safe:
        raise ValueError("run name must not include path separators")
    return safe


def _hash8(digest: str) -> str:
    normalized = digest.strip()
    if not normalized:
        raise ValueError("run group hash must not be empty")
    return normalized[:8]


def run_group_dir_for_key(output_dir: Path, name: str, schedule_hash: str) -> Path:
    """Return the run group directory for a logical schedule hash."""

    safe = _validate_run_name(name)
    return output_dir / _RUNS_DIRNAME / f"{safe}-{_hash8(schedule_hash)}"


def run_group_dir(config_path: Path, output_dir: Path) -> Path:
    """Return a fallback run group for callers that only know a config path."""

    return run_group_dir_for_key(output_dir, config_path.stem, config_path.stem)


def run_group_dir_for_name(config_path: Path, output_dir: Path, name: str) -> Path:
    """Return a fallback named run group for callers that only know a config path."""

    return run_group_dir_for_key(output_dir, name, config_path.stem)


def timestamp_slug(value: datetime | None = None) -> str:
    """Return a YYYYMMDDHHmmSS timestamp slug in local time."""

    return (value or datetime.now()).strftime("%Y%m%d%H%M%S")


def default_run_dir(config_path: Path, output_dir: Path, *, now: datetime | None = None) -> Path:
    """Return the default run directory for a config file."""

    return run_group_dir(config_path, output_dir) / timestamp_slug(now)


def run_dir_for_name(config_path: Path, output_dir: Path, name: str, *, now: datetime | None = None) -> Path:
    """Return the run directory for a custom name."""

    return run_group_dir_for_name(config_path, output_dir, name) / timestamp_slug(now)


def run_dir_for_key(output_dir: Path, name: str, schedule_hash: str, *, now: datetime | None = None) -> Path:
    """Return a run directory grouped by logical schedule hash."""

    return run_group_dir_for_key(output_dir, name, schedule_hash) / timestamp_slug(now)


def latest_run_dir(config_path: Path, output_dir: Path) -> Path | None:
    """Return the most recent run directory for a config, if any."""

    runs_root = output_dir / _RUNS_DIRNAME
    if not runs_root.exists():
        return None
    group_dirs: list[Path] = []
    preferred = run_group_dir(config_path, output_dir)
    if preferred.exists():
        group_dirs.append(preferred)
    suffix = f"-{_hash8(config_path.stem)}"
    for entry in runs_root.iterdir():
        if not entry.is_dir():
            continue
        if entry == preferred:
            continue
        if entry.name.endswith(suffix):
            group_dirs.append(entry)
    if not group_dirs:
        return None
    candidates: list[Path] = []
    for group in group_dirs:
        for entry in group.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.isdigit() and len(entry.name) == 14:
                candidates.append(entry)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def latest_run_dir_for_key(output_dir: Path, name: str, schedule_hash: str) -> Path | None:
    """Return the most recent run directory for an exact logical schedule key."""

    group = run_group_dir_for_key(output_dir, name, schedule_hash)
    if not group.is_dir():
        return None
    candidates = [
        entry for entry in group.iterdir() if entry.is_dir() and entry.name.isdigit() and len(entry.name) == 14
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.name)


__all__ = [
    "default_run_dir",
    "latest_run_dir",
    "latest_run_dir_for_key",
    "run_dir_for_name",
    "run_dir_for_key",
    "run_group_dir",
    "run_group_dir_for_name",
    "run_group_dir_for_key",
    "timestamp_slug",
]
