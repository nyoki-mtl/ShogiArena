from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, cast

import rshogi

from shogiarena._core.platform.records.codec_registry import RecordSerializer
from shogiarena._core.platform.records.codecs import get_serializer, iter_psv_entries
from shogiarena._core.platform.records.manifest_store import (
    append_manifest_entry,
    load_manifest,
    summarize_manifest_totals,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


@dataclass(frozen=True)
class RecordBinaryWriterConfig:
    """Configuration for binary record output."""

    format_id: str
    output_dir: Path
    max_positions_per_file: int
    max_games_per_file: int | None
    file_prefix: str


@dataclass
class _RecordManifestTotals:
    """Accumulated counters for binary record output."""

    games: int
    positions: int
    bytes_count: int
    file_count: int


class RecordBinaryWriter:
    """Writer for psv/sbinpack record binaries."""

    def __init__(self, config: RecordBinaryWriterConfig) -> None:
        self._config = config
        if config.format_id not in {"psv", "sbinpack"}:
            raise ValueError("Binary writer supports only 'psv' or 'sbinpack' formats")
        self._serializer: RecordSerializer | None = None
        if config.format_id == "sbinpack":
            serializer = get_serializer(config.format_id)
            if serializer is None:
                raise ValueError(f"Unsupported record format: {config.format_id}")
            self._serializer = serializer
        if config.max_positions_per_file <= 0:
            raise ValueError("max_positions_per_file must be positive")
        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._config.output_dir / "records_manifest.json"
        total_games, total_positions, total_bytes, file_count = summarize_manifest_totals(
            load_manifest(self._manifest_path)
        )
        self._manifest_totals = _RecordManifestTotals(
            games=total_games,
            positions=total_positions,
            bytes_count=total_bytes,
            file_count=file_count,
        )
        self._file_index = self._find_next_index()
        self._positions_in_file = 0
        self._games_in_file = 0
        self._bytes_in_file = 0
        self._handle: BinaryIO | None = self._open_new_file()
        self._current_meta: JsonObject | None = self._create_file_meta(self._file_index)

    def append_record(self, record: rshogi.record.GameRecord) -> None:
        """Append GameRecord to binary output."""

        if self._config.format_id == "psv":
            self._append_psv(record)
            return
        self._append_sbinpack(record)

    def close(self) -> None:
        """Close current writing file."""

        if self._handle is not None:
            self._handle.close()
            self._handle = None
        self._finalize_current_meta()

    def get_records_summary(self) -> dict[str, int]:
        """Return current output summary."""

        totals = self._manifest_totals
        current_games = self._games_in_file
        current_positions = self._positions_in_file
        current_bytes = self._bytes_in_file
        current_files = 1 if self._has_current_file_data() else 0
        return {
            "totalGames": totals.games + current_games,
            "totalPositions": totals.positions + current_positions,
            "totalBytes": totals.bytes_count + current_bytes,
            "fileCount": totals.file_count + current_files,
        }

    def _append_psv(self, record: rshogi.record.GameRecord) -> None:
        max_games = self._config.max_games_per_file
        if max_games is not None and max_games > 0 and (self._games_in_file + 1) > max_games:
            self._rotate()
        for entry_payload in iter_psv_entries(record):
            max_positions = self._config.max_positions_per_file
            if max_positions > 0 and (self._positions_in_file + 1) > max_positions:
                self._rotate()
            handle = self._handle
            if handle is None:
                raise RuntimeError("Record writer handle is closed")
            handle.write(entry_payload)
            self._positions_in_file += 1
            self._bytes_in_file += len(entry_payload)
        self._games_in_file += 1
        self._refresh_current_meta()

    def _append_sbinpack(self, record: rshogi.record.GameRecord) -> None:
        incoming_positions = len(record.moves)
        max_games = self._config.max_games_per_file
        exceeds_games = max_games is not None and max_games > 0 and (self._games_in_file + 1) > max_games
        max_positions = self._config.max_positions_per_file
        exceeds_positions = max_positions > 0 and (self._positions_in_file + incoming_positions) > max_positions
        if exceeds_games or exceeds_positions:
            self._rotate()
        if self._serializer is None:
            raise RuntimeError("sbinpack serializer not initialized")
        payload = self._serializer.serialize(record)
        if not isinstance(payload, bytes):
            raise TypeError("sbinpack codec must return bytes")
        handle = self._handle
        if handle is None:
            raise RuntimeError("Record writer handle is closed")
        handle.write(payload)
        self._games_in_file += 1
        self._positions_in_file += incoming_positions
        self._bytes_in_file += len(payload)
        self._refresh_current_meta()

    def _rotate(self) -> None:
        if self._handle is not None:
            self._handle.close()
        self._finalize_current_meta()
        self._file_index += 1
        self._positions_in_file = 0
        self._games_in_file = 0
        self._bytes_in_file = 0
        self._handle = self._open_new_file()
        self._current_meta = self._create_file_meta(self._file_index)

    def _open_new_file(self) -> BinaryIO:
        path = self._build_path(self._file_index)
        return path.open("ab")

    def _build_path(self, index: int) -> Path:
        ext = self._config.format_id
        name = f"{self._config.file_prefix}_{index:05d}.{ext}"
        return self._config.output_dir / name

    def _find_next_index(self) -> int:
        pattern = re.compile(rf"^{re.escape(self._config.file_prefix)}_(\d+)\.{re.escape(self._config.format_id)}$")
        max_index = 0
        for path in self._config.output_dir.glob(f"{self._config.file_prefix}_*.{self._config.format_id}"):
            match = pattern.match(path.name)
            if match:
                max_index = max(max_index, int(match.group(1)))
        return max_index + 1

    def _create_file_meta(self, index: int) -> JsonObject:
        return {
            "file": self._build_path(index).name,
            "index": index,
            "format": self._config.format_id,
            "games": 0,
            "positions": 0,
            "bytes": 0,
            "created_at_iso": datetime.now(UTC).isoformat(),
            "closed_at_iso": None,
        }

    def _refresh_current_meta(self) -> None:
        if self._current_meta is None:
            return
        self._current_meta["games"] = self._games_in_file
        self._current_meta["positions"] = self._positions_in_file
        self._current_meta["bytes"] = self._bytes_in_file

    def _finalize_current_meta(self) -> None:
        if self._current_meta is None:
            return
        if self._current_meta.get("closed_at_iso") is None:
            self._current_meta["closed_at_iso"] = datetime.now(UTC).isoformat()
        self._refresh_current_meta()
        entry = dict(self._current_meta)
        append_manifest_entry(
            self._manifest_path,
            format_id=self._config.format_id,
            file_prefix=self._config.file_prefix,
            entry=entry,
        )
        games = entry.get("games")
        positions = entry.get("positions")
        bytes_count = entry.get("bytes")
        added_games = cast(int, games)
        added_positions = cast(int, positions)
        added_bytes = cast(int, bytes_count)
        self._manifest_totals = _RecordManifestTotals(
            games=self._manifest_totals.games + added_games,
            positions=self._manifest_totals.positions + added_positions,
            bytes_count=self._manifest_totals.bytes_count + added_bytes,
            file_count=self._manifest_totals.file_count + 1,
        )
        self._current_meta = None

    def _has_current_file_data(self) -> bool:
        return self._games_in_file > 0 or self._positions_in_file > 0 or self._bytes_in_file > 0


__all__ = [
    "RecordBinaryWriter",
    "RecordBinaryWriterConfig",
]
