"""Helpers for generating and locating run directories."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

_RUNS_DIRNAME = "runs"


def _normalize_bytes(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def config_hash8(path: Path) -> str:
    """Return an 8-char hash of the config file contents."""
    data = _normalize_bytes(path.read_bytes())
    return hashlib.sha256(data).hexdigest()[:8]


def run_group_dir(config_path: Path, output_dir: Path) -> Path:
    """Return the directory that groups runs for a specific config."""
    stem = config_path.stem
    digest = config_hash8(config_path)
    return output_dir / _RUNS_DIRNAME / f"{stem}-{digest}"


def run_group_dir_for_name(config_path: Path, output_dir: Path, name: str) -> Path:
    """Return the run group dir using a custom name and config hash."""
    safe = name.strip()
    if not safe:
        raise ValueError("run name must not be empty")
    if Path(safe).name != safe or "/" in safe or "\\" in safe:
        raise ValueError("run name must not include path separators")
    digest = config_hash8(config_path)
    return output_dir / _RUNS_DIRNAME / f"{safe}-{digest}"


def timestamp_slug(value: datetime | None = None) -> str:
    """Return a YYYYMMDDHHmmSS timestamp slug in local time."""
    return (value or datetime.now()).strftime("%Y%m%d%H%M%S")


def default_run_dir(config_path: Path, output_dir: Path, *, now: datetime | None = None) -> Path:
    """Return the default run directory for a config file."""
    return run_group_dir(config_path, output_dir) / timestamp_slug(now)


def run_dir_for_name(config_path: Path, output_dir: Path, name: str, *, now: datetime | None = None) -> Path:
    """Return the run directory for a custom name."""
    return run_group_dir_for_name(config_path, output_dir, name) / timestamp_slug(now)


def latest_run_dir(config_path: Path, output_dir: Path) -> Path | None:
    """Return the most recent run directory for a config, if any."""
    runs_root = output_dir / _RUNS_DIRNAME
    if not runs_root.exists():
        return None
    digest = config_hash8(config_path)
    group_dirs: list[Path] = []
    preferred = run_group_dir(config_path, output_dir)
    if preferred.exists():
        group_dirs.append(preferred)
    suffix = f"-{digest}"
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


__all__ = [
    "config_hash8",
    "default_run_dir",
    "run_dir_for_name",
    "run_group_dir_for_name",
    "latest_run_dir",
    "run_group_dir",
    "timestamp_slug",
]
