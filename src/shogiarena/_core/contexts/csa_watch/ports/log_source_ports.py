"""Contracts for reading a CSA bridge event log that is still being written."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

EVENTS_LOG_SUFFIX = "-events.jsonl"


@dataclass(frozen=True)
class DiscoveredRun:
    """One ``{run_id}-events.jsonl`` found under the watched directory."""

    run_id: str
    path: Path


@dataclass(frozen=True)
class LogChunk:
    """Newline-terminated lines read in one poll, plus what is still incomplete.

    ``has_partial_line`` is an observation, not an error: records are flushed one
    line at a time, so a trailing fragment means the writer stopped mid-record.
    """

    lines: tuple[str, ...]
    has_partial_line: bool
    # The file shrank, so these lines belong to a different byte stream than the
    # ones before them. Whoever folds them has to start from nothing: a shrink
    # means "this is not the file I was reading", and carrying state across it
    # merges two sessions into one run — games, alerts, ponder and liveness all
    # mixed. Resetting the offset alone, which is what this replaced, fixes only
    # half of that.
    restarted: bool = False


class CsaLogCursorPort(Protocol):
    """A resumable read position inside one event log."""

    @property
    def run_id(self) -> str: ...

    @property
    def path(self) -> Path: ...

    def read_new_lines(self) -> LogChunk:
        """Return complete lines appended since the last call. Blocking."""
        ...

    def close(self) -> None: ...


class CsaLogSourcePort(Protocol):
    """Discovery and cursor creation over a directory of bridge event logs."""

    def discover_runs(self) -> Sequence[DiscoveredRun]:
        """Newest run first. Blocking."""
        ...

    def open_cursor(self, run: DiscoveredRun) -> CsaLogCursorPort: ...


class CsaLogSourceFactory(Protocol):
    """Builds a log source for one watched directory."""

    def __call__(self, log_dir: Path) -> CsaLogSourcePort: ...


__all__ = [
    "EVENTS_LOG_SUFFIX",
    "CsaLogCursorPort",
    "CsaLogSourceFactory",
    "CsaLogSourcePort",
    "DiscoveredRun",
    "LogChunk",
]
