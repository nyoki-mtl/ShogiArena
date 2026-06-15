"""Append-only index helpers for binary record outputs."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecordIndexEntry:
    """One committed record write in a binary records output."""

    game_id: str
    game_type: str
    format_id: str
    file: str
    file_index: int
    record_index: int
    positions: int
    byte_start: int
    byte_end: int

    def to_json_object(self) -> JsonObject:
        """Serialize the index entry."""

        return {
            "game_id": self.game_id,
            "game_type": self.game_type,
            "format": self.format_id,
            "file": self.file,
            "file_index": self.file_index,
            "record_index": self.record_index,
            "positions": self.positions,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
        }


@dataclass(slots=True)
class _RecordFileSummary:
    file: str
    index: int
    format_id: str
    games: int = 0
    positions: int = 0
    bytes_count: int = 0

    def to_json_object(self) -> JsonObject:
        return {
            "file": self.file,
            "index": self.index,
            "format": self.format_id,
            "games": self.games,
            "positions": self.positions,
            "bytes": self.bytes_count,
        }


def _parse_entry(raw: Mapping[str, object], *, line_number: int) -> RecordIndexEntry:
    game_id = coerce_str(raw.get("game_id"))
    game_type = coerce_str(raw.get("game_type"))
    format_id = coerce_str(raw.get("format"))
    file_name = coerce_str(raw.get("file"))
    file_index = coerce_int(raw.get("file_index"))
    record_index = coerce_int(raw.get("record_index"))
    positions = coerce_int(raw.get("positions"))
    byte_start = coerce_int(raw.get("byte_start"))
    byte_end = coerce_int(raw.get("byte_end"))
    if (
        not game_id
        or not game_type
        or not format_id
        or not file_name
        or file_index is None
        or record_index is None
        or positions is None
        or byte_start is None
        or byte_end is None
    ):
        raise ValueError(f"records index line {line_number} is missing required fields")
    if file_index <= 0 or record_index <= 0 or positions < 0 or byte_start < 0 or byte_end < byte_start:
        raise ValueError(f"records index line {line_number} has invalid counters")
    return RecordIndexEntry(
        game_id=game_id,
        game_type=game_type,
        format_id=format_id,
        file=file_name,
        file_index=file_index,
        record_index=record_index,
        positions=positions,
        byte_start=byte_start,
        byte_end=byte_end,
    )


def load_record_index(path: Path) -> list[RecordIndexEntry]:
    """Load committed record index entries from JSONL (tolerating a torn final line)."""

    entries, _torn = _read_index_entries(path)
    return entries


def _read_index_entries(path: Path) -> tuple[list[RecordIndexEntry], bool]:
    """Parse index entries, tolerating a single torn final line from an interrupted append.

    Returns ``(entries, dropped_torn_line)``. A malformed JSON line that is *not* the last line is
    treated as genuine corruption and raises; only a truncated final line is dropped.
    """
    if not path.exists():
        return [], False
    text = path.read_text(encoding="utf-8")
    # A torn line is one the crash left without its terminating newline; a fully written (newline-
    # terminated) line that fails to parse is genuine corruption, not an interrupted append.
    has_trailing_newline = text.endswith("\n")
    non_empty: list[tuple[int, str]] = [
        (line_number, line.strip()) for line_number, line in enumerate(text.splitlines(), start=1) if line.strip()
    ]
    entries: list[RecordIndexEntry] = []
    for position, (line_number, stripped) in enumerate(non_empty):
        is_last = position == len(non_empty) - 1
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError as exc:
            if is_last and not has_trailing_newline:
                logger.warning("Dropping torn final records index line %d (interrupted append)", line_number)
                return entries, True
            raise ValueError(f"records index line {line_number} is not valid JSON") from exc
        if not isinstance(raw, Mapping):
            raise ValueError(f"records index line {line_number} must be an object")
        entries.append(_parse_entry(raw, line_number=line_number))
    return entries, False


def rewrite_record_index(path: Path, entries: Iterable[RecordIndexEntry]) -> None:
    """Atomically rewrite the index file to exactly ``entries`` (used during crash recovery)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry.to_json_object(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)


def _ensure_index_matches_config(entries: Iterable[RecordIndexEntry], *, file_prefix: str, format_id: str) -> None:
    """Reject an existing index that belongs to a different format or file prefix.

    Because the index is the source of truth for resume, opening an ``sbinpack`` output as ``psv``
    (or with another file_prefix) would otherwise mix incompatible records and corrupt the manifest.
    """
    for entry in entries:
        if entry.format_id != format_id:
            raise ValueError(
                f"Existing records index uses format {entry.format_id!r} but the writer is "
                f"configured for {format_id!r}; start fresh or choose a matching records_output."
            )
        if not (entry.file.startswith(f"{file_prefix}_") and entry.file.endswith(f".{format_id}")):
            raise ValueError(
                f"Existing records index entry {entry.file!r} does not match the configured "
                f"file_prefix {file_prefix!r}; start fresh or choose a matching records_output."
            )


