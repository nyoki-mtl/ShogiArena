"""Contract for replaying a folded game on a real shogi board.

Replay is what turns a log into an audited game: every move is applied to a board
that rejects illegal input, so a run that replays cleanly is evidence the bridge
and this reader agree. The port keeps the board library out of the domain layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ReplayedPly:
    """One applied move: how it reads, and the position it produced."""

    ply: int
    usi: str
    ki2: str | None
    csa: str | None
    sfen_before: str
    sfen_after: str
    move_value: int


@dataclass(frozen=True)
class ReplayFailure:
    """Where replay stopped and why. Never guessed past."""

    ply: int
    usi: str
    reason: str


@dataclass(frozen=True)
class ReplayResult:
    """Plies applied before the run stopped, and the failure if it did."""

    plies: tuple[ReplayedPly, ...]
    failure: ReplayFailure | None = None

    @property
    def is_complete(self) -> bool:
        return self.failure is None

    @property
    def final_sfen(self) -> str | None:
        return self.plies[-1].sfen_after if self.plies else None

    @property
    def ki2_moves(self) -> tuple[str, ...]:
        return tuple(entry.ki2 or entry.usi for entry in self.plies)

    @property
    def csa_moves(self) -> tuple[str, ...]:
        return tuple(entry.csa or "" for entry in self.plies)


class BoardReplayPort(Protocol):
    """Applies USI moves from an initial SFEN, stopping at the first illegal one."""

    def replay(self, initial_sfen: str, usi_moves: Sequence[str]) -> ReplayResult: ...


__all__ = [
    "BoardReplayPort",
    "ReplayFailure",
    "ReplayResult",
    "ReplayedPly",
]
