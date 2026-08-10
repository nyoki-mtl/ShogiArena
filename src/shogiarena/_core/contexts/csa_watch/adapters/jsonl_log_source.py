"""Tail ``{run_id}-events.jsonl`` files that a bridge process is still appending to.

Two properties matter more than speed here. A record is only a record once its
newline has arrived, so a trailing fragment is held back rather than parsed. And a
single undecodable byte must not end the read, so decoding replaces instead of
raising: one corrupt line becomes one unparsable record, not a dead watcher.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path

from shogiarena._core.contexts.csa_watch.ports.log_source_ports import (
    EVENTS_LOG_SUFFIX,
    DiscoveredRun,
    LogChunk,
)

logger = logging.getLogger(__name__)

_RESTART_ANCHOR_BYTES = 4096


class JsonlLogCursor:
    """Byte-offset cursor with a buffer for the record that has not landed yet."""

    def __init__(self, run: DiscoveredRun) -> None:
        self._run_id = run.run_id
        self._path = run.path
        self._offset = 0
        # Held as bytes, not text: a poll can land in the middle of a multi-byte
        # character, and decoding each half separately would replace both with
        # U+FFFD and hand the fold a silently corrupted line.
        self._partial = b""
        self._file_identity: tuple[int, int] | None = None
        self._anchor_offset = 0
        self._anchor = b""

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def path(self) -> Path:
        return self._path

    def read_new_lines(self) -> LogChunk:
        restarted = False
        try:
            with self._path.open("rb") as handle:
                stat = os.fstat(handle.fileno())
                identity = (stat.st_dev, stat.st_ino)
                size = stat.st_size
                identity_changed = self._file_identity is not None and identity != self._file_identity
                anchor_changed = False
                if self._anchor:
                    handle.seek(self._anchor_offset)
                    anchor_changed = handle.read(len(self._anchor)) != self._anchor
                if size < self._offset or identity_changed or anchor_changed:
                    # Shrink, replacement, and rewriting of consumed bytes all mean
                    # this is no longer the append-only stream we were folding.
                    logger.info("csa event log %s changed; restarting from offset 0", self._path)
                    self._offset = 0
                    self._partial = b""
                    self._anchor_offset = 0
                    self._anchor = b""
                    restarted = True
                self._file_identity = identity
                if size == self._offset:
                    return LogChunk(lines=(), has_partial_line=bool(self._partial), restarted=restarted)
                handle.seek(self._offset)
                raw = handle.read(size - self._offset)
                self._offset += len(raw)
                self._anchor_offset = max(0, self._offset - _RESTART_ANCHOR_BYTES)
                handle.seek(self._anchor_offset)
                self._anchor = handle.read(self._offset - self._anchor_offset)
        except OSError as exc:
            logger.warning("failed to read csa event log %s: %s", self._path, exc)
            return LogChunk(lines=(), has_partial_line=bool(self._partial), restarted=restarted)

        buffered = self._partial + raw
        segments = buffered.split(b"\n")
        self._partial = segments.pop()
        lines = tuple(
            decoded
            for decoded in (segment.decode("utf-8", errors="replace").rstrip("\r") for segment in segments)
            if decoded.strip()
        )
        return LogChunk(lines=lines, has_partial_line=bool(self._partial.strip()), restarted=restarted)

    def close(self) -> None:
        self._partial = b""
        self._file_identity = None
        self._anchor = b""


class JsonlLogSource:
    """Discovers event logs under one directory and hands out cursors."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    def discover_runs(self) -> Sequence[DiscoveredRun]:
        try:
            candidates = sorted(self._log_dir.glob(f"*{EVENTS_LOG_SUFFIX}"))
        except OSError as exc:
            logger.warning("failed to scan csa log dir %s: %s", self._log_dir, exc)
            return ()
        runs = [
            DiscoveredRun(run_id=path.name[: -len(EVENTS_LOG_SUFFIX)], path=path)
            for path in candidates
            if path.name != EVENTS_LOG_SUFFIX
        ]
        # Run ids are epoch seconds, so lexicographic order is chronological; the
        # newest run gets worker index 0 and stays there while it is the newest.
        runs.sort(key=lambda run: run.run_id, reverse=True)
        return tuple(runs)

    def open_cursor(self, run: DiscoveredRun) -> JsonlLogCursor:
        return JsonlLogCursor(run)


__all__ = ["JsonlLogCursor", "JsonlLogSource"]
