"""Per-game USI transcript artifact writing."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Literal, Protocol

from shogiarena._core.shared.kernel.engine_io import UsiIoEvent
from shogiarena._core.shared.kernel.usi_transcript_contract import (
    TranscriptRole,
    transcript_path_for_game,
)

TranscriptDetail = Literal["commands", "commands_and_info"]

DEFAULT_TRANSCRIPT_MAX_BYTES = 8 * 1024 * 1024

_INFO_BOUND_TOKENS = {"lowerbound", "upperbound"}


class UsiTranscriptEnginePort(Protocol):
    """USI transcript writer が必要とする engine runtime 契約。"""

    # 読み取り専用（実装側は @property / frozen dataclass）。可変属性宣言だと
    # 書き込み可能性を要求してしまい protocol 適合しない。
    @property
    def name(self) -> str: ...

    def register_io_log_handler(
        self,
        handler: Callable[[UsiIoEvent], Awaitable[None] | None],
    ) -> Callable[[], None]: ...

    async def flush_io_log_handlers(self, *, timeout: float | None = None) -> None: ...


def _truncation_marker(role: TranscriptRole, max_bytes: int) -> bytes:
    return f"{0:010d}ms marker {role} truncated max_bytes={max_bytes}\n".encode()


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
        max_bytes: int = DEFAULT_TRANSCRIPT_MAX_BYTES,
    ) -> None:
        self._path = path
        self._role = role
        self._detail = detail
        # 上限が打ち切りマーカーより小さいと「上限に達したこと」自体を書けず、
        # サイレントな打ち切りになる。マーカーが必ず収まる下限まで引き上げる。
        # マーカー本文が上限値を含むため、引き上げで桁が増える分をもう一度見る。
        resolved_max_bytes = max(max_bytes, len(_truncation_marker(role, max_bytes)))
        self._max_bytes = max(resolved_max_bytes, len(_truncation_marker(role, resolved_max_bytes)))
        self._bytes_written = 0
        self._is_truncated = False
        self._lock = threading.Lock()
        self._start_ts_ms: int | None = None
        self._pending_final_info: str | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: BinaryIO = path.open("wb")
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

    def handle(self, entry: UsiIoEvent) -> None:
        """USI I/O log entry を transcript に反映する。"""

        line_value = entry.line
        if not line_value:
            return
        line = line_value.strip()
        if not line:
            return

        direction = entry.direction
        state = entry.phase or "-"
        timestamp = _coerce_ts_ms(entry.timestamp_ms)
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
            if not self._is_truncated:
                self._write_marker_locked("close", reserve_truncation_marker=False)
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
        self._write_locked("# ShogiArena USI transcript\n")
        self._write_locked(f"# game_id: {game_id}\n")
        self._write_locked(f"# role: {role}\n")
        self._write_locked(f"# engine: {engine_name}\n")
        self._write_locked(f"# initial_sfen: {initial_sfen}\n")
        self._write_locked(f"# detail: {detail}\n")
        self._write_locked(f"# max_bytes: {self._max_bytes}\n")
        self._write_marker_locked("start")

    def _format_line(self, *, timestamp_ms: int, direction: str, state: str, line: str) -> str:
        if self._start_ts_ms is None:
            self._start_ts_ms = timestamp_ms
        elapsed_ms = max(0, timestamp_ms - self._start_ts_ms)
        return f"{elapsed_ms:010d}ms {direction} {state} {line}\n"

    def _write_marker_locked(self, marker: str, *, reserve_truncation_marker: bool = True) -> None:
        self._write_locked(
            f"{0:010d}ms marker {self._role} {marker}\n",
            reserve_truncation_marker=reserve_truncation_marker,
        )

    def _write_locked(self, line: str, *, reserve_truncation_marker: bool = True) -> None:
        if self._is_truncated:
            return
        encoded = line.encode("utf-8")
        marker = _truncation_marker(self._role, self._max_bytes)
        reserve = len(marker) if reserve_truncation_marker else 0
        if self._bytes_written + len(encoded) + reserve > self._max_bytes:
            remaining = self._max_bytes - self._bytes_written
            if remaining >= len(marker):
                self._handle.write(marker)
                self._bytes_written += len(marker)
            self._is_truncated = True
            self._handle.flush()
            return
        self._handle.write(encoded)
        self._bytes_written += len(encoded)
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
        max_bytes: int = DEFAULT_TRANSCRIPT_MAX_BYTES,
    ) -> None:
        self._run_dir = run_dir
        self._game_id = game_id
        self._initial_sfen = initial_sfen
        self._black_engine = black_engine
        self._white_engine = white_engine
        self._black_name = black_name
        self._white_name = white_name
        self._detail = detail
        self._max_bytes = max_bytes
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
            max_bytes=self._max_bytes,
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
    "DEFAULT_TRANSCRIPT_MAX_BYTES",
    "TranscriptDetail",
    "UsiTranscriptWriter",
]
