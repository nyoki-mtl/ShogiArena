from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, cast

import rshogi

from shogiarena.records import get_codec
from shogiarena.records.formats.base import RecordCodec
from shogiarena.records.formats.psv import iter_psv_entries


@dataclass(frozen=True)
class RecordBinaryWriterConfig:
    """バイナリ棋譜の出力設定。"""

    format_id: str
    output_dir: Path
    max_positions_per_file: int
    max_games_per_file: int | None
    file_prefix: str


@dataclass
class RecordManifestTotals:
    """バイナリ棋譜出力の累積カウント。"""

    games: int
    positions: int
    bytes_count: int
    file_count: int


class RecordBinaryWriter:
    """psv/sbinpack 形式のバイナリ書き出しを担当する。"""

    def __init__(self, config: RecordBinaryWriterConfig) -> None:
        self._config = config
        codec = get_codec(config.format_id)
        if codec is None:
            raise ValueError(f"Unsupported record format: {config.format_id}")
        self._codec: RecordCodec = codec
        if config.format_id not in {"psv", "sbinpack"}:
            raise ValueError("Binary writer supports only 'psv' or 'sbinpack' formats")
        if config.max_positions_per_file <= 0:
            raise ValueError("max_positions_per_file must be positive")
        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._config.output_dir / "records_manifest.json"
        self._manifest_totals = self._summarize_manifest(self._load_manifest())
        self._file_index = self._find_next_index()
        self._positions_in_file = 0
        self._games_in_file = 0
        self._bytes_in_file = 0
        self._handle: BinaryIO | None = self._open_new_file()
        self._current_meta = self._create_file_meta(self._file_index)

    def append_record(self, record: rshogi.record.GameRecord) -> None:
        """GameRecord をバイナリ形式で追記する。"""
        if self._config.format_id == "psv":
            self._append_psv(record)
            return
        self._append_sbinpack(record)

    def close(self) -> None:
        """書き出し中のファイルを閉じる。"""
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        self._finalize_current_meta()

    def get_records_summary(self) -> dict[str, int]:
        """現在のレコード出力サマリを返す。"""
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
        if self._would_exceed_games(1):
            self._rotate()
        for entry_payload in iter_psv_entries(record):
            if self._would_exceed_positions(1):
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
        if self._would_exceed_games(1) or self._would_exceed_positions(incoming_positions):
            self._rotate()
        payload = self._codec.serialize(record)
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

    def _would_exceed_positions(self, incoming_positions: int) -> bool:
        max_positions = self._config.max_positions_per_file
        if max_positions <= 0:
            return False
        return (self._positions_in_file + incoming_positions) > max_positions

    def _would_exceed_games(self, incoming_games: int) -> bool:
        max_games = self._config.max_games_per_file
        if max_games is None or max_games <= 0:
            return False
        return (self._games_in_file + incoming_games) > max_games

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

    def _create_file_meta(self, index: int) -> dict[str, object]:
        return {
            "file": self._build_path(index).name,
            "index": index,
            "format": self._config.format_id,
            "games": 0,
            "positions": 0,
            "bytes": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "closed_at": None,
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
        if self._current_meta.get("closed_at") is None:
            self._current_meta["closed_at"] = datetime.now(timezone.utc).isoformat()
        self._refresh_current_meta()
        self._append_manifest_entry(dict(self._current_meta))
        self._current_meta = None

    def _append_manifest_entry(self, entry: dict[str, object]) -> None:
        manifest = self._load_manifest()
        files: list[dict[str, object]] = []
        files_raw = manifest.get("files")
        if isinstance(files_raw, list):
            for item in files_raw:
                if isinstance(item, dict):
                    files.append(cast(dict[str, object], item))
        files.append(entry)
        payload = {
            "format": self._config.format_id,
            "file_prefix": self._config.file_prefix,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
        }
        self._manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._update_manifest_totals(entry)

    def _summarize_manifest(self, manifest: dict[str, object]) -> RecordManifestTotals:
        files_raw = manifest.get("files")
        if not isinstance(files_raw, list):
            return RecordManifestTotals(games=0, positions=0, bytes_count=0, file_count=0)
        total_games = 0
        total_positions = 0
        total_bytes = 0
        file_count = 0
        for entry in files_raw:
            if not isinstance(entry, dict):
                continue
            file_count += 1
            entry_map = cast(Mapping[str, object], entry)
            games = entry_map.get("games")
            positions = entry_map.get("positions")
            bytes_count = entry_map.get("bytes")
            if isinstance(games, int):
                total_games += games
            if isinstance(positions, int):
                total_positions += positions
            if isinstance(bytes_count, int):
                total_bytes += bytes_count
        return RecordManifestTotals(
            games=total_games,
            positions=total_positions,
            bytes_count=total_bytes,
            file_count=file_count,
        )

    def _update_manifest_totals(self, entry: dict[str, object]) -> None:
        games = entry.get("games")
        positions = entry.get("positions")
        bytes_count = entry.get("bytes")
        added_games = games if isinstance(games, int) else 0
        added_positions = positions if isinstance(positions, int) else 0
        added_bytes = bytes_count if isinstance(bytes_count, int) else 0
        self._manifest_totals = RecordManifestTotals(
            games=self._manifest_totals.games + added_games,
            positions=self._manifest_totals.positions + added_positions,
            bytes_count=self._manifest_totals.bytes_count + added_bytes,
            file_count=self._manifest_totals.file_count + 1,
        )

    def _has_current_file_data(self) -> bool:
        return self._games_in_file > 0 or self._positions_in_file > 0 or self._bytes_in_file > 0

    def _load_manifest(self) -> dict[str, object]:
        if not self._manifest_path.exists():
            return {}
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}
