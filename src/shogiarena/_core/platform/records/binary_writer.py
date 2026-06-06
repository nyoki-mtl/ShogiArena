from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import rshogi

from shogiarena._core.platform.records.codec_registry import RecordSerializer
from shogiarena._core.platform.records.codecs import get_serializer, iter_psv_entries
from shogiarena._core.platform.records.index_store import (
    RecordIndexEntry,
    append_record_index_entry,
    build_records_manifest_payload,
    load_record_index,
    next_file_index,
    truncate_unindexed_record_bytes,
    written_game_ids,
)
from shogiarena._core.platform.records.manifest_store import (
    load_manifest,
    write_manifest,
)
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str


@dataclass(frozen=True)
class RecordBinaryWriterConfig:
    """Configuration for binary record output."""

    format_id: str
    output_dir: Path
    max_positions_per_file: int
    max_games_per_file: int | None
    file_prefix: str


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
        self._index_path = self._config.output_dir / "records_index.jsonl"
        self._ensure_existing_output_is_compatible()
        self._index_entries = load_record_index(self._index_path)
        truncate_unindexed_record_bytes(
            output_dir=self._config.output_dir,
            entries=self._index_entries,
            file_prefix=self._config.file_prefix,
            format_id=self._config.format_id,
        )
        self._written_game_ids = written_game_ids(self._index_entries)
        self._file_index = max(self._find_next_index(), next_file_index(self._index_entries))
        self._positions_in_file = 0
        self._games_in_file = 0
        self._bytes_in_file = 0
        self._handle: BinaryIO | None = self._open_new_file()
        self._write_manifest()

    def append_record(
        self,
        record: rshogi.record.GameRecord,
        *,
        game_id: str | None = None,
        game_type: str | None = None,
    ) -> None:
        """Append GameRecord to binary output."""

        resolved_game_id = game_id or self._record_game_id(record)
        resolved_game_type = game_type or self._record_game_type(record)
        if not resolved_game_id:
            raise ValueError("records output requires a game_id")
        if resolved_game_id in self._written_game_ids:
            return
        if self._config.format_id == "psv":
            self._append_psv(record, game_id=resolved_game_id, game_type=resolved_game_type)
            return
        self._append_sbinpack(record, game_id=resolved_game_id, game_type=resolved_game_type)

    def close(self) -> None:
        """Close current writing file."""

        if self._handle is not None:
            self._handle.close()
            self._handle = None
        self._write_manifest()

    def get_records_summary(self) -> dict[str, int]:
        """Return current output summary."""

        total_games = len(self._index_entries)
        total_positions = sum(entry.positions for entry in self._index_entries)
        total_bytes = sum(entry.byte_end - entry.byte_start for entry in self._index_entries)
        file_count = len({entry.file for entry in self._index_entries})
        return {
            "totalGames": total_games,
            "totalPositions": total_positions,
            "totalBytes": total_bytes,
            "fileCount": file_count,
        }

    def written_game_ids(self) -> set[str]:
        """Return game IDs already committed to records output."""

        return set(self._written_game_ids)

    def _append_psv(self, record: rshogi.record.GameRecord, *, game_id: str, game_type: str) -> None:
        payloads = tuple(iter_psv_entries(record))
        incoming_positions = len(payloads)
        max_games = self._config.max_games_per_file
        if max_games is not None and max_games > 0 and (self._games_in_file + 1) > max_games:
            self._rotate()
        max_positions = self._config.max_positions_per_file
        if self._has_current_file_data() and (self._positions_in_file + incoming_positions) > max_positions:
            self._rotate()
        handle = self._handle
        if handle is None:
            raise RuntimeError("Record writer handle is closed")
        byte_start = handle.tell()
        for entry_payload in payloads:
            handle.write(entry_payload)
            self._positions_in_file += 1
            self._bytes_in_file += len(entry_payload)
        handle.flush()
        byte_end = handle.tell()
        self._games_in_file += 1
        self._commit_index_entry(
            game_id=game_id,
            game_type=game_type,
            positions=incoming_positions,
            byte_start=byte_start,
            byte_end=byte_end,
        )

    def _append_sbinpack(self, record: rshogi.record.GameRecord, *, game_id: str, game_type: str) -> None:
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
        byte_start = handle.tell()
        handle.write(payload)
        handle.flush()
        self._games_in_file += 1
        self._positions_in_file += incoming_positions
        self._bytes_in_file += len(payload)
        byte_end = handle.tell()
        self._commit_index_entry(
            game_id=game_id,
            game_type=game_type,
            positions=incoming_positions,
            byte_start=byte_start,
            byte_end=byte_end,
        )

    def _rotate(self) -> None:
        if self._handle is not None:
            self._handle.close()
        self._write_manifest()
        self._file_index += 1
        self._positions_in_file = 0
        self._games_in_file = 0
        self._bytes_in_file = 0
        self._handle = self._open_new_file()

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

    def _commit_index_entry(
        self,
        *,
        game_id: str,
        game_type: str,
        positions: int,
        byte_start: int,
        byte_end: int,
    ) -> None:
        entry = RecordIndexEntry(
            game_id=game_id,
            game_type=game_type,
            format_id=self._config.format_id,
            file=self._build_path(self._file_index).name,
            file_index=self._file_index,
            record_index=self._games_in_file,
            positions=positions,
            byte_start=byte_start,
            byte_end=byte_end,
        )
        append_record_index_entry(self._index_path, entry)
        self._index_entries.append(entry)
        self._written_game_ids.add(game_id)
        self._write_manifest()

    def _write_manifest(self) -> None:
        payload = build_records_manifest_payload(
            format_id=self._config.format_id,
            file_prefix=self._config.file_prefix,
            entries=self._index_entries,
        )
        write_manifest(self._manifest_path, payload)

    def _ensure_existing_output_is_compatible(self) -> None:
        data_files = list(self._config.output_dir.glob(f"{self._config.file_prefix}_*.{self._config.format_id}"))
        manifest = load_manifest(self._manifest_path)
        has_existing = bool(data_files) or self._manifest_path.exists() or self._index_path.exists()
        if not has_existing:
            return
        schema_version = coerce_int(manifest.get("schema_version"))
        if schema_version != 2 or not self._index_path.exists():
            raise ValueError(
                "Existing records output is not compatible with schema v2; "
                "start fresh or choose an empty records_output.output_dir"
            )

    @staticmethod
    def _record_game_id(record: rshogi.record.GameRecord) -> str | None:
        metadata = record.metadata
        attributes = metadata.attributes
        return coerce_str(attributes.get("game_name")) or coerce_str(getattr(metadata, "game_name", None))

    @staticmethod
    def _record_game_type(record: rshogi.record.GameRecord) -> str:
        metadata = record.metadata
        attributes = metadata.attributes
        return coerce_str(attributes.get("game_type")) or coerce_str(getattr(metadata, "game_type", None)) or "arena"

    def _has_current_file_data(self) -> bool:
        return self._games_in_file > 0 or self._positions_in_file > 0 or self._bytes_in_file > 0


__all__ = [
    "RecordBinaryWriter",
    "RecordBinaryWriterConfig",
]
