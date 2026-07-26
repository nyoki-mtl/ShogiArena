"""Structured representation of USI ``go`` commands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from rsshogi.core import Move

from shogiarena._core.shared.kernel.time_control import TimeControlLimitsPort


@dataclass(slots=True, kw_only=True)
class PonderHitTimings:
    """Timings payload used when issuing ``ponderhit``."""

    btime: int | None = None
    wtime: int | None = None
    byoyomi: int | None = None
    binc: int | None = None
    winc: int | None = None

    def to_command_suffix(self) -> str:
        parts: list[str] = []

        def append(name: str, value: int | None) -> None:
            if value is None:
                return
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
            parts.extend([name, str(int(value))])

        append("btime", self.btime)
        append("wtime", self.wtime)
        append("binc", self.binc)
        append("winc", self.winc)
        append("byoyomi", self.byoyomi)
        return "" if not parts else " " + " ".join(parts)


@runtime_checkable
class UsiThinkPVPort(Protocol):
    eval: int | None
    depth: int | None
    seldepth: int | None
    nodes: int | None
    time: int | None


@runtime_checkable
class UsiThinkResultPort(Protocol):
    bestmove: Move | None
    ponder: Move | None
    pvs: Sequence[UsiThinkPVPort]

    def get_last_pv(self, multipv_index: int = 1) -> UsiThinkPVPort | None: ...


@runtime_checkable
class ObservedBestmovePort(Protocol):
    """``bestmove`` の観測時刻を運ぶ think result の追加 capability（task 0052）。

    値は engine の出力行を読んだ直後の monotonic 時刻で、**実到着時刻の上界** として扱う。
    提供しない実装では timeout attribution が ``unknown`` へ倒す。
    """

    observed_at_s: float | None
    observation_basis: str | None


@runtime_checkable
class BestmoveObservationCapabilityPort(Protocol):
    """``bestmove`` を観測できなかった場合にも delivery coverage を問い合わせる contract。"""

    def bestmove_observation_basis(self) -> str | None: ...


@dataclass(slots=True, kw_only=True)
class UsiThinkRequest:
    """Structured parameters for a USI ``go`` command."""

    movetime: int | None = None
    btime: int | None = None
    wtime: int | None = None
    binc: int | None = None
    winc: int | None = None
    byoyomi: int | None = None
    depth: int | None = None
    nodes: int | None = None
    is_infinite: bool = False
    is_ponder: bool = False
    searchmoves: tuple[Move, ...] = ()

    def __post_init__(self) -> None:
        self.searchmoves = normalize_searchmoves(self.searchmoves)
        self._validate_non_negative()

    def _validate_non_negative(self) -> None:
        def ensure_positive(name: str, value: int | None, *, should_allow_zero: bool = False) -> None:
            if value is None:
                return
            if should_allow_zero:
                if value < 0:
                    raise ValueError(f"{name} must be >= 0")
            else:
                if value <= 0:
                    raise ValueError(f"{name} must be > 0")

        ensure_positive("movetime", self.movetime)
        ensure_positive("btime", self.btime, should_allow_zero=True)
        ensure_positive("wtime", self.wtime, should_allow_zero=True)
        ensure_positive("binc", self.binc, should_allow_zero=True)
        ensure_positive("winc", self.winc, should_allow_zero=True)
        ensure_positive("byoyomi", self.byoyomi, should_allow_zero=True)
        ensure_positive("depth", self.depth)
        ensure_positive("nodes", self.nodes)

    def to_command(self) -> str:
        parts: list[str] = ["go"]
        if self.is_ponder:
            parts.append("ponder")
        if self.movetime is not None:
            parts.extend(["movetime", str(int(self.movetime))])
        if self.btime is not None:
            parts.extend(["btime", str(int(self.btime))])
        if self.wtime is not None:
            parts.extend(["wtime", str(int(self.wtime))])
        if self.binc is not None:
            parts.extend(["binc", str(int(self.binc))])
        if self.winc is not None:
            parts.extend(["winc", str(int(self.winc))])
        if self.byoyomi is not None:
            parts.extend(["byoyomi", str(int(self.byoyomi))])
        if self.depth is not None:
            parts.extend(["depth", str(int(self.depth))])
        if self.nodes is not None:
            parts.extend(["nodes", str(int(self.nodes))])
        if self.is_infinite:
            parts.append("infinite")
        if self.searchmoves:
            parts.append("searchmoves")
            parts.extend(m.to_usi() for m in self.searchmoves)
        return " ".join(parts)


def normalize_searchmoves(moves: Sequence[Move] | None) -> tuple[Move, ...]:
    """Utility to normalize optional ``searchmoves`` collections."""
    if not moves:
        return ()
    return tuple(moves)


def request_from_time_controls(
    *,
    my_limits: TimeControlLimitsPort,
    enemy_limits: TimeControlLimitsPort,
    is_my_black: bool,
    my_remaining_ms: int,
    enemy_remaining_ms: int,
) -> UsiThinkRequest:
    """Build a ``UsiThinkRequest`` from two ``TimeControl`` snapshots."""

    if my_limits.fixed_time_ms:
        return UsiThinkRequest(
            movetime=my_limits.fixed_time_ms,
            depth=my_limits.depth_limit,
            nodes=my_limits.node_limit,
        )

    if my_limits.time_ms is not None and (my_limits.byoyomi_ms or 0) > 0:
        if my_remaining_ms < 0 or enemy_remaining_ms < 0:
            raise ValueError("remaining time must be >= 0")
        byoyomi_ms = int(my_limits.byoyomi_ms or 0)
        if byoyomi_ms < 0:
            raise ValueError("byoyomi must be >= 0")

        # Align with shogihome behavior:
        # btime/wtime represent "main time" and can be paired with byoyomi.
        # When a side uses Fischer-style increment, advertised main time should
        # subtract that increment to avoid double counting.
        def adjust(rem: int, inc: int | None) -> int:
            if rem < 0:
                raise ValueError("remaining time must be >= 0")
            if inc is None:
                return rem
            if inc < 0:
                raise ValueError("increment must be >= 0")
            return max(0, rem - inc)

        my_adj = adjust(my_remaining_ms, my_limits.increment_ms)
        enemy_adj = adjust(enemy_remaining_ms, enemy_limits.increment_ms)
        return UsiThinkRequest(
            btime=my_adj if is_my_black else enemy_adj,
            wtime=enemy_adj if is_my_black else my_adj,
            byoyomi=byoyomi_ms,
            depth=my_limits.depth_limit,
            nodes=my_limits.node_limit,
        )

    if my_limits.time_ms is not None:
        if my_remaining_ms < 0 or enemy_remaining_ms < 0:
            raise ValueError("remaining time must be >= 0")

        def adjust(rem: int, inc: int | None) -> int:
            if rem < 0:
                raise ValueError("remaining time must be >= 0")
            if inc is None:
                return rem
            if inc < 0:
                raise ValueError("increment must be >= 0")
            return max(0, rem - inc)

        def norm_increment(value: int | None) -> int | None:
            if value is None:
                return None
            if value < 0:
                raise ValueError("increment must be >= 0")
            if value == 0:
                return None
            return value

        my_adj = adjust(my_remaining_ms, my_limits.increment_ms)
        enemy_adj = adjust(enemy_remaining_ms, enemy_limits.increment_ms)
        my_inc = norm_increment(my_limits.increment_ms)
        enemy_inc = norm_increment(enemy_limits.increment_ms)

        if is_my_black:
            return UsiThinkRequest(
                btime=my_adj,
                wtime=enemy_adj,
                binc=my_inc,
                winc=enemy_inc,
                depth=my_limits.depth_limit,
                nodes=my_limits.node_limit,
            )
        return UsiThinkRequest(
            btime=enemy_adj,
            wtime=my_adj,
            binc=enemy_inc,
            winc=my_inc,
            depth=my_limits.depth_limit,
            nodes=my_limits.node_limit,
        )

    if my_limits.depth_limit is not None or my_limits.node_limit is not None:
        return UsiThinkRequest(
            depth=my_limits.depth_limit,
            nodes=my_limits.node_limit,
        )

    raise ValueError("Unsupported TimeControlLimits for USI go command")


__all__ = [
    "PonderHitTimings",
    "UsiThinkPVPort",
    "UsiThinkRequest",
    "UsiThinkResultPort",
    "normalize_searchmoves",
    "request_from_time_controls",
]
