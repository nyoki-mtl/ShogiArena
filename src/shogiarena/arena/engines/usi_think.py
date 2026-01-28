"""Structured representation of USI ``go`` commands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shogiarena.arena.engines.time_control import TimeControlLimits


@dataclass(slots=True)
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
    infinite: bool = False
    ponder: bool = False
    searchmoves: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.searchmoves = normalize_searchmoves(self.searchmoves)
        self._validate_non_negative()

    def _validate_non_negative(self) -> None:
        def ensure_positive(name: str, value: int | None, *, allow_zero: bool = False) -> None:
            if value is None:
                return
            if allow_zero:
                if value < 0:
                    raise ValueError(f"{name} must be >= 0")
            else:
                if value <= 0:
                    raise ValueError(f"{name} must be > 0")

        ensure_positive("movetime", self.movetime)
        ensure_positive("btime", self.btime, allow_zero=True)
        ensure_positive("wtime", self.wtime, allow_zero=True)
        ensure_positive("binc", self.binc, allow_zero=True)
        ensure_positive("winc", self.winc, allow_zero=True)
        ensure_positive("byoyomi", self.byoyomi, allow_zero=True)

    def to_command(self) -> str:
        parts: list[str] = ["go"]
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
        if self.infinite:
            parts.append("infinite")
        if self.ponder:
            parts.append("ponder")
        if self.searchmoves:
            parts.append("searchmoves")
            parts.extend(self.searchmoves)
        return " ".join(parts)

    def with_limits(self, *, depth: int | None = None, nodes: int | None = None) -> UsiThinkRequest:
        new_depth = depth if depth is not None else self.depth
        new_nodes = nodes if nodes is not None else self.nodes
        return replace(self, depth=new_depth, nodes=new_nodes)


def normalize_searchmoves(moves: Sequence[str] | None) -> tuple[str, ...]:
    """Utility to normalize optional ``searchmoves`` collections."""
    if not moves:
        return ()
    return tuple(str(m).strip() for m in moves if str(m).strip())


def request_from_time_controls(
    *,
    my_limits: TimeControlLimits,
    enemy_limits: TimeControlLimits,
    my_is_black: bool,
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
        return UsiThinkRequest(
            btime=my_remaining_ms if my_is_black else enemy_remaining_ms,
            wtime=enemy_remaining_ms if my_is_black else my_remaining_ms,
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
            return value or None

        my_adj = adjust(my_remaining_ms, my_limits.increment_ms)
        enemy_adj = adjust(enemy_remaining_ms, enemy_limits.increment_ms)
        my_inc = norm_increment(my_limits.increment_ms)
        enemy_inc = norm_increment(enemy_limits.increment_ms)

        if my_is_black:
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
