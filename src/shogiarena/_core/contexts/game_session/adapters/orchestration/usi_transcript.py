"""Per-game USI transcript artifact writing."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Literal, Protocol

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.usi_transcript_contract import (
    TranscriptRole,
    transcript_path_for_game,
)

TranscriptDetail = Literal["commands", "commands_and_info"]

_INFO_BOUND_TOKENS = {"lowerbound", "upperbound"}


class UsiTranscriptEnginePort(Protocol):
    """USI transcript writer が必要とする engine runtime 契約。"""

    name: str

    def register_io_log_handler(
        self,
        handler: Callable[[JsonObject], Awaitable[None] | None],
    ) -> Callable[[], None]: ...

    async def flush_io_log_handlers(self, *, timeout: float | None = None) -> None: ...


class UsiTranscriptWriter:
    """1 engine/1 side 分の USI transcript をファイルへ書き出す。"""

    def __init__(
        self,
        *,
        path: Path,
        game_id: str,
        role: TranscriptRole,
        engine_name: str,
        initial_sfen: str,
        detail: TranscriptDetail,
    ) -> None:
        self._path = path
        self._role = role
        self._detail = detail
        self._lock = threading.Lock()
        self._start_ts_ms: int | None = None
        self._pending_final_info: str | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("w", encoding="utf-8")
        self._write_header(
            game_id=game_id,
            role=role,
            engine_name=engine_name,
            initial_sfen=initial_sfen,
            detail=detail,
        )

    @property
    def path(self) -> Path:
        """出力先 path。"""

        return self._path

    def handle(self, entry: Mapping[str, object]) -> None:
        """USI I/O log entry を transcript に反映する。"""

        line_value = entry.get("line")
        if not isinstance(line_value, str) or not line_value:
            return
        line = line_value.strip()
        if not line:
            return

        direction = str(entry.get("dir") or "?")
        state = str(entry.get("state") or "-")
        timestamp = _coerce_ts_ms(entry.get("ts"))
        formatted = self._format_line(
            timestamp_ms=timestamp,
            direction=direction,
            state=state,
            line=line,
        )

        with self._lock:
            if line.startswith("info "):
                if self._detail == "commands_and_info":
                    self._write_locked(formatted)
                    return
                if _is_final_info_candidate(line):
                    self._pending_final_info = formatted
                return

            if _is_search_terminal_line(line) and self._pending_final_info is not None:
                self._write_locked(self._pending_final_info)
                self._pending_final_info = None
            self._write_locked(formatted)

    def close(self) -> None:
        """Transcript を閉じる。"""

        with self._lock:
            if self._pending_final_info is not None:
                self._write_locked(self._pending_final_info)
                self._pending_final_info = None
            self._write_marker_locked("close")
            self._handle.close()

    def _write_header(
        self,
        *,
        game_id: str,
        role: TranscriptRole,
        engine_name: str,
        initial_sfen: str,
        detail: TranscriptDetail,
    ) -> None:
        self._handle.write("# ShogiArena USI transcript\n")
        self._handle.write(f"# game_id: {game_id}\n")
        self._handle.write(f"# role: {role}\n")
        self._handle.write(f"# engine: {engine_name}\n")
        self._handle.write(f"# initial_sfen: {initial_sfen}\n")
        self._handle.write(f"# detail: {detail}\n")
        self._write_marker_locked("start")

    def _format_line(self, *, timestamp_ms: int, direction: str, state: str, line: str) -> str:
        if self._start_ts_ms is None:
            self._start_ts_ms = timestamp_ms
        elapsed_ms = max(0, timestamp_ms - self._start_ts_ms)
        return f"{elapsed_ms:010d}ms {direction} {state} {line}\n"

    def _write_marker_locked(self, marker: str) -> None:
        self._handle.write(f"{0:010d}ms marker {self._role} {marker}\n")
        self._handle.flush()

    def _write_locked(self, line: str) -> None:
        self._handle.write(line)
        self._handle.flush()


class GameUsiTranscriptContext:
    """対局中だけ両 engine の USI transcript handler を登録する。"""

    def __init__(
        self,
        *,
        run_dir: Path,
        game_id: str,
        initial_sfen: str,
        black_engine: UsiTranscriptEnginePort,
        white_engine: UsiTranscriptEnginePort,
        black_name: str,
        white_name: str,
        detail: TranscriptDetail,
    ) -> None:
        self._run_dir = run_dir
        self._game_id = game_id
        self._initial_sfen = initial_sfen
        self._black_engine = black_engine
        self._white_engine = white_engine
        self._black_name = black_name
        self._white_name = white_name
        self._detail = detail
        self._writers: list[UsiTranscriptWriter] = []
        self._cleanups: list[Callable[[], None]] = []

    async def __aenter__(self) -> GameUsiTranscriptContext:
        self._register("black", self._black_engine, self._black_name)
        self._register("white", self._white_engine, self._white_name)
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        await asyncio.gather(
            self._black_engine.flush_io_log_handlers(),
            self._white_engine.flush_io_log_handlers(),
            return_exceptions=True,
        )
        for cleanup in self._cleanups:
            cleanup()
        for writer in self._writers:
            writer.close()

    def _register(self, role: TranscriptRole, engine: UsiTranscriptEnginePort, engine_name: str) -> None:
        writer = UsiTranscriptWriter(
            path=transcript_path_for_game(self._run_dir, game_id=self._game_id, role=role),
            game_id=self._game_id,
            role=role,
            engine_name=engine_name,
            initial_sfen=self._initial_sfen,
            detail=self._detail,
        )
        self._writers.append(writer)
        self._cleanups.append(engine.register_io_log_handler(writer.handle))


def _coerce_ts_ms(value: object) -> int:
    if isinstance(value, int | float):
        return int(value)
    return 0


def _is_final_info_candidate(line: str) -> bool:
    tokens = set(line.split())
    return bool(tokens - {"info"}) and not bool(tokens & _INFO_BOUND_TOKENS)


def _is_search_terminal_line(line: str) -> bool:
    return line.startswith(("bestmove", "checkmate", "nomate", "timeout"))


__all__ = [
    "GameUsiTranscriptContext",
    "TranscriptDetail",
    "UsiTranscriptWriter",
]
