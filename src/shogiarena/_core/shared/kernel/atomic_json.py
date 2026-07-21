"""Atomic JSON file writing helpers."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from shogiarena._core.shared.kernel.json_types import JsonValue

logger = logging.getLogger(__name__)


def _fsync_directory(directory: Path) -> None:
    """Persist the rename itself, not just the file contents.

    Windows にはディレクトリを開いて fsync する手段がないため no-op になる。
    """

    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        logger.debug("Skipped directory fsync for %s: %s", directory, exc)
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        logger.debug("Directory fsync failed for %s: %s", directory, exc)
    finally:
        os.close(fd)


def write_json_atomic(path: Path, payload: JsonValue, *, indent: int = 2) -> None:
    """Write JSON via a temporary file and atomic rename.

    write-ahead マーカーの永続化に使うため、rename の前に内容を fsync する。
    fsync が無いと、プロセスの生存中は正しく見えても電源断で rename だけが
    先に到達し、内容が失われうる。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)
    _fsync_directory(path.parent)


__all__ = ["write_json_atomic"]