def recover_record_index(
    *,
    index_path: Path,
    output_dir: Path,
    file_prefix: str,
    format_id: str,
) -> list[RecordIndexEntry]:
    """Load the index and repair it against the on-disk binaries after a possible crash.

    - tolerates a torn final index line (interrupted append),
    - drops entries whose ``byte_end`` exceeds the actual binary file size (binary tail lost),
    - truncates binaries to the committed extent / removes uncovered files,
    - rewrites the index file when anything was dropped.
    """

    entries, dropped = _read_index_entries(index_path)
    _ensure_index_matches_config(entries, file_prefix=file_prefix, format_id=format_id)
    file_size_cache: dict[str, int] = {}
    survivors: list[RecordIndexEntry] = []
    for entry in entries:
        size = file_size_cache.get(entry.file)
        if size is None:
            file_path = output_dir / entry.file
            size = file_path.stat().st_size if file_path.exists() else 0
            file_size_cache[entry.file] = size
        if entry.byte_end > size:
            logger.warning(
                "Dropping records index entry beyond file extent: %s byte_end=%d file_size=%d",
                entry.file,
                entry.byte_end,
                size,
            )
            dropped = True
            continue
        survivors.append(entry)
    if dropped:
        rewrite_record_index(index_path, survivors)
    truncate_unindexed_record_bytes(
        output_dir=output_dir,
        entries=survivors,
        file_prefix=file_prefix,
        format_id=format_id,
    )
    return survivors


def append_record_index_entry(path: Path, entry: RecordIndexEntry) -> None:
    """Append one committed record index entry."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.to_json_object(), ensure_ascii=False, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def written_game_ids(entries: Iterable[RecordIndexEntry]) -> set[str]:
    """Return game IDs that have committed records output."""

    return {entry.game_id for entry in entries}


def next_file_index(entries: Iterable[RecordIndexEntry], *, default: int = 1) -> int:
    """Return the next file index after committed entries."""

    max_index = 0
    for entry in entries:
        max_index = max(max_index, entry.file_index)
    return max(max_index + 1, default)


def truncate_unindexed_record_bytes(
    *,
    output_dir: Path,
    entries: Iterable[RecordIndexEntry],
    file_prefix: str,
    format_id: str,
) -> None:
    """Remove bytes/files not covered by committed index entries."""

    max_end_by_file: dict[str, int] = {}
    for entry in entries:
        max_end_by_file[entry.file] = max(max_end_by_file.get(entry.file, 0), entry.byte_end)

    pattern = f"{file_prefix}_*.{format_id}"
    for path in output_dir.glob(pattern):
        committed_size = max_end_by_file.get(path.name)
        if committed_size is None:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            continue
        current_size = path.stat().st_size
        if current_size > committed_size:
            logger.warning("Truncating unindexed records payload: %s -> %s bytes", path, committed_size)
            with path.open("r+b") as handle:
                handle.truncate(committed_size)


def build_records_manifest_payload(
    *,
    format_id: str,
    file_prefix: str,
    entries: Iterable[RecordIndexEntry],
) -> JsonObject:
    """Build the schema-v2 records manifest from index entries."""

    files_by_name: dict[str, _RecordFileSummary] = {}
    for entry in entries:
        file_payload = files_by_name.setdefault(
            entry.file,
            _RecordFileSummary(file=entry.file, index=entry.file_index, format_id=entry.format_id),
        )
        file_payload.games += 1
        file_payload.positions += entry.positions
        file_payload.bytes_count = max(file_payload.bytes_count, entry.byte_end)

    summaries = sorted(files_by_name.values(), key=lambda item: item.index)
    files = [summary.to_json_object() for summary in summaries]
    total_games = sum(summary.games for summary in summaries)
    total_positions = sum(summary.positions for summary in summaries)
    total_bytes = sum(summary.bytes_count for summary in summaries)
    return {
        "schema_version": 2,
        "format": format_id,
        "file_prefix": file_prefix,
        "totals": {
            "games": total_games,
            "positions": total_positions,
            "bytes": total_bytes,
            "files": len(files),
        },
        "index": {"path": "records_index.jsonl"},
        "files": files,
    }


__all__ = [
    "RecordIndexEntry",
    "append_record_index_entry",
    "build_records_manifest_payload",
    "load_record_index",
    "next_file_index",
    "recover_record_index",
    "rewrite_record_index",
    "truncate_unindexed_record_bytes",
    "written_game_ids",
]
