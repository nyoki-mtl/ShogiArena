"""Type stubs for cshogi.usi module - USI protocol interface

This file provides type hints for the USI (Universal Shogi Interface) module.
"""

from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from cshogi import Move

InfoDict = dict[str, int | str | list[int] | list[str]]
ScoreValue = int | str  # int for centipawn, "mate" for mate scores

class Engine:
    """USI protocol engine interface"""

    def __init__(
        self,
        cmd: str | list[str],
        *,
        connect: bool = True,
        debug: bool = False,
        options: dict[str, Any] | None = None,
        cwd: str | Path | None = None,
    ) -> None: ...
    def connect(self) -> None: ...
    def quit(self) -> None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def is_alive(self) -> bool: ...
    def usi(self) -> dict[str, Any]: ...
    def isready(self) -> None: ...
    def usinewgame(self) -> None: ...
    def setoption(self, name: str, value: str | int | bool) -> None: ...
    def position(
        self, sfen: str | None = None, moves: list[str | Move] | None = None, startpos: bool = False
    ) -> None: ...
    def go(
        self,
        *,
        ponder: bool = False,
        btime: int | None = None,
        wtime: int | None = None,
        binc: int | None = None,
        winc: int | None = None,
        byoyomi: int | None = None,
        depth: int | None = None,
        nodes: int | None = None,
        mate: int | None = None,
        movetime: int | None = None,
        infinite: bool = False,
    ) -> tuple[str | None, str | None]: ...
    def go_async(
        self,
        *,
        ponder: bool = False,
        btime: int | None = None,
        wtime: int | None = None,
        binc: int | None = None,
        winc: int | None = None,
        byoyomi: int | None = None,
        depth: int | None = None,
        nodes: int | None = None,
        mate: int | None = None,
        movetime: int | None = None,
        infinite: bool = False,
        on_info: Callable[[InfoDict], None] | None = None,
    ) -> Future[tuple[str | None, str | None]]: ...
    def stop(self) -> None: ...
    def ponderhit(self) -> None: ...
    def gameover(self, result: str) -> None: ...
    @property
    def name(self) -> str: ...
    @property
    def author(self) -> str: ...
    @property
    def options(self) -> dict[str, dict[str, Any]]: ...
    def __enter__(self) -> Engine: ...
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None: ...

# Async engine interface
class AsyncEngine:
    """Asynchronous USI protocol engine interface"""

    @classmethod
    async def create(
        cls,
        cmd: str | list[str],
        *,
        debug: bool = False,
        options: dict[str, Any] | None = None,
        cwd: str | Path | None = None,
    ) -> AsyncEngine: ...
    async def quit(self) -> None: ...
    async def terminate(self) -> None: ...
    async def kill(self) -> None: ...
    async def usi(self) -> dict[str, Any]: ...
    async def isready(self) -> None: ...
    async def usinewgame(self) -> None: ...
    async def setoption(self, name: str, value: str | int | bool) -> None: ...
    async def position(
        self, sfen: str | None = None, moves: list[str | Move] | None = None, startpos: bool = False
    ) -> None: ...
    async def go(
        self,
        *,
        ponder: bool = False,
        btime: int | None = None,
        wtime: int | None = None,
        binc: int | None = None,
        winc: int | None = None,
        byoyomi: int | None = None,
        depth: int | None = None,
        nodes: int | None = None,
        mate: int | None = None,
        movetime: int | None = None,
        infinite: bool = False,
        on_info: Callable[[InfoDict], None] | None = None,
    ) -> tuple[str | None, str | None]: ...
    async def stop(self) -> None: ...
    async def ponderhit(self) -> None: ...
    async def gameover(self, result: str) -> None: ...
    @property
    def name(self) -> str: ...
    @property
    def author(self) -> str: ...
    @property
    def options(self) -> dict[str, dict[str, Any]]: ...
    async def __aenter__(self) -> AsyncEngine: ...
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None: ...

# Info dictionary keys
class InfoKeys:
    """Constants for USI info dictionary keys"""

    depth: str = "depth"
    seldepth: str = "seldepth"
    time: str = "time"
    nodes: str = "nodes"
    pv: str = "pv"
    multipv: str = "multipv"
    score_cp: str = "score_cp"
    score_mate: str = "score_mate"
    currmove: str = "currmove"
    currmovenumber: str = "currmovenumber"
    hashfull: str = "hashfull"
    nps: str = "nps"
    string: str = "string"

# Analysis result types
class Score:
    """USI score representation"""
    def __init__(self, cp: int | None = None, mate: int | None = None) -> None: ...
    @property
    def cp(self) -> int | None: ...
    @property
    def mate(self) -> int | None: ...
    def is_mate(self) -> bool: ...
    def to_cp(self, mate_score: int = 30000) -> int: ...
    def __str__(self) -> str: ...
    def __repr__(self) -> str: ...

class Analysis:
    """Analysis result from engine"""

    depth: int
    seldepth: int | None
    time: int
    nodes: int
    score: Score
    pv: list[str]
    multipv: int | None

    def __init__(self, info: InfoDict) -> None: ...

# Helper functions
def parse_info_string(info_string: str) -> InfoDict: ...
def format_score(score: Score) -> str: ...
def is_mate_score(cp: int, mate_score: int = 30000) -> bool: ...
