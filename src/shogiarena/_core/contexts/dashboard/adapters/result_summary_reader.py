"""SQLite-backed reader for offline result summaries."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from sqlalchemy import select

from shogiarena._core.contexts.game_session.domain.result_summary_models import ResultSummaryGameRow
from shogiarena._core.contexts.game_session.domain.run_health import (
    COMPLETION_STATUS_SCHEMA_VERSION,
    RunHealthStatus,
    RunTerminationReason,
)
from shogiarena._core.platform.db.store.entities import Game, Player
from shogiarena._core.platform.db.store.repository_factory import (
    SQLiteShogiDBFactory,
    build_sqlite_read_only_uri,
)
from shogiarena._core.shared.kernel.game_results import parse_game_result_name

_LEGACY_V1_0_VERSION_PATTERN = re.compile(r"1\.0\.\d+")
_TERMINAL_STATUS_VALUES = frozenset(status.value for status in RunHealthStatus)
_TERMINATION_REASON_VALUES = frozenset(reason.value for reason in RunTerminationReason)


class SQLiteResultSummaryReader:
    """`game.db` から完了済み対局行を読み取る。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def read_games(self) -> tuple[ResultSummaryGameRow, ...]:
        with TemporaryDirectory(prefix="shogiarena-results-") as temp_dir:
            snapshot_path = Path(temp_dir) / self._db_path.name
            if self._can_copy_file_family():
                # 終端 commit 済みの run では writer が閉じているので、WAL family を
                # byte-for-byte 複製しても一貫性が崩れない。source の SHM も更新しない。
                self._copy_file_family(snapshot_path)
            else:
                # live / completion 不明の DB は checkpoint と競合しうるため、
                # SQLite 自身の snapshot 契約を使う。
                self._backup_database(snapshot_path)
            repository = SQLiteShogiDBFactory(snapshot_path, read_only=True).create()
            try:
                black_player = Player.__table__.alias("black_player")
                white_player = Player.__table__.alias("white_player")
                query = (
                    select(
                        Game.game_name,
                        black_player.c.player_name,
                        white_player.c.player_name,
                        Game.game_result,
                    )
                    .join(black_player, Game.black_player_id == black_player.c.id)
                    .join(white_player, Game.white_player_id == white_player.c.id)
                    .order_by(Game.id.asc())
                )
                rows: list[ResultSummaryGameRow] = []
                for game_name, black_name, white_name, raw_result in repository.session.execute(query).all():
                    raw_result_text = str(raw_result)
                    rows.append(
                        ResultSummaryGameRow(
                            game_name=str(game_name),
                            black_player=str(black_name),
                            white_player=str(white_name),
                            result=parse_game_result_name(raw_result_text),
                            raw_result=raw_result_text,
                        )
                    )
                return tuple(rows)
            finally:
                repository.close_db()

    def _can_copy_file_family(self) -> bool:
        """writer 終了を artifact から確認できる run だけ静的コピーを許す。"""

        run_dir = self._db_path.parent
        if not (run_dir / "completed.flag").is_file():
            return False

        status_path = run_dir / "completion_status.json"
        status = self._load_json_object(status_path)
        if status is not None:
            return (
                status.get("schema_version") == COMPLETION_STATUS_SCHEMA_VERSION
                and status.get("status") in _TERMINAL_STATUS_VALUES
                and status.get("termination_reason") in _TERMINATION_REASON_VALUES
                and status.get("is_provisional") is False
            )
        if status_path.exists():
            return False

        # v1.0.x は completion_status.json 導入前。sealed manifest と marker の
        # 両方が揃う既知の legacy artifact だけを互換例外にする。
        manifest = self._load_json_object(run_dir / "manifest.json")
        version = manifest.get("shogiarena_version") if manifest is not None else None
        return (
            manifest is not None
            and manifest.get("schema_version") == 2
            and manifest.get("status") == "provenance_sealed"
            and isinstance(version, str)
            and _LEGACY_V1_0_VERSION_PATTERN.fullmatch(version) is not None
        )

    @staticmethod
    def _load_json_object(path: Path) -> dict[str, object] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        return {str(key): item for key, item in value.items()}

    def _copy_file_family(self, snapshot_path: Path) -> None:
        """確定済み DB と既存 WAL/SHM を同じ basename で一時領域へ複製する。"""

        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{self._db_path}{suffix}")
            if suffix and not source.exists():
                continue
            copyfile(source, Path(f"{snapshot_path}{suffix}"))

    def _backup_database(self, snapshot_path: Path) -> None:
        """SQLite Online Backup API で一貫した単一 DB snapshot を作る。"""

        source = sqlite3.connect(build_sqlite_read_only_uri(self._db_path), uri=True)
        destination = sqlite3.connect(snapshot_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()


__all__ = ["SQLiteResultSummaryReader"]
